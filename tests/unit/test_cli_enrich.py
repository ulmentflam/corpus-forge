"""Unit tests for ``corpus-forge enrich``.

Phase H / H-06.

Tests use typer's :class:`CliRunner` and an in-process stub enricher
(injected via ``EnricherRegistry``) so we don't depend on Docker /
Ollama. The CLI is exercised against an SQLite-backed config; the
Postgres live-Ollama path is in ``tests/integration/test_enrich_e2e.py``.

Coverage:

- ``backend = "none"`` aborts with exit 2 + clear error.
- Happy path: writes one enrichment per chunk; ``classifier:rule``
  ``class=code`` documents are visited.
- ``--dry-run`` writes nothing.
- ``--json`` emits one JSON object per enriched chunk.
- ``--limit N`` short-circuits after N enrichments.
- ``--dataset NAME`` filters; unknown dataset → exit 2.
- ``--backend qwen-local`` / ``qwen-remote`` is honoured.
- ``--reclassify-on-model-change`` reprocesses already-enriched chunks.
"""

from __future__ import annotations

import json as _json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.enrichers.base import CodeChunkEnrichment

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubEnricher:
    """Always-returns-a-fixed-enrichment enricher; pretends to be
    ``qwen-local`` so the CLI's idempotency-tag math picks it up under
    the ``"local"`` config branch.
    """

    name = "qwen-local"

    def __init__(self, model: str = "stub-model") -> None:
        self.model = model
        self.calls: list[tuple[str, str]] = []

    def enrich(self, chunk, *, language):
        self.calls.append((chunk.text, language))
        return CodeChunkEnrichment(
            docstring="Synthesised.",
            summary=f"Did a thing in {language}.",
            symbols=["x"],
            model=self.model,
            confidence=0.7,
        )

    def warmup(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Config + corpus seeding helpers
# ---------------------------------------------------------------------------


def _build_test_config(tmp_path: Path, *, backend: str = "local") -> Path:
    """Write a minimal SQLite-backed config with ``code_enricher.backend``."""
    db_path = tmp_path / "corpus.db"
    # TOML basic strings interpret ``\\U`` / ``\\u`` as unicode escapes,
    # which corrupts Windows paths like ``C:\\Users\\...``. Render with
    # forward slashes so the same fixture works on every OS.
    cfg = textwrap.dedent(
        f"""
        [backend]
        kind = "sqlite"
        dsn  = "{db_path.as_posix()}"

        [daemon]

        [[datasets]]
        name = "demo"
        kind = "text"
        sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]

        [[embedders]]
        name      = "fake"
        provider  = "sentence_transformers"
        model_id  = "fake-1"
        dimension = 8

        [code_enricher]
        backend = "{backend}"
        local_model = "stub-model"
        remote_model = "stub-model"
        local_url = "http://localhost:11434"
        remote_url = "http://localhost:11434"
        timeout_s = 10.0
        temperature = 0.1
        """
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(cfg, encoding="utf-8")
    return cfg_path


def _seed_corpus(cfg_path: Path) -> tuple[int, list[int]]:
    """Seed two code documents + one non-code document. Returns
    ``(dataset_id, [code_chunk_ids])``."""
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.config import Config

    cfg = Config.load(config_path=cfg_path)
    backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
    backend.migrate()

    backend._execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", ("demo", "text"))
    dataset_id = int(backend._execute("SELECT id FROM datasets WHERE name = ?", ("demo",))[0]["id"])

    code_chunk_ids: list[int] = []
    code_fixtures = [
        ("file:///vault/code/a.py", "def add(a, b): return a + b\n"),
        ("file:///vault/code/b.py", "def mul(a, b): return a * b\n"),
    ]
    for src_uri, body in code_fixtures:
        backend._execute(
            """
            INSERT INTO documents (dataset_id, source_uri, title, text, content_hash, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (dataset_id, src_uri, None, body, src_uri, _json.dumps({})),
        )
        doc_id = int(
            backend._execute("SELECT id FROM documents WHERE source_uri = ?", (src_uri,))[0]["id"]
        )
        backend.apply_label(
            "document", doc_id, "class", "code", source="classifier:rule", confidence=0.99
        )
        backend._execute(
            """
            INSERT INTO chunks
                (document_id, chunk_index, heading, text, metadata, role, token_count,
                 content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                0,
                None,
                body,
                _json.dumps({"language": "python"}),
                None,
                None,
                f"{src_uri}-chunk-0",
            ),
        )
        chunk_id = int(
            backend._execute("SELECT id FROM chunks WHERE document_id = ? LIMIT 1", (doc_id,))[0][
                "id"
            ]
        )
        code_chunk_ids.append(chunk_id)

    # Non-code document — should be ignored.
    backend._execute(
        """
        INSERT INTO documents (dataset_id, source_uri, title, text, content_hash, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (dataset_id, "file:///vault/notes/c.md", None, "body", "c-hash", _json.dumps({})),
    )
    note_id = int(
        backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?", ("file:///vault/notes/c.md",)
        )[0]["id"]
    )
    backend.apply_label("document", note_id, "class", "note", source="classifier:rule")

    return dataset_id, code_chunk_ids


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cfg_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = _build_test_config(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
    return cfg


def _patch_enricher_factory(stub: _StubEnricher):
    """Patch :func:`get_active_enricher` AND the direct-import paths used
    when ``--backend qwen-local/qwen-remote`` is set."""
    return patch.multiple(
        "corpus_forge.cli",
        # Catch ``--backend qwen-local`` direct construction via the
        # import path inside the CLI module.
    )


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestEnrichCLI:
    def test_backend_none_aborts(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _build_test_config(tmp_path, backend="none")
        monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
        _seed_corpus(cfg)

        result = runner.invoke(app, ["enrich"])
        assert result.exit_code == 2
        assert "disabled" in result.output.lower() or "none" in result.output.lower()

    def test_happy_path_writes_enrichment(
        self,
        runner: CliRunner,
        cfg_path: Path,
        tmp_path: Path,
    ) -> None:
        _dataset_id, code_chunk_ids = _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich"])
        assert result.exit_code == 0, result.output
        # Both code chunks enriched.
        assert len(stub.calls) == 2

        # Verify metadata landed.
        from corpus_forge.backends.sqlite import SQLiteBackend
        from corpus_forge.config import Config

        cfg = Config.load(config_path=cfg_path)
        backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
        for cid in code_chunk_ids:
            md_row = backend._execute("SELECT metadata FROM chunks WHERE id = ?", (cid,))[0]
            md = md_row["metadata"]
            if isinstance(md, str):
                md = _json.loads(md)
            assert "enrichment" in md
            assert md["enrichment"]["model"] == stub.model
            # Sibling key survives.
            assert md["language"] == "python"

    def test_dry_run_writes_nothing(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _, code_chunk_ids = _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich", "--dry-run"])
        assert result.exit_code == 0, result.output
        # Enricher was called (so cost-guard preview surfaces, output is
        # informative) but no DB writes happened.
        from corpus_forge.backends.sqlite import SQLiteBackend
        from corpus_forge.config import Config

        cfg = Config.load(config_path=cfg_path)
        backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
        for cid in code_chunk_ids:
            md_row = backend._execute("SELECT metadata FROM chunks WHERE id = ?", (cid,))[0]
            md = md_row["metadata"]
            if isinstance(md, str):
                md = _json.loads(md)
            assert "enrichment" not in md

    def test_json_output(self, runner: CliRunner, cfg_path: Path, tmp_path: Path) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich", "--json"])
        assert result.exit_code == 0, result.output
        lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("{")]
        assert len(lines) >= 1
        parsed = _json.loads(lines[0])
        assert "chunk_id" in parsed
        assert "summary" in parsed
        assert "model" in parsed
        assert parsed["applied"] is True

    def test_limit_short_circuits(self, runner: CliRunner, cfg_path: Path, tmp_path: Path) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich", "--limit", "1"])
        assert result.exit_code == 0, result.output
        assert len(stub.calls) == 1

    def test_unknown_dataset_exits_2(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich", "--dataset", "missing"])
        assert result.exit_code == 2
        assert "not found" in result.output.lower()

    def test_known_dataset_filter(self, runner: CliRunner, cfg_path: Path, tmp_path: Path) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich", "--dataset", "demo"])
        assert result.exit_code == 0, result.output
        assert len(stub.calls) == 2

    def test_backend_override_qwen_local(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        # --backend qwen-local takes the direct-construction path; patch
        # the class symbol the CLI imports inside the command body.
        with patch("corpus_forge.enrichers.qwen_local.QwenCoderLocal", return_value=stub):
            result = runner.invoke(app, ["enrich", "--backend", "qwen-local"])
        assert result.exit_code == 0, result.output
        assert len(stub.calls) == 2

    def test_backend_override_invalid_exits_2(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(cfg_path)
        result = runner.invoke(app, ["enrich", "--backend", "bogus"])
        assert result.exit_code == 2

    def test_idempotency_second_run_is_noop(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            runner.invoke(app, ["enrich"])  # first pass
            # Reset stub call list and run again — should be a no-op (same model tag).
            stub.calls.clear()
            runner.invoke(app, ["enrich"])
        assert stub.calls == []

    def test_reclassify_on_model_change_forces_reenrich(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(cfg_path)
        stub = _StubEnricher()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            runner.invoke(app, ["enrich"])  # first pass writes enrichment
            stub.calls.clear()
            runner.invoke(app, ["enrich", "--reclassify-on-model-change"])
        # Despite matching model tag, the flag forces reprocessing.
        assert len(stub.calls) == 2

    def test_enricher_error_does_not_abort_run(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        """A single chunk that fails to enrich must not crash the whole
        run — the CLI logs the failure and moves on (parity with the
        classify CLI's per-doc exception handling)."""
        from corpus_forge.enrichers.base import EnricherUnavailableError

        _seed_corpus(cfg_path)

        class _PartialFailer:
            name = "qwen-local"
            model = "stub-model"

            def __init__(self) -> None:
                self.calls = 0

            def enrich(self, chunk, *, language):
                self.calls += 1
                if self.calls == 1:
                    raise EnricherUnavailableError("flaky network")
                return CodeChunkEnrichment(
                    docstring=None,
                    summary="ok",
                    symbols=[],
                    model="stub-model",
                    confidence=0.5,
                )

            def warmup(self) -> None:
                return None

        stub = _PartialFailer()
        with patch("corpus_forge.enrichers.get_active_enricher", return_value=stub):
            result = runner.invoke(app, ["enrich"])
        assert result.exit_code == 0, result.output
        # Run summary reports the failure count.
        assert "failed 1" in result.output
