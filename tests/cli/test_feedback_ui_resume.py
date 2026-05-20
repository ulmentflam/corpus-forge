"""Phase Q Wave 3 — ``corpus-forge feedback`` session resume tests.

RED suite for Q3-T1 (resume surface).

Every test in this file MUST fail until the Coder ships
``corpus_forge/cli_feedback.py`` and wires it into ``corpus_forge/cli.py``.

Contracts tested:
- ``feedback start --no-tui --action skip --action skip --action quit`` saves a
  session JSON with a non-zero ``position`` field.
- Session state is persisted at
  ``$CORPUS_FORGE_FEEDBACK_DIR/session-<id>.json``.
- ``feedback resume --session <id> --no-tui --action quit`` resumes from the
  saved position and exits 0.
- ``feedback list-sessions`` lists the saved session.
- ``feedback resume --session NONEXISTENT`` exits non-zero with a descriptive
  error.

IO contract: tests assert SIDE EFFECTS (session JSON file, exit codes, output
text) not visual layout.
"""

from __future__ import annotations

import json
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
    """Create a minimal corpus DB with a seeded demo dataset."""
    conn = sqlite3.connect(str(db_path))

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
            dataset_id INTEGER NOT NULL,
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
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT,
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

    conn.execute("INSERT OR IGNORE INTO datasets (name) VALUES (?)", ("demo",))
    dataset_id = conn.execute("SELECT id FROM datasets WHERE name = 'demo'").fetchone()[0]
    conn.execute(
        "INSERT INTO documents (dataset_id, source_uri, title, modified_at) VALUES (?, ?, ?, ?)",
        (dataset_id, "file:///demo/a.md", "A", 1_700_000_000.0),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Seed several chunks so skip actions have something to traverse
    for i in range(5):
        conn.execute(
            "INSERT INTO chunks (document_id, text, token_count) VALUES (?, ?, ?)",
            (doc_id, f"Chunk {i} for feedback resume testing.", 6 + i),
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


def _find_session_file(feedback_dir: Path) -> Path | None:
    """Return the first session JSON file found in *feedback_dir*, or None."""
    for candidate in feedback_dir.glob("session-*.json"):
        return candidate
    return None


def _session_id_from_file(session_file: Path) -> str:
    """Extract the session_id from a session JSON file path."""
    # file name: session-<id>.json
    stem = session_file.stem  # "session-<id>"
    return stem[len("session-") :]


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
# T1 — Session JSON is persisted after skips
# ---------------------------------------------------------------------------


def test_session_json_written_after_skips(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``feedback start --action skip --action skip --action quit`` writes a session JSON."""
    db_path, _conn = seeded_db
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
                "--action",
                "skip",
                "--action",
                "skip",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"exit_code={result.exit_code}\noutput={result.output!r}\nstderr={result.stderr!r}"
    )

    session_file = _find_session_file(feedback_dir)
    assert session_file is not None, (
        f"No session-*.json found in {feedback_dir}.\n"
        f"Contents: {list(feedback_dir.iterdir()) if feedback_dir.exists() else '(dir absent)'}"
    )


def test_session_json_has_required_fields(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """The session JSON has fields: session_id, dataset, started_at, position."""
    db_path, _conn = seeded_db
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
                "--action",
                "skip",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    session_file = _find_session_file(feedback_dir)
    assert session_file is not None, "No session JSON written"

    data = json.loads(session_file.read_text(encoding="utf-8"))
    for field in ("session_id", "dataset", "started_at", "position"):
        assert field in data, (
            f"Session JSON missing required field '{field}'.\nFields present: {list(data.keys())}"
        )


def test_session_json_position_advances_on_skip(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """After two skips, the persisted ``position`` is >= 2."""
    db_path, _conn = seeded_db
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
                "--action",
                "skip",
                "--action",
                "skip",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    session_file = _find_session_file(feedback_dir)
    assert session_file is not None, "No session JSON written"

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert int(data["position"]) >= 2, (
        f"Expected position >= 2 after two skips, got {data['position']!r}"
    )


# ---------------------------------------------------------------------------
# T2 — ``feedback resume --session <id>`` resumes and exits 0
# ---------------------------------------------------------------------------


def test_feedback_resume_exits_zero(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``feedback resume --session <id> --no-tui --action quit`` exits 0."""
    db_path, _conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    # First: create a session
    with patch("corpus_forge.config.Config.load", return_value=cfg):
        _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--action",
                "skip",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    session_file = _find_session_file(feedback_dir)
    assert session_file is not None, "Precondition: no session JSON was written by start"

    session_id = _session_id_from_file(session_file)

    # Resume the session
    with patch("corpus_forge.config.Config.load", return_value=cfg):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "resume",
                "--session",
                session_id,
                "--no-tui",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"feedback resume exited {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# T3 — ``feedback list-sessions`` shows the saved session
# ---------------------------------------------------------------------------


def test_feedback_list_sessions_shows_session(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``feedback list-sessions`` lists the session created by ``feedback start``."""
    db_path, _conn = seeded_db
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config(db_path)

    # Create a session first
    with patch("corpus_forge.config.Config.load", return_value=cfg):
        _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--action",
                "skip",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    session_file = _find_session_file(feedback_dir)
    assert session_file is not None, "Precondition: no session file"
    session_id = _session_id_from_file(session_file)

    # Now list sessions
    with patch("corpus_forge.config.Config.load", return_value=cfg):
        result = _runner().invoke(
            app,
            ["feedback", "list-sessions"],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"feedback list-sessions exited {result.exit_code}.\noutput={result.output!r}"
    )
    combined = (result.output or "") + (result.stderr or "")
    assert session_id in combined or "demo" in combined, (
        f"Session id or dataset 'demo' not found in list-sessions output.\noutput={combined!r}"
    )


def test_feedback_list_sessions_empty_when_no_sessions(tmp_path: Path) -> None:
    """``feedback list-sessions`` exits 0 and produces output even when no sessions exist."""
    feedback_dir = tmp_path / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            ["feedback", "list-sessions"],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"feedback list-sessions exited {result.exit_code}.\noutput={result.output!r}"
    )


# ---------------------------------------------------------------------------
# T4 — ``feedback resume --session NONEXISTENT`` exits non-zero with clear error
# ---------------------------------------------------------------------------


def test_feedback_resume_nonexistent_exits_nonzero(tmp_path: Path) -> None:
    """``feedback resume --session NONEXISTENT`` exits non-zero.

    The exit must be specifically due to the missing session, not because the
    'feedback' command itself is absent (which would also be non-zero but for
    the wrong reason).
    """
    feedback_dir = tmp_path / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "resume",
                "--session",
                "feedback-nonexistent-0000",
                "--no-tui",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code != 0, (
        f"Expected non-zero exit for nonexistent session but got {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )
    # Guard against accidentally passing because the 'feedback' command doesn't exist.
    combined = (result.output or "") + (result.stderr or "")
    assert "No such command 'feedback'" not in combined, (
        "Exit was due to missing 'feedback' command, not a missing session — "
        "cli_feedback.py must be shipped before this test passes correctly."
    )


def test_feedback_resume_nonexistent_prints_error(tmp_path: Path) -> None:
    """``feedback resume --session NONEXISTENT`` prints a descriptive error message."""
    feedback_dir = tmp_path / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    fake_id = "feedback-nonexistent-0000"

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "resume",
                "--session",
                fake_id,
                "--no-tui",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    combined = (result.output or "") + (result.stderr or "")
    assert (
        fake_id in combined or "not found" in combined.lower() or "session" in combined.lower()
    ), f"Error message does not mention session id or 'not found'.\noutput={combined!r}"
