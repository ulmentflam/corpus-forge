"""Unit tests for corpus_forge.sources.codex_cli."""

import json
from pathlib import Path

import pytest

from corpus_forge.sources.base import RawConversation
from corpus_forge.sources.codex_cli import CodexCLISource

pytestmark = pytest.mark.unit


@pytest.fixture
def codex_root(tmp_path: Path) -> Path:
    """Return a fake ~/.codex/sessions root with one JSONL session."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session = sessions_dir / "abc.jsonl"
    lines = [
        json.dumps({"role": "user", "content": "Write a function", "ts": 1700000000}),
        json.dumps({"role": "assistant", "content": "Sure, here it is", "ts": 1700000001}),
    ]
    session.write_text("\n".join(lines))
    return sessions_dir


class TestCodexCLISource:
    def test_codex_cli_parses_fixture_file(self, codex_root: Path) -> None:
        source = CodexCLISource(sessions_root=codex_root)
        session = codex_root / "abc.jsonl"
        conv = source.parse(session)

        assert conv is not None
        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Write a function"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "Sure, here it is"

    def test_codex_cli_discover_finds_files(self, codex_root: Path) -> None:
        source = CodexCLISource(sessions_root=codex_root)
        found = list(source.discover())
        names = {f.name for f in found}
        assert "abc.jsonl" in names

    def test_codex_cli_empty_file_returns_none(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        empty = sessions_dir / "empty.jsonl"
        empty.write_text("")

        source = CodexCLISource(sessions_root=sessions_dir)
        result = source.parse(empty)
        assert result is None

    def test_codex_cli_session_link_client_set(self) -> None:
        assert CodexCLISource._session_link_client == "codex-cli"

    def test_codex_cli_content_hash_populated(self, codex_root: Path) -> None:
        source = CodexCLISource(sessions_root=codex_root)
        session = codex_root / "abc.jsonl"
        conv = source.parse(session)
        assert conv is not None
        assert len(conv.content_hash) == 64

    def test_codex_cli_skips_malformed_lines(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "mixed.jsonl"
        session.write_text(
            json.dumps({"role": "user", "content": "Hello"})
            + "\nnot json at all\n"
            + json.dumps({"role": "assistant", "content": "World"})
        )
        source = CodexCLISource(sessions_root=sessions_dir)
        conv = source.parse(session)
        assert conv is not None
        assert len(conv.messages) == 2

    def test_codex_cli_skips_non_dict_json_lines(self, tmp_path: Path) -> None:
        """Lines that parse as non-dict JSON (array, string, number) are skipped (line 57-58)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "nondicts.jsonl"
        session.write_text('["an", "array"]\n' + json.dumps({"role": "user", "content": "valid"}))
        source = CodexCLISource(sessions_root=sessions_dir)
        conv = source.parse(session)
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].content == "valid"

    def test_codex_cli_timestamp_field_fallback(self, tmp_path: Path) -> None:
        """Entry with 'timestamp' (not 'ts') is parsed correctly (line 61)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "ts_alt.jsonl"
        session.write_text(json.dumps({"role": "user", "content": "Hi", "timestamp": 1700000000}))
        source = CodexCLISource(sessions_root=sessions_dir)
        conv = source.parse(session)
        assert conv is not None
        assert conv.messages[0].ts == pytest.approx(1700000000.0)

    def test_codex_cli_missing_timestamp_falls_back_to_mtime(self, tmp_path: Path) -> None:
        """Entry with neither 'ts' nor 'timestamp' falls back to file mtime (line 62-63)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        session = sessions_dir / "notimestamp.jsonl"
        session.write_text(json.dumps({"role": "user", "content": "no ts"}))
        source = CodexCLISource(sessions_root=sessions_dir)
        conv = source.parse(session)
        assert conv is not None
        assert conv.messages[0].ts == pytest.approx(session.stat().st_mtime, abs=1.0)
