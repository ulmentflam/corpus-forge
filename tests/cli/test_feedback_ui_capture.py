"""Phase Q Wave 3 — ``corpus-forge feedback`` capture tests.

RED suite for Q3-T1 (capture surface).

Every test in this file MUST fail until the Coder ships
``corpus_forge/cli_feedback.py`` and wires it into ``corpus_forge/cli.py``.

Contracts tested:
- ``feedback start --no-tui --record-demo "q|s|t|target" --action quit`` writes one
  ``sdft_demonstrations`` row with ``source="cli_feedback"``.
- ``--record-demo`` can be repeated to write multiple rows in one session.
- ``query/student/teacher/target`` are pipe-separated for the scripted form.
- The written row has the expected content (query match, target match).
- An audit row is written alongside the demonstration row.

IO contract: tests assert SIDE EFFECTS (rows, session JSON files, exit codes)
not visual layout.  All tests use CliRunner with scripted --action flags.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner() -> CliRunner:
    return CliRunner()


def _env(feedback_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    e = {**os.environ, "CORPUS_FORGE_FEEDBACK_DIR": str(feedback_dir)}
    if extra:
        e.update(extra)
    return e


def _seed_sqlite_db(db_path: Path) -> sqlite3.Connection:
    """Create the minimal DB schema needed for the feedback command.

    Includes ``sdft_demonstrations`` so capture tests can assert on rows.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset TEXT NOT NULL,
            source_uri TEXT NOT NULL,
            content_hash TEXT,
            title TEXT,
            modified_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER,
            conversation_id INTEGER,
            dataset TEXT NOT NULL,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT,
            classifier_label TEXT,
            metadata TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sdft_demonstrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            student_messages TEXT NOT NULL,
            teacher_messages TEXT NOT NULL,
            target TEXT NOT NULL,
            source TEXT NOT NULL,
            trace_id TEXT,
            content_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT NOT NULL,
            args_json TEXT,
            result_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # Seed dataset row
    conn.execute("INSERT OR IGNORE INTO datasets (name) VALUES (?)", ("demo",))
    # Seed a document + chunk so the feedback queue has something to traverse
    conn.execute(
        "INSERT INTO documents (dataset, source_uri, title, modified_at) VALUES (?, ?, ?, ?)",
        ("demo", "file:///demo/note1.md", "Demo note", 1_700_000_000.0),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunks (document_id, dataset, text, token_count) VALUES (?, ?, ?, ?)",
        (doc_id, "demo", "This is a demo chunk for feedback testing.", 9),
    )
    conn.commit()
    return conn


def _make_fake_config(db_path: Path) -> MagicMock:
    backend_cfg = MagicMock()
    backend_cfg.kind = "sqlite"
    backend_cfg.dsn = str(db_path)
    backend_cfg.schema = "corpus"

    cfg = MagicMock()
    cfg.backend = backend_cfg
    cfg.datasets = []
    return cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "corpus.db"
    conn = _seed_sqlite_db(db_path)
    yield db_path, conn
    conn.close()


# ---------------------------------------------------------------------------
# T1 — Single --record-demo writes one sdft_demonstrations row with
#       source="cli_feedback"
# ---------------------------------------------------------------------------


def test_record_demo_single_writes_one_row(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``--record-demo "q|s|t|target" --action quit`` writes one row with source=cli_feedback."""
    db_path, conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--record-demo",
                "test query|student response|teacher demo|target text",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"exit_code={result.exit_code}\noutput={result.output!r}\nstderr={result.stderr!r}"
    )

    rows = conn.execute("SELECT * FROM sdft_demonstrations").fetchall()
    assert len(rows) == 1, (
        f"Expected 1 demonstration row, got {len(rows)}. output={result.output!r}"
    )


def test_record_demo_source_is_cli_feedback(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """The written row has ``source='cli_feedback'``."""
    db_path, conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--record-demo",
                "q|s|t|target",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    row = conn.execute("SELECT source FROM sdft_demonstrations LIMIT 1").fetchone()
    assert row is not None, "No demonstration row written"
    assert row[0] == "cli_feedback", f"Expected source='cli_feedback', got {row[0]!r}"


def test_record_demo_query_stored_correctly(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """The query segment from the pipe-separated string is stored in the ``query`` column."""
    db_path, conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--record-demo",
                "my query text|student|teacher|my target",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    row = conn.execute("SELECT query FROM sdft_demonstrations LIMIT 1").fetchone()
    assert row is not None, "No demonstration row written"
    assert "my query text" in row[0], f"Expected query to contain 'my query text', got {row[0]!r}"


def test_record_demo_target_stored_correctly(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """The target segment from the pipe-separated string is stored in the ``target`` column."""
    db_path, conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--record-demo",
                "query|student|teacher|expected target text",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    row = conn.execute("SELECT target FROM sdft_demonstrations LIMIT 1").fetchone()
    assert row is not None, "No demonstration row written"
    assert "expected target text" in row[0], (
        f"Expected target to contain 'expected target text', got {row[0]!r}"
    )


# ---------------------------------------------------------------------------
# T2 — Repeated --record-demo writes multiple rows in one session
# ---------------------------------------------------------------------------


def test_record_demo_repeated_writes_multiple_rows(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """Two ``--record-demo`` flags in one invocation write two distinct rows."""
    db_path, conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--record-demo",
                "query one|student one|teacher one|target one",
                "--record-demo",
                "query two|student two|teacher two|target two",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\noutput={result.output!r}"

    rows = conn.execute("SELECT * FROM sdft_demonstrations").fetchall()
    assert len(rows) == 2, f"Expected 2 demonstration rows, got {len(rows)}"


def test_record_demo_repeated_all_have_cli_feedback_source(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """All rows from a repeated ``--record-demo`` session have source='cli_feedback'."""
    db_path, conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--record-demo",
                "q1|s1|t1|target1",
                "--record-demo",
                "q2|s2|t2|target2",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    rows = conn.execute("SELECT source FROM sdft_demonstrations").fetchall()
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    for (source,) in rows:
        assert source == "cli_feedback", f"Expected source='cli_feedback', got {source!r}"
