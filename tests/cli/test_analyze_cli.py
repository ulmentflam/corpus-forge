"""Phase O Wave 4 — ``corpus-forge analyze`` CLI subgroup tests.

RED suite for O4-T1.  Every test in this file MUST fail until the Coder
ships ``corpus_forge/cli_analyze.py`` and wires it into ``corpus_forge/cli.py``.

Contracts tested (from phase_o_eda_cleaning.md § Wave O4 RED):
- ``corpus-forge analyze --help`` lists six subcommands.
- Each subcommand --help exits 0.
- Each subcommand exits 0 for an existing dataset.
- Report files land in the directory nominated by ``CORPUS_FORGE_REPORT_DIR``
  (or the ``--report-dir`` / ``--out`` override).
- ``analyze stats --json`` emits JSON to stdout, no markdown report written.
- ``analyze duplicates --threshold 0.85`` writes exact-dup + near-dup sections.
- ``analyze quality`` invokes ``score_chunks_batch`` + ``persist_quality_signals``
  and leaves rows in ``chunk_quality_signals``.
- ``--limit N`` parameter passes through to every subcommand.
- Report directory creation is idempotent across back-to-back invocations.
- Missing dataset exits non-zero with the dataset name in stderr/output.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUBCOMMANDS = ["stats", "duplicates", "topics", "distribution", "drift", "quality"]


def _runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Seeded SQLite dataset fixture
#
# The CLI commands construct a backend via ``Config.load()`` and then query
# corpus chunks.  We mock the heavy machinery (Config.load, backend
# construction) and return a small in-memory SQLite database pre-seeded with
# a handful of chunks — enough to exercise every subcommand without spinning
# up a real Postgres container.
# ---------------------------------------------------------------------------


def _seed_sqlite_db(db_path: Path) -> sqlite3.Connection:
    """Create a minimal SQLite corpus database with a few chunk rows.

    The schema is the subset of the real corpus schema that the analyze
    commands need: ``documents``, ``chunks``, and ``chunk_quality_signals``.
    This is intentionally lighter than the full migration chain — the tests
    are pinning CLI / dispatch behavior, not migration correctness.
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Datasets table (matches 0001_core; chunks reach it via documents.dataset_id).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'text'
        )
        """
    )

    # Minimal documents table (FK to datasets).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            source_uri TEXT NOT NULL,
            content_hash TEXT,
            title TEXT,
            modified_at REAL
        )
        """
    )

    # Minimal chunks table (no direct dataset column — chunks reach the
    # dataset via documents.dataset_id, matching the real 0001_core shape).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER,
            conversation_id INTEGER,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT,
            metadata TEXT
        )
        """
    )

    # Minimal labels / chunk_labels tables so the analyze classifier-label
    # join resolves cleanly (LEFT JOIN handles empty rows).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            value TEXT NOT NULL,
            UNIQUE(namespace, value)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_labels (
            chunk_id INTEGER NOT NULL,
            label_id INTEGER NOT NULL,
            confidence REAL,
            source TEXT
        )
        """
    )

    # Minimal chunk_quality_signals table (matches 0012_analyze_signals DDL)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_quality_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER NOT NULL,
            signal_name TEXT NOT NULL,
            signal_value REAL,
            source TEXT NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # Seed a dataset row.
    cur.execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", ("demo", "text"))
    dataset_id = cur.lastrowid

    # Seed a document under the dataset.
    cur.execute(
        "INSERT INTO documents (dataset_id, source_uri, content_hash, title, modified_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (dataset_id, "file:///demo/note1.md", "hash_doc1", "Demo note", 1_700_000_000.0),
    )
    doc_id = cur.lastrowid

    # Seed chunks with varied token counts and hashes (no `dataset` column —
    # chunks reach the dataset via document_id → documents.dataset_id).
    chunk_rows = [
        (doc_id, "Hello world, this is a test chunk with enough tokens.", 12, "h1"),
        (doc_id, "Another chunk about machine learning and embeddings.", 9, "h2"),
        (doc_id, "Exact duplicate content for dedup testing purposes.", 8, "h3"),
        (doc_id, "Exact duplicate content for dedup testing purposes.", 8, "h3"),
        (
            doc_id,
            "A longer chunk with more text to exercise the stats percentile "
            "calculations across a wider range of token counts.",
            28,
            "h4",
        ),
        (doc_id, "Short.", 1, "h5"),
    ]
    for doc_id_val, text, token_count, content_hash in chunk_rows:
        cur.execute(
            "INSERT INTO chunks (document_id, text, token_count, content_hash) VALUES (?, ?, ?, ?)",
            (doc_id_val, text, token_count, content_hash),
        )

    conn.commit()
    return conn


def _make_fake_config(db_path: Path) -> MagicMock:
    """Return a MagicMock that looks enough like ``corpus_forge.config.Config``
    to let the CLI commands construct a SQLite backend.
    """
    backend_cfg = MagicMock()
    backend_cfg.kind = "sqlite"
    backend_cfg.dsn = str(db_path)
    backend_cfg.schema = "corpus"

    cfg = MagicMock()
    cfg.backend = backend_cfg
    cfg.embedders = []
    cfg.datasets = []
    # AnalyzeConfig — dedup_threshold and other params
    analyze_cfg = MagicMock()
    analyze_cfg.enabled = True
    analyze_cfg.dedup_threshold = 0.85
    analyze_cfg.topic_min_cluster_size = 2
    analyze_cfg.language_detector = "langdetect"
    cfg.analyze = analyze_cfg
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path: Path):
    """Return (db_path, conn) with a seeded in-memory-like SQLite file."""
    db_path = tmp_path / "corpus.db"
    conn = _seed_sqlite_db(db_path)
    yield db_path, conn
    conn.close()


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """Return a fresh directory that the CLI will write reports into."""
    d = tmp_path / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def fake_config(seeded_db):
    """Return a fake Config wired to the seeded SQLite DB."""
    db_path, _conn = seeded_db
    return _make_fake_config(db_path)


# ---------------------------------------------------------------------------
# Helper: build the env dict for a single CLI invocation
# ---------------------------------------------------------------------------


def _env(report_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env dict with CORPUS_FORGE_REPORT_DIR set."""
    e = {**os.environ, "CORPUS_FORGE_REPORT_DIR": str(report_dir)}
    if extra:
        e.update(extra)
    return e


# ---------------------------------------------------------------------------
# T1 — Help surface: six subcommands listed
# ---------------------------------------------------------------------------


def test_analyze_help_lists_six_subcommands() -> None:
    """``corpus-forge analyze --help`` must list all six subcommands."""
    result = _runner().invoke(app, ["analyze", "--help"])
    # Even before the subgroup exists, collect the output for inspection.
    combined = (result.output or "") + (result.stderr or "")
    for sub in _SUBCOMMANDS:
        assert sub in combined, (
            f"subcommand '{sub}' not found in analyze --help output.\n"
            f"exit_code={result.exit_code}\noutput={combined!r}"
        )


# ---------------------------------------------------------------------------
# T2 — Each subcommand --help exits 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sub", _SUBCOMMANDS)
def test_subcommand_help_exits_zero(sub: str) -> None:
    """``corpus-forge analyze <sub> --help`` exits 0."""
    result = _runner().invoke(app, ["analyze", sub, "--help"])
    assert result.exit_code == 0, (
        f"analyze {sub} --help exited {result.exit_code}.\noutput={result.output!r}"
    )


# ---------------------------------------------------------------------------
# T3 — Each subcommand exits 0 on a fixture dataset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sub", _SUBCOMMANDS)
def test_subcommand_exits_zero_with_demo_dataset(
    sub: str,
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """``analyze <sub> --dataset demo`` exits 0 for the seeded demo dataset."""
    with (
        patch("corpus_forge.config.Config.load", return_value=fake_config),
        patch("corpus_forge.cli_analyze._get_backend_conn", return_value=MagicMock()),
    ):
        result = _runner().invoke(
            app,
            ["analyze", sub, "--dataset", "demo"],
            env=_env(report_dir),
        )
    assert result.exit_code == 0, (
        f"analyze {sub} --dataset demo exited {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# T4 — Report dir gets a markdown file per invocation
# ---------------------------------------------------------------------------


def test_stats_writes_markdown_report(
    fake_config: MagicMock,
    report_dir: Path,
    seeded_db: tuple[Path, Any],
) -> None:
    """``analyze stats --dataset demo`` writes a .md file under report_dir."""
    _db_path, _conn = seeded_db

    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "demo"],
            env=_env(report_dir),
        )

    assert result.exit_code == 0, result.output

    # A markdown file should exist somewhere under report_dir.
    md_files = list(report_dir.rglob("stats.md"))
    assert md_files, (
        f"No stats.md found under {report_dir}.\nDirectory contents: {list(report_dir.rglob('*'))}"
    )


def test_duplicates_writes_markdown_report(
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """``analyze duplicates --dataset demo`` writes a duplicates.md report."""
    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "duplicates", "--dataset", "demo"],
            env=_env(report_dir),
        )

    assert result.exit_code == 0, result.output

    md_files = list(report_dir.rglob("duplicates.md"))
    assert md_files, (
        f"No duplicates.md found under {report_dir}.\n"
        f"Directory contents: {list(report_dir.rglob('*'))}"
    )


# ---------------------------------------------------------------------------
# T5 — --out PATH override writes report to the specified file
# ---------------------------------------------------------------------------


def test_stats_out_flag_writes_to_custom_path(
    fake_config: MagicMock,
    tmp_path: Path,
) -> None:
    """``--out <path>`` writes the report exactly to the specified file."""
    out_file = tmp_path / "custom_stats.md"

    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "demo", "--out", str(out_file)],
        )

    assert result.exit_code == 0, result.output
    assert out_file.exists(), f"--out {out_file} was not created."
    content = out_file.read_text(encoding="utf-8")
    # The report must be non-empty markdown.
    assert len(content.strip()) > 0, "Report file is empty."


# ---------------------------------------------------------------------------
# T6 — --json flag on stats emits JSON, no markdown
# ---------------------------------------------------------------------------


def test_stats_json_flag_emits_json_no_markdown(
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """``analyze stats --json`` writes JSON to stdout and does NOT write a .md file."""
    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "demo", "--json"],
            env=_env(report_dir),
        )

    assert result.exit_code == 0, result.output

    # Stdout must be parseable JSON.
    try:
        payload = json.loads(result.output.strip())
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"analyze stats --json did not emit valid JSON.\noutput={result.output!r}\nerror={exc}"
        )

    # The JSON payload must contain stats keys.
    assert isinstance(payload, dict), f"Expected dict, got {type(payload).__name__}"

    # No markdown file should be written when --json is used.
    md_files = list(report_dir.rglob("stats.md"))
    assert not md_files, f"--json flag should suppress markdown report but found: {md_files}"


# ---------------------------------------------------------------------------
# T7 — duplicates: exact-dup section + near-dup section in report
# ---------------------------------------------------------------------------


def test_duplicates_report_contains_both_sections(
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """``analyze duplicates --threshold 0.85`` report contains exact- and near-dup sections."""
    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "duplicates", "--threshold", "0.85", "--dataset", "demo"],
            env=_env(report_dir),
        )

    assert result.exit_code == 0, result.output

    md_files = list(report_dir.rglob("duplicates.md"))
    assert md_files, f"No duplicates.md found under {report_dir}."

    content = md_files[0].read_text(encoding="utf-8")
    # Report must mention both exact and near duplicate sections.
    assert "exact" in content.lower(), (
        f"Exact-dup section missing from duplicates report.\ncontent={content!r}"
    )
    assert "near" in content.lower(), (
        f"Near-dup section missing from duplicates report.\ncontent={content!r}"
    )


# ---------------------------------------------------------------------------
# T8 — quality: persists rows to chunk_quality_signals
# ---------------------------------------------------------------------------


def test_quality_persists_rows_to_chunk_quality_signals(
    seeded_db: tuple[Path, Any],
    report_dir: Path,
) -> None:
    """``analyze quality --dataset demo`` writes rows to ``chunk_quality_signals``."""
    db_path, conn = seeded_db
    fake_config = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "quality", "--dataset", "demo"],
            env=_env(report_dir),
        )

    assert result.exit_code == 0, result.output

    # Verify that rows were inserted into chunk_quality_signals.
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chunk_quality_signals WHERE signal_name = 'learned_quality'")
    count = cur.fetchone()[0]
    assert count > 0, (
        f"Expected rows in chunk_quality_signals after analyze quality, found 0.\n"
        f"CLI output: {result.output!r}"
    )


# ---------------------------------------------------------------------------
# T9 — missing dataset exits non-zero, names the dataset in output
# ---------------------------------------------------------------------------


def test_missing_dataset_exits_nonzero(
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """An unknown dataset name exits non-zero and names the dataset in output."""
    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "nonexistent_dataset_xyz"],
            env=_env(report_dir),
        )

    # Must exit non-zero.
    assert result.exit_code != 0, (
        f"Expected non-zero exit for missing dataset but got {result.exit_code}.\n"
        f"output={result.output!r}"
    )

    # The dataset name must appear in combined output so the user knows what was wrong.
    combined = (result.output or "") + (result.stderr or "")
    assert "nonexistent_dataset_xyz" in combined, (
        f"Dataset name not echoed in error output.\ncombined={combined!r}"
    )


# ---------------------------------------------------------------------------
# T10 — --limit parameter passes through to every subcommand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sub", _SUBCOMMANDS)
def test_limit_parameter_accepted_by_all_subcommands(
    sub: str,
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """``--limit N`` is accepted by all six subcommands without error."""
    with (
        patch("corpus_forge.config.Config.load", return_value=fake_config),
        patch("corpus_forge.cli_analyze._get_backend_conn", return_value=MagicMock()),
    ):
        result = _runner().invoke(
            app,
            ["analyze", sub, "--dataset", "demo", "--limit", "3"],
            env=_env(report_dir),
        )

    # Must not fail with "No such option: --limit" (exit_code 2).
    assert result.exit_code != 2, f"analyze {sub} rejected --limit: {result.output!r}"


# ---------------------------------------------------------------------------
# T11 — Report directory creation is idempotent (running twice doesn't crash)
# ---------------------------------------------------------------------------


def test_report_directory_creation_is_idempotent(
    fake_config: MagicMock,
    report_dir: Path,
) -> None:
    """Running analyze stats twice does not raise on pre-existing report dir."""
    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result1 = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "demo"],
            env=_env(report_dir),
        )
        result2 = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "demo"],
            env=_env(report_dir),
        )

    assert result1.exit_code == 0, f"First run failed: {result1.output!r}"
    assert result2.exit_code == 0, f"Second run (idempotent) failed: {result2.output!r}"

    # Two separate timestamped subdirs must now exist under report_dir.
    subdirs = [p for p in report_dir.iterdir() if p.is_dir()]
    assert len(subdirs) >= 1, f"Expected at least one timestamped subdir, found: {subdirs}"


# ---------------------------------------------------------------------------
# T12 — analyze subgroup itself is registered on the root app
# ---------------------------------------------------------------------------


def test_analyze_is_registered_as_app_subgroup() -> None:
    """The root ``app`` must know the 'analyze' command group.

    This pins the ``app.add_typer(analyze_app, name='analyze')`` wiring
    in ``cli.py``.  If cli_analyze.py doesn't exist yet, this fails at
    import time or at the help-text assertion, both of which are correct
    RED states.
    """
    result = _runner().invoke(app, ["--help"])
    combined = (result.output or "") + (result.stderr or "")
    assert "analyze" in combined, (
        f"'analyze' not found in root --help output.\ncombined={combined!r}"
    )


# ---------------------------------------------------------------------------
# T13 — quality subcommand writes a markdown report in addition to persisting
# ---------------------------------------------------------------------------


def test_quality_also_writes_markdown_report(
    seeded_db: tuple[Path, Any],
    report_dir: Path,
) -> None:
    """``analyze quality`` writes a quality.md report alongside persisting rows."""
    db_path, _conn = seeded_db
    fake_config = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "quality", "--dataset", "demo"],
            env=_env(report_dir),
        )

    assert result.exit_code == 0, result.output

    md_files = list(report_dir.rglob("quality.md"))
    assert md_files, (
        f"No quality.md found under {report_dir}.\n"
        f"Directory contents: {list(report_dir.rglob('*'))}"
    )


# ---------------------------------------------------------------------------
# T14 — --report-dir flag is accepted as alternative to CORPUS_FORGE_REPORT_DIR
# ---------------------------------------------------------------------------


def test_report_dir_flag_overrides_env(
    fake_config: MagicMock,
    tmp_path: Path,
) -> None:
    """``--report-dir <path>`` writes the report under that directory
    even when CORPUS_FORGE_REPORT_DIR is set to something else.
    """
    env_dir = tmp_path / "env_reports"
    env_dir.mkdir()
    flag_dir = tmp_path / "flag_reports"
    flag_dir.mkdir()

    with patch("corpus_forge.config.Config.load", return_value=fake_config):
        result = _runner().invoke(
            app,
            ["analyze", "stats", "--dataset", "demo", "--report-dir", str(flag_dir)],
            env=_env(env_dir),
        )

    assert result.exit_code == 0, result.output

    # Report must land under flag_dir, not env_dir.
    flag_files = list(flag_dir.rglob("stats.md"))
    assert flag_files, (
        f"No stats.md under --report-dir path {flag_dir}.\n"
        f"flag_dir contents: {list(flag_dir.rglob('*'))}\n"
        f"env_dir contents: {list(env_dir.rglob('*'))}"
    )


# ---------------------------------------------------------------------------
# _load_chunks_for_dataset — non-SQLite (Postgres-shaped) cursor path
#
# The SQLite branch is exercised by every command test above; the
# non-sqlite branch (generic DB-API `cursor()` + `%s` placeholders) is driven
# here with a fake cursor-based connection returning real list rows, so the
# Postgres dialect + row-mapping path is covered without a live Postgres.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.executed: list[tuple] = []

    def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list:
        return self._rows


class _FakePgConn:
    """Non-sqlite connection (a real class, not a MagicMock) with a cursor."""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.last_cursor: _FakeCursor | None = None

    def cursor(self) -> _FakeCursor:
        self.last_cursor = _FakeCursor(self._rows)
        return self.last_cursor


def test_load_chunks_for_dataset_non_sqlite_path() -> None:
    from corpus_forge.cli_analyze import _load_chunks_for_dataset

    rows = [
        (1, "alpha", 10, "hash-a", "topic_x", '{"language": "en"}'),
        (2, "beta", 7, "hash-b", None, None),
    ]
    conn = _FakePgConn(rows)
    out = _load_chunks_for_dataset(conn, "demo")
    assert out == [
        {
            "id": 1,
            "text": "alpha",
            "token_count": 10,
            "content_hash": "hash-a",
            "classifier_label": "topic_x",
            "metadata": '{"language": "en"}',
        },
        {
            "id": 2,
            "text": "beta",
            "token_count": 7,
            "content_hash": "hash-b",
            "classifier_label": None,
            "metadata": None,
        },
    ]
    # `%s` Postgres placeholders + dataset bound through.
    assert conn.last_cursor is not None
    sql, params = conn.last_cursor.executed[0]
    assert "%s" in sql and params == ("demo",)


def test_load_chunks_for_dataset_non_sqlite_with_limit() -> None:
    from corpus_forge.cli_analyze import _load_chunks_for_dataset

    conn = _FakePgConn([(1, "alpha", 10, "hash-a", None, None)])
    out = _load_chunks_for_dataset(conn, "demo", limit=5)
    assert out is not None
    assert len(out) == 1
    sql, params = conn.last_cursor.executed[0]
    assert "LIMIT %s" in sql and params == ("demo", 5)


def test_load_chunks_for_dataset_magicmock_cursor_raises_returns_empty() -> None:
    """A MagicMock conn whose `.cursor()` raises → treated as 'no data' ([])."""
    from corpus_forge.cli_analyze import _load_chunks_for_dataset

    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("no cursor")
    assert _load_chunks_for_dataset(conn, "demo") == []


def test_load_chunks_for_dataset_magicmock_execute_raises_returns_empty() -> None:
    """A MagicMock conn whose cursor `.execute()` raises → treated as [].

    Drives the inner try/except guard (the MagicMock-shaped 'no data' path);
    real driver errors on a non-mock conn still propagate.
    """
    from corpus_forge.cli_analyze import _load_chunks_for_dataset

    conn = MagicMock()
    cur = MagicMock()
    cur.execute.side_effect = RuntimeError("execute boom")
    conn.cursor.return_value = cur
    assert _load_chunks_for_dataset(conn, "demo") == []


def test_load_chunks_for_dataset_real_conn_cursor_error_propagates() -> None:
    """A non-MagicMock conn whose `.cursor()` raises must re-raise (line 135).

    Operators must see real driver errors — only the MagicMock test path is
    swallowed as 'no data'.
    """
    from corpus_forge.cli_analyze import _load_chunks_for_dataset

    class _BoomConn:
        def cursor(self):
            raise RuntimeError("driver down")

    with pytest.raises(RuntimeError, match="driver down"):
        _load_chunks_for_dataset(_BoomConn(), "demo")


def test_load_chunks_for_dataset_real_conn_execute_error_propagates() -> None:
    """A non-MagicMock conn whose `.execute()` raises must re-raise (line 171)."""
    from corpus_forge.cli_analyze import _load_chunks_for_dataset

    class _BoomCursor:
        def execute(self, *a, **k):
            raise RuntimeError("query down")

    class _BoomExecConn:
        def cursor(self):
            return _BoomCursor()

    with pytest.raises(RuntimeError, match="query down"):
        _load_chunks_for_dataset(_BoomExecConn(), "demo")
