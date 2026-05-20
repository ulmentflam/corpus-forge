"""Unit tests for ``corpus_forge.cli_feedback`` helper functions.

The end-to-end TUI flows are covered by ``tests/cli/test_feedback_ui_*.py``;
these unit tests target the helpers (_feedback_dir, _session_file,
_load_session, _save_session, _get_dataset_id, _do_record_demo) so the
coverage gate is not pulled down by the helpers' edge cases.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.cli_feedback import (
    _feedback_dir,
    _get_dataset_id,
    _load_session,
    _save_session,
    _session_file,
)

_RUNNER = CliRunner()


# ─────────────────────────────────────────────────────────────────────────────
# _feedback_dir
# ─────────────────────────────────────────────────────────────────────────────


def test_feedback_dir_respects_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "feedback-env"
    monkeypatch.setenv("CORPUS_FORGE_FEEDBACK_DIR", str(target))
    assert _feedback_dir() == target


def test_feedback_dir_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_FORGE_FEEDBACK_DIR", raising=False)
    out = _feedback_dir()
    assert isinstance(out, Path)
    # Default lives under ~/.cache/corpus-forge/feedback
    assert "feedback" in out.as_posix()


# ─────────────────────────────────────────────────────────────────────────────
# _session_file / _load_session / _save_session
# ─────────────────────────────────────────────────────────────────────────────


def test_session_file_pattern(tmp_path: Path) -> None:
    p = _session_file(tmp_path, "feedback-2026-x")
    assert p == tmp_path / "session-feedback-2026-x.json"


def test_save_then_load_session_round_trip(tmp_path: Path) -> None:
    payload = {
        "session_id": "s1",
        "dataset": "demo",
        "started_at": "2026-01-01T00:00:00Z",
        "queue_strategy": "default",
        "position": 0,
        "processed_chunk_ids": [],
        "pending_writes": [],
    }
    _save_session(tmp_path, payload)
    loaded = _load_session(tmp_path, "s1")
    assert loaded["dataset"] == "demo"
    assert loaded["session_id"] == "s1"


def test_load_session_missing_exits_nonzero(tmp_path: Path) -> None:
    """_load_session raises typer.Exit(1) when the file is missing."""
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        _load_session(tmp_path, "does-not-exist")
    assert exc_info.value.exit_code == 1


# ─────────────────────────────────────────────────────────────────────────────
# _get_dataset_id (SQLite path; Postgres path is integration-only)
# ─────────────────────────────────────────────────────────────────────────────


def test_get_dataset_id_returns_int_when_present() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE datasets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)"
    )
    conn.execute("INSERT INTO datasets (name) VALUES ('demo')")
    conn.commit()
    out = _get_dataset_id(conn, "demo")
    assert out == 1


def test_get_dataset_id_returns_none_when_absent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE datasets (id INTEGER, name TEXT)")
    conn.commit()
    assert _get_dataset_id(conn, "missing") is None


def test_get_dataset_id_returns_none_when_table_missing() -> None:
    conn = sqlite3.connect(":memory:")
    # No datasets table at all → broad except returns None.
    assert _get_dataset_id(conn, "demo") is None


# ─────────────────────────────────────────────────────────────────────────────
# CLI: list-sessions
# ─────────────────────────────────────────────────────────────────────────────


def test_list_sessions_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORPUS_FORGE_FEEDBACK_DIR", str(tmp_path / "missing"))
    result = _RUNNER.invoke(app, ["feedback", "list-sessions"])
    assert result.exit_code == 0
    assert "No sessions" in result.output


def test_list_sessions_empty_existing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fb_dir = tmp_path / "feedback"
    fb_dir.mkdir()
    monkeypatch.setenv("CORPUS_FORGE_FEEDBACK_DIR", str(fb_dir))
    result = _RUNNER.invoke(app, ["feedback", "list-sessions"])
    assert result.exit_code == 0
    assert "No sessions" in result.output


def test_list_sessions_prints_each_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fb_dir = tmp_path / "feedback"
    fb_dir.mkdir()
    sf = fb_dir / "session-abc.json"
    sf.write_text(
        json.dumps(
            {
                "session_id": "abc",
                "dataset": "demo",
                "started_at": "2026-01-01T00:00:00Z",
                "queue_strategy": "default",
                "position": 0,
                "processed_chunk_ids": [],
                "pending_writes": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_FORGE_FEEDBACK_DIR", str(fb_dir))
    result = _RUNNER.invoke(app, ["feedback", "list-sessions"])
    assert result.exit_code == 0
    assert "abc" in result.output
    assert "demo" in result.output


def test_list_sessions_unreadable_file_falls_back_to_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fb_dir = tmp_path / "feedback"
    fb_dir.mkdir()
    sf = fb_dir / "session-broken.json"
    sf.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_FEEDBACK_DIR", str(fb_dir))
    result = _RUNNER.invoke(app, ["feedback", "list-sessions"])
    assert result.exit_code == 0
    assert "unreadable" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# CLI: export-session
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# _do_record_demo — exercised directly (not through the TUI)
# ─────────────────────────────────────────────────────────────────────────────


def _make_seeded_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE datasets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE sdft_demonstrations (
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
        );
        INSERT INTO datasets (name) VALUES ('demo');
        """
    )
    c.commit()
    return c


def test_do_record_demo_dry_run_does_not_persist(capsys: pytest.CaptureFixture) -> None:
    from corpus_forge.cli_feedback import _do_record_demo

    conn = _make_seeded_conn()
    pending: list[dict] = []
    _do_record_demo(conn, "q|s|t|target", "demo", dry_run=True, pending_writes=pending)
    captured = capsys.readouterr()
    assert "dry-run" in captured.out
    # No row written.
    count = conn.execute("SELECT COUNT(*) FROM sdft_demonstrations").fetchone()[0]
    assert count == 0


def test_do_record_demo_persists_row_when_not_dry_run() -> None:
    from corpus_forge.cli_feedback import _do_record_demo

    conn = _make_seeded_conn()
    pending: list[dict] = []
    _do_record_demo(conn, "ask|stud|teach|targ", "demo", dry_run=False, pending_writes=pending)
    count = conn.execute("SELECT COUNT(*) FROM sdft_demonstrations").fetchone()[0]
    assert count == 1
    # pending_writes is appended for export-session.
    assert len(pending) == 1
    assert pending[0]["query"] == "ask"


def test_do_record_demo_bad_format_warns(capsys: pytest.CaptureFixture) -> None:
    from corpus_forge.cli_feedback import _do_record_demo

    conn = _make_seeded_conn()
    pending: list[dict] = []
    _do_record_demo(conn, "too|few", "demo", dry_run=False, pending_writes=pending)
    captured = capsys.readouterr()
    assert "must be 'query|student|teacher|target'" in captured.err
    assert len(pending) == 0


def test_do_record_demo_unknown_dataset_skips_and_warns(
    capsys: pytest.CaptureFixture,
) -> None:
    from corpus_forge.cli_feedback import _do_record_demo

    conn = _make_seeded_conn()
    pending: list[dict] = []
    _do_record_demo(conn, "q|s|t|targ", "unknown_dataset", dry_run=False, pending_writes=pending)
    captured = capsys.readouterr()
    assert "not found in datasets" in captured.err
    count = conn.execute("SELECT COUNT(*) FROM sdft_demonstrations").fetchone()[0]
    assert count == 0
    assert pending == []


def test_export_session_writes_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fb_dir = tmp_path / "feedback"
    fb_dir.mkdir()
    out_file = tmp_path / "exports" / "out.jsonl"
    sf = fb_dir / "session-xyz.json"
    sf.write_text(
        json.dumps(
            {
                "session_id": "xyz",
                "dataset": "demo",
                "started_at": "2026-01-01T00:00:00Z",
                "queue_strategy": "default",
                "position": 0,
                "processed_chunk_ids": [],
                "pending_writes": [
                    {"query": "q1", "target": "t1"},
                    {"query": "q2", "target": "t2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_FORGE_FEEDBACK_DIR", str(fb_dir))
    result = _RUNNER.invoke(
        app,
        [
            "feedback",
            "export-session",
            "--session",
            "xyz",
            "--out",
            str(out_file),
        ],
    )
    assert result.exit_code == 0
    assert out_file.is_file()
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert payloads[0]["query"] == "q1"
    assert payloads[1]["target"] == "t2"
