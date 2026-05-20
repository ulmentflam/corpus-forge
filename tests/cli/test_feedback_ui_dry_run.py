"""Phase Q Wave 3 — ``corpus-forge feedback`` dry-run and export-session tests.

RED suite for Q3-T1 (dry-run + export surface).

Every test in this file MUST fail until the Coder ships
``corpus_forge/cli_feedback.py`` and wires it into ``corpus_forge/cli.py``.

Contracts tested:
- ``feedback start --dry-run --record-demo "q|s|t|t" --action quit`` writes NO
  rows to ``sdft_demonstrations`` and NO session JSON.  Output describes what it
  would do.
- ``feedback export-session --session <id> --format jsonl --out PATH`` exports
  the session to a JSONL file (one JSON object per line).

IO contract: tests assert SIDE EFFECTS (absent rows, absent JSON files, JSONL
content, exit codes) not visual layout.
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
    """Create a minimal corpus DB with ``sdft_demonstrations`` ready."""
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

    conn.execute("INSERT OR IGNORE INTO datasets (name) VALUES (?)", ("demo",))
    conn.execute(
        "INSERT INTO documents (dataset, source_uri, title, modified_at) VALUES (?, ?, ?, ?)",
        ("demo", "file:///demo/note1.md", "Demo note", 1_700_000_000.0),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO chunks (document_id, dataset, text, token_count) VALUES (?, ?, ?, ?)",
        (doc_id, "demo", "A dry-run test chunk.", 5),
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
    if not feedback_dir.exists():
        return None
    for candidate in feedback_dir.glob("session-*.json"):
        return candidate
    return None


def _session_id_from_file(session_file: Path) -> str:
    return session_file.stem[len("session-") :]


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
# T1 — ``--dry-run`` writes NO sdft_demonstrations rows
# ---------------------------------------------------------------------------


def test_dry_run_writes_no_demonstration_rows(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``feedback start --dry-run --record-demo ... --action quit`` leaves sdft_demonstrations
    empty."""
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
                "--dry-run",
                "--record-demo",
                "q|s|t|target",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"--dry-run exited {result.exit_code}.\noutput={result.output!r}\nstderr={result.stderr!r}"
    )

    rows = conn.execute("SELECT * FROM sdft_demonstrations").fetchall()
    assert len(rows) == 0, (
        f"--dry-run should not write rows but found {len(rows)} row(s).\nRows: {rows}"
    )


def test_dry_run_writes_no_session_json(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``feedback start --dry-run`` writes no session JSON file.

    Requires the command to exit 0 first so this cannot pass vacuously due to
    the 'feedback' command being absent.
    """
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
                "--dry-run",
                "--record-demo",
                "q|s|t|target",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    # The command must exist and exit 0 for this assertion to be meaningful.
    assert result.exit_code == 0, (
        f"--dry-run exited {result.exit_code} (command may not exist yet).\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )

    session_file = _find_session_file(feedback_dir)
    assert session_file is None, (
        f"--dry-run must not write a session file, but found: {session_file}"
    )


def test_dry_run_prints_what_it_would_do(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``--dry-run`` output describes what would be written (dry run preview)."""
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
                "--dry-run",
                "--record-demo",
                "q|s|t|target",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    combined = (result.output or "") + (result.stderr or "")
    # The output must mention "dry" (dry-run) or "would" or "preview"
    assert any(kw in combined.lower() for kw in ("dry", "would", "preview", "no-op")), (
        f"--dry-run output does not describe what it would do.\noutput={combined!r}"
    )


def test_dry_run_exits_zero(
    seeded_db: tuple[Path, sqlite3.Connection],
    tmp_path: Path,
) -> None:
    """``feedback start --dry-run`` exits 0 (no crash, graceful)."""
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
                "--dry-run",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"--dry-run exited {result.exit_code}.\noutput={result.output!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# T2 — ``feedback export-session --session <id> --format jsonl --out PATH``
# ---------------------------------------------------------------------------


def _seed_session_file(
    feedback_dir: Path, session_id: str, dataset: str, demo_rows: list[dict]
) -> Path:
    """Write a fake session JSON and populate demo_rows into sdft_demonstrations.

    This helper bypasses the CLI to pre-seed state, letting export-session
    tests run without a full feedback start → resume lifecycle.
    """
    feedback_dir.mkdir(parents=True, exist_ok=True)
    session_data = {
        "session_id": session_id,
        "dataset": dataset,
        "started_at": "2026-05-20T00:00:00Z",
        "queue_strategy": "random",
        "position": len(demo_rows),
        "processed_chunk_ids": list(range(len(demo_rows))),
        "pending_writes": demo_rows,
    }
    session_file = feedback_dir / f"session-{session_id}.json"
    session_file.write_text(json.dumps(session_data), encoding="utf-8")
    return session_file


def test_export_session_produces_jsonl_file(tmp_path: Path) -> None:
    """``feedback export-session --format jsonl --out PATH`` writes a JSONL file."""
    feedback_dir = tmp_path / "feedback"
    session_id = "feedback-20260520T000000-abcdef01"
    demo_rows = [
        {
            "query": "test q",
            "student_messages": [{"role": "user", "content": "test q"}],
            "teacher_messages": [{"role": "assistant", "content": "teacher demo"}],
            "target": "target text",
            "source": "cli_feedback",
        }
    ]
    _seed_session_file(feedback_dir, session_id, "demo", demo_rows)

    out_file = tmp_path / "export.jsonl"

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "export-session",
                "--session",
                session_id,
                "--format",
                "jsonl",
                "--out",
                str(out_file),
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"export-session exited {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )
    assert out_file.exists(), f"Expected {out_file} to be created by export-session"


def test_export_session_jsonl_is_valid_json_per_line(tmp_path: Path) -> None:
    """Each line in the exported JSONL file is valid JSON."""
    feedback_dir = tmp_path / "feedback"
    session_id = "feedback-20260520T000000-abcdef02"
    demo_rows = [
        {
            "query": "q1",
            "student_messages": [{"role": "user", "content": "q1"}],
            "teacher_messages": [{"role": "assistant", "content": "t1"}],
            "target": "target1",
            "source": "cli_feedback",
        },
        {
            "query": "q2",
            "student_messages": [{"role": "user", "content": "q2"}],
            "teacher_messages": [{"role": "assistant", "content": "t2"}],
            "target": "target2",
            "source": "cli_feedback",
        },
    ]
    _seed_session_file(feedback_dir, session_id, "demo", demo_rows)

    out_file = tmp_path / "export.jsonl"

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "export-session",
                "--session",
                session_id,
                "--format",
                "jsonl",
                "--out",
                str(out_file),
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, f"exit_code={result.exit_code}\noutput={result.output!r}"
    assert out_file.exists(), "JSONL file not created"

    lines = [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 1, f"Expected at least one JSONL line, got {len(lines)}"

    for i, line in enumerate(lines):
        parsed = json.loads(line)  # Must not raise
        assert isinstance(parsed, dict), f"Line {i} is not a JSON object: {line!r}"


def test_export_session_jsonl_contains_query_field(tmp_path: Path) -> None:
    """Each JSONL row has a ``query`` field matching the demo content."""
    feedback_dir = tmp_path / "feedback"
    session_id = "feedback-20260520T000000-abcdef03"
    demo_rows = [
        {
            "query": "specific query text",
            "student_messages": [{"role": "user", "content": "specific query text"}],
            "teacher_messages": [{"role": "assistant", "content": "teacher response"}],
            "target": "target answer",
            "source": "cli_feedback",
        }
    ]
    _seed_session_file(feedback_dir, session_id, "demo", demo_rows)

    out_file = tmp_path / "export.jsonl"

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "export-session",
                "--session",
                session_id,
                "--format",
                "jsonl",
                "--out",
                str(out_file),
            ],
            env=_env(feedback_dir),
        )

    # Command must succeed for the output to be inspectable.
    assert result.exit_code == 0, (
        f"export-session exited {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )
    assert out_file.exists(), f"Expected {out_file} to be created"

    lines = [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 1, f"Expected at least one JSONL line, got {len(lines)}"
    first = json.loads(lines[0])
    assert "query" in first or "target" in first, (
        f"JSONL row missing expected fields.\nRow: {first}"
    )


def test_export_session_nonexistent_session_exits_nonzero(tmp_path: Path) -> None:
    """``feedback export-session --session NONEXISTENT`` exits non-zero.

    The exit must be specifically due to the missing session, not because the
    'feedback' command itself is absent.
    """
    feedback_dir = tmp_path / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "out.jsonl"

    with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "export-session",
                "--session",
                "feedback-does-not-exist",
                "--format",
                "jsonl",
                "--out",
                str(out_file),
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code != 0, (
        f"Expected non-zero exit for missing session but got {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )
    # Guard against accidentally passing because the 'feedback' command doesn't exist.
    combined = (result.output or "") + (result.stderr or "")
    assert "No such command 'feedback'" not in combined, (
        "Exit was due to missing 'feedback' command, not a missing session — "
        "cli_feedback.py must be shipped before this test passes correctly."
    )
