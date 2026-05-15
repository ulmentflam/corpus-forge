"""Unit tests for `corpus-forge classify`.

Phase E / Wave 1 — C-05.

Tests use typer's :class:`CliRunner` and a temp SQLite-backed config so
we don't need Docker; the Postgres end-to-end test (C-08) covers the
production path. We exercise the flag surface:

- ``--dry-run`` writes no rows.
- ``--json`` emits one JSON object per document line.
- ``--limit N`` short-circuits after N applies.
- ``--reclassify`` forces include_classified=True.
- ``--dataset`` filter targets a single dataset.
- ``--classifier`` bypasses the chain.
"""

from __future__ import annotations

import json as _json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app


def _build_test_config(tmp_path: Path) -> Path:
    """Write a minimal SQLite-backed config; return its path.

    Caller is expected to set ``CORPUS_FORGE_CONFIG`` so
    :meth:`Config.load` finds it.
    """
    db_path = tmp_path / "corpus.db"
    cfg = textwrap.dedent(
        f"""
        [backend]
        kind = "sqlite"
        dsn  = "{db_path}"

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

        [classifier]
        chain = ["rule"]
        escalation_threshold = 0.4
        """
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(cfg, encoding="utf-8")
    return cfg_path


def _seed_corpus(tmp_path: Path, cfg_path: Path) -> tuple[int, list[int]]:
    """Migrate the SQLite db pointed at by ``cfg_path`` and seed three docs."""
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.config import Config

    cfg = Config.load(config_path=cfg_path)
    backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
    backend.migrate()

    backend._execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", ("demo", "text"))
    dataset_id = int(backend._execute("SELECT id FROM datasets WHERE name = ?", ("demo",))[0]["id"])

    import json as _json2

    doc_ids: list[int] = []
    fixtures = [
        ("file:///vault/notes/a.md", "Note A", "todo", [("format", "markdown")]),
        ("file:///vault/blog/post.md", "Post", "body", [("format", "markdown")]),
        ("file:///vault/code/x.py", None, "def f(): pass", [("format", "code")]),
    ]
    for src_uri, title, body, labels in fixtures:
        backend._execute(
            """
            INSERT INTO documents (dataset_id, source_uri, title, text, content_hash, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (dataset_id, src_uri, title, body, src_uri, _json2.dumps({})),
        )
        d_id = int(
            backend._execute("SELECT id FROM documents WHERE source_uri = ?", (src_uri,))[0]["id"]
        )
        doc_ids.append(d_id)
        for ns, val in labels:
            backend.apply_label("document", d_id, ns, val, source="extractor")
    return dataset_id, doc_ids


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cfg_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = _build_test_config(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
    return cfg


class TestClassifyCLI:
    def test_dry_run_writes_nothing(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _dataset_id, _doc_ids = _seed_corpus(tmp_path, cfg_path)

        result = runner.invoke(app, ["classify", "--dry-run"])
        assert result.exit_code == 0, result.output

        # No class labels written.
        from corpus_forge.backends.sqlite import SQLiteBackend
        from corpus_forge.config import Config

        cfg = Config.load(config_path=cfg_path)
        backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
        rows = backend._execute(
            """
            SELECT dl.document_id
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE l.namespace = ?
            """,
            ("class",),
        )
        assert rows == []

    def test_default_writes_one_class_label_per_doc(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _dataset_id, _doc_ids = _seed_corpus(tmp_path, cfg_path)

        result = runner.invoke(app, ["classify"])
        assert result.exit_code == 0, result.output

        from corpus_forge.backends.sqlite import SQLiteBackend
        from corpus_forge.config import Config

        cfg = Config.load(config_path=cfg_path)
        backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
        rows = backend._execute(
            """
            SELECT dl.document_id, l.value, dl.source, dl.confidence
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE l.namespace = ?
            """,
            ("class",),
        )
        # Three docs → three class rows.
        assert len(rows) == 3
        for r in rows:
            assert r["source"] == "classifier:rule"
            assert r["confidence"] is not None
            # value in the 9-enum
            assert r["value"] in (
                "code",
                "chat",
                "book",
                "textbook",
                "paper",
                "article",
                "reference",
                "note",
                "other",
            )

    def test_idempotency_second_run_is_a_noop(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _dataset_id, _ = _seed_corpus(tmp_path, cfg_path)

        runner.invoke(app, ["classify"])
        runner.invoke(app, ["classify"])  # second time — no change

        from corpus_forge.backends.sqlite import SQLiteBackend
        from corpus_forge.config import Config

        cfg = Config.load(config_path=cfg_path)
        backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
        rows = backend._execute(
            """
            SELECT COUNT(*) AS n FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE l.namespace = ?
            """,
            ("class",),
        )
        assert rows[0]["n"] == 3  # still three rows, not six

    def test_reclassify_reattaches_rows(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _dataset_id, _ = _seed_corpus(tmp_path, cfg_path)
        # First pass.
        result = runner.invoke(app, ["classify"])
        assert result.exit_code == 0, result.output

        # Re-classify — must process all docs again. We don't assert on
        # row counts (the chain writes the same source+value+namespace
        # so the unique constraint deduplicates) but the iterator must
        # consider every document.
        result = runner.invoke(app, ["classify", "--reclassify", "--json"])
        assert result.exit_code == 0, result.output
        # JSON output is one object per line — assert three lines.
        lines = [line for line in result.output.splitlines() if line.startswith("{")]
        assert len(lines) == 3

    def test_json_output_emits_one_object_per_doc(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(tmp_path, cfg_path)
        result = runner.invoke(app, ["classify", "--json", "--dry-run"])
        assert result.exit_code == 0, result.output
        lines = [line for line in result.output.splitlines() if line.startswith("{")]
        assert len(lines) == 3
        for line in lines:
            obj = _json.loads(line)
            # Phase E P1 (C-10/11): JSON output gains a ``classifier``
            # field carrying the registry's winner name so callers can
            # tell which classifier in the chain produced the label.
            assert set(obj.keys()) >= {
                "doc_id",
                "source_uri",
                "class",
                "confidence",
                "rationale",
                "applied",
                "classifier",
            }
            # dry-run → applied=False
            assert obj["applied"] is False
            # The P0 fixture corpus only hits the rule classifier
            # (chain = ["rule"] in the test config).
            assert obj["classifier"] == "rule"

    def test_limit_stops_after_n(self, runner: CliRunner, cfg_path: Path, tmp_path: Path) -> None:
        _seed_corpus(tmp_path, cfg_path)
        result = runner.invoke(app, ["classify", "--limit", "1", "--json"])
        assert result.exit_code == 0, result.output
        lines = [line for line in result.output.splitlines() if line.startswith("{")]
        assert len(lines) == 1

    def test_dataset_filter(self, runner: CliRunner, cfg_path: Path, tmp_path: Path) -> None:
        # Seed two datasets; assert --dataset demo restricts.
        _seed_corpus(tmp_path, cfg_path)
        from corpus_forge.backends.sqlite import SQLiteBackend
        from corpus_forge.config import Config

        cfg = Config.load(config_path=cfg_path)
        backend = SQLiteBackend(path=cfg.backend.dsn, schema=cfg.backend.schema)
        backend._execute(
            "INSERT INTO datasets (name, kind) VALUES (?, ?)",
            ("untouched", "text"),
        )
        untouched_id = int(
            backend._execute("SELECT id FROM datasets WHERE name = ?", ("untouched",))[0]["id"]
        )
        import json as _json2

        backend._execute(
            """
            INSERT INTO documents (dataset_id, source_uri, title, text, content_hash, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                untouched_id,
                "file:///elsewhere.md",
                None,
                "x",
                "hash",
                _json2.dumps({}),
            ),
        )

        result = runner.invoke(app, ["classify", "--dataset", "demo"])
        assert result.exit_code == 0, result.output

        # Only the three demo docs got classified.
        rows = backend._execute(
            """
            SELECT d.dataset_id, COUNT(*) AS n
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            JOIN documents d ON d.id = dl.document_id
            WHERE l.namespace = ?
            GROUP BY d.dataset_id
            """,
            ("class",),
        )
        per_ds = {int(r["dataset_id"]): int(r["n"]) for r in rows}
        # untouched dataset has zero class rows.
        assert untouched_id not in per_ds or per_ds[untouched_id] == 0

    def test_classifier_flag_filters_chain(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        """--classifier rule keeps only the rule classifier in the
        chain. Smoke-test that the flag is honoured (full chain has
        only ``rule`` at P0 anyway, but the flag must not break)."""
        _seed_corpus(tmp_path, cfg_path)
        result = runner.invoke(app, ["classify", "--classifier", "rule"])
        assert result.exit_code == 0, result.output

    def test_unknown_classifier_flag_errors(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(tmp_path, cfg_path)
        result = runner.invoke(app, ["classify", "--classifier", "noooo"])
        # Should bail cleanly (non-zero exit, helpful message).
        assert result.exit_code != 0

    def test_cost_guard_message_printed(
        self, runner: CliRunner, cfg_path: Path, tmp_path: Path
    ) -> None:
        _seed_corpus(tmp_path, cfg_path)
        result = runner.invoke(app, ["classify", "--dry-run"])
        assert result.exit_code == 0, result.output
        # Pre-flight prints a count + estimate line.
        assert "3" in result.output  # mentions document count somewhere
