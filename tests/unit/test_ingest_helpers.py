"""Unit tests for ingest module helper functions."""

from corpus_forge.ingest import _instantiate_source
from corpus_forge.sources.claude_code import ClaudeCodeSource
from corpus_forge.sources.markdown_vault import MarkdownVaultSource
from corpus_forge.sources.opencode import OpenCodeSource


class TestGetChunkerForSource:
    """Tests for get_chunker_for_source function."""

    def test_get_markdown_chunker(self, temp_dir):
        """Test getting a markdown chunker."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()

        class MockSourceConfig:
            plugin = "markdown_vault"
            vault_root = vault_dir
            exclude_globs: list[str] = [".obsidian/**", ".trash/**", ".*"]  # noqa: RUF012
            chunker = "markdown"
            chunker_config: dict = {}  # noqa: RUF012

        class MockSource:
            root = vault_dir
            name = "markdown_vault"

        class MockConfig:
            datasets: list = [type("MockDataset", (), {"sources": [MockSourceConfig()]})()]  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert isinstance(source, MarkdownVaultSource)
        assert source.root == vault_dir

    def test_instantiate_claude_code(self, temp_dir):
        """Test instantiating a ClaudeCodeSource."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()

        class MockSourceConfig:
            plugin = "claude_code"
            projects_root = projects_dir
            include_subagents = True
            chunker = "conversation"
            chunker_config: dict = {"mode": "per_message"}  # noqa: RUF012

        class MockSource:
            root = projects_dir
            name = "claude_code"

        class MockConfig:
            datasets: list = [type("MockDataset", (), {"sources": [MockSourceConfig()]})()]  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert isinstance(source, ClaudeCodeSource)
        assert source.root == projects_dir
        assert source.include_subagents is True

    def test_instantiate_opencode(self, temp_dir):
        """Test instantiating an OpenCodeSource."""
        storage_dir = temp_dir / "storage"
        storage_dir.mkdir()

        class MockSourceConfig:
            plugin = "opencode"
            storage_root = storage_dir
            chunker = "conversation"
            chunker_config: dict = {"mode": "sliding_window"}  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert isinstance(source, OpenCodeSource)
        assert source.root == storage_dir


# ─────────────────────────────────────────────────────────────────────────
# Wall-clock planner + error classifier (added with the live-ETA fix)
# ─────────────────────────────────────────────────────────────────────────


class TestPlanIngest:
    """Tests for ``_plan_ingest`` — the up-front walk that drives both
    the ETA log line and the per-source progress-bar totals."""

    def test_returns_per_source_file_counts(self, temp_dir):
        """Every source with a recognisable filesystem root must show
        up in the returned mapping; the count must match what the
        planner walked."""
        import textwrap

        from corpus_forge.config import Config
        from corpus_forge.ingest import _plan_ingest

        vault = temp_dir / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("x" * 4096, encoding="utf-8")
        (vault / "b.md").write_text("y" * 4096, encoding="utf-8")

        cfg_path = temp_dir / "config.toml"
        cfg_path.write_text(
            textwrap.dedent(
                f"""
                [backend]
                kind = "sqlite"
                dsn  = "{(temp_dir / "corpus.db").as_posix()}"

                [daemon]

                [[datasets]]
                name = "demo"
                kind = "text"
                sources = [
                  {{plugin = "filesystem", root = "{vault.as_posix()}", chunker = "markdown"}}
                ]

                [[embedders]]
                name      = "fake"
                provider  = "sentence_transformers"
                model_id  = "fake-1"
                dimension = 384
                """
            ),
            encoding="utf-8",
        )
        config = Config.load(config_path=cfg_path)
        totals = _plan_ingest(config)
        # One source → one entry; count covers both .md files.
        assert len(totals) == 1
        assert list(totals.values()) == [2]

    def test_returns_empty_dict_when_no_filesystem_roots(self):
        """A config with only API-driven sources returns ``{}`` so the
        ingest loop falls back to unbounded progress bars."""

        class _StubSourceCfg:
            plugin = "api_only"

        class _StubDataset:
            sources: list = [_StubSourceCfg()]  # noqa: RUF012

        class _StubConfig:
            datasets: list = [_StubDataset()]  # noqa: RUF012

        from corpus_forge.ingest import _plan_ingest

        assert _plan_ingest(_StubConfig()) == {}


class TestClassifyIngestError:
    """Tests for ``_classify_and_log_ingest_error`` — the per-document
    failure logger that distinguishes embedder NaN / 5xx from real
    extractor failures."""

    def test_nan_message_logs_at_warning_with_hint(self, caplog):
        """The Ollama 500-with-NaN error must surface at WARNING with
        a model-selection hint, NOT as a generic 'Extractor failed'."""
        import logging

        from corpus_forge.ingest import _classify_and_log_ingest_error

        class _Raw:
            source_uri = "filesystem://Workspace/M1/HYBRID_PACK.md"

        with caplog.at_level(logging.WARNING, logger="corpus_forge.ingest"):
            _classify_and_log_ingest_error(
                _Raw(),
                RuntimeError(
                    "Error code: 500 - {'error': {'message': "
                    "'failed to encode response: json: unsupported value: NaN'}}"
                ),
            )
        records = [r for r in caplog.records if "NaN" in r.getMessage()]
        assert records, "expected a NaN warning to be logged"
        assert records[0].levelno == logging.WARNING
        assert "try a different embedder" in records[0].getMessage()

    def test_generic_failure_keeps_extractor_taxonomy(self, caplog):
        """Non-embedder failures must continue logging as 'Extractor
        failed on X' so existing grep / dashboards still match."""
        import logging

        from corpus_forge.ingest import _classify_and_log_ingest_error

        class _Raw:
            source_uri = "filesystem://Workspace/notes/broken.pdf"

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.extract"):
            _classify_and_log_ingest_error(_Raw(), ValueError("PDF parse error"))
        records = [r for r in caplog.records if "Extractor failed" in r.getMessage()]
        assert records, "expected an extractor-failure line on a generic exception"


class TestProgressAdvanceOnFailure:
    """Regression test: the per-source and global progress bars must
    advance on EVERY iteration of the ingest loop, not just successes.

    The planner's ``_plan_ingest`` totals come from ``estimate_sync``
    which counts every file regardless of whether ingest will succeed.
    If we only advanced on success, a single Ollama-NaN 5xx (or any
    other recoverable per-file failure) would strand the global bar
    permanently below 100% — exactly the misleading state the global
    bar was added to eliminate.
    """

    def test_loop_body_uses_finally_for_progress_update(self):
        """``ingest_once`` must call ``progress.update`` inside a
        ``finally`` block so failed items still count toward the bars.

        Implemented as a source-text inspection rather than a full
        end-to-end run because exercising the real ``Progress`` instance
        under failure conditions requires significant fake-backend +
        fake-source plumbing. The source-text check is precise enough
        to lock the contract: the progress-update lines must follow the
        ``finally:`` keyword, not the closing of the ``except`` block.
        """
        import inspect
        import textwrap

        from corpus_forge import ingest

        src = inspect.getsource(ingest.ingest_once)
        # The two advance calls must live inside the ``finally`` block.
        # Walk every ``finally:`` block and check that both updates
        # appear before the next dedent.
        dedented = textwrap.dedent(src)
        # Quick structural check: both ``progress.update(<task>,
        # advance=1)`` calls appear AFTER a ``finally:`` keyword and
        # BEFORE the next non-indented sibling statement.
        assert "finally:" in dedented, "ingest_once must use try/finally for progress updates"
        finally_idx = dedented.index("finally:")
        tail = dedented[finally_idx:]
        # Both updates must appear inside this finally block (before
        # the next ``for`` / function-level boundary).
        next_for = tail.find("\n            for ")
        next_func = tail.find("\ndef ")
        end = min(idx for idx in (next_for, next_func, len(tail)) if idx > 0)
        finally_block = tail[:end]
        assert "progress.update(source_task, advance=1)" in finally_block, (
            "source-task advance must live inside the ingest finally block"
        )
        assert "progress.update(global_task, advance=1)" in finally_block, (
            "global-task advance must live inside the ingest finally block"
        )
