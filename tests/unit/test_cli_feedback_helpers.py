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
