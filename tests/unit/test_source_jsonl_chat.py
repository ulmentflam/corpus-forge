"""Unit tests for corpus_forge.sources.jsonl_chat."""

import json
from pathlib import Path

import pytest

from corpus_forge.sources.base import RawConversation
from corpus_forge.sources.jsonl_chat import JSONLChatSource

pytestmark = pytest.mark.unit


@pytest.fixture
def jsonl_root(tmp_path: Path) -> Path:
    """Return a directory with one JSONL chat file."""
    chat = tmp_path / "chat.jsonl"
    lines = [
        json.dumps({"role": "user", "content": "Hi there", "ts": 1700000000}),
        json.dumps({"role": "assistant", "content": "Hello!", "ts": 1700000001}),
        json.dumps(
            {
                "role": "user",
                "content": "Use a tool",
                "tool_calls": [{"name": "search", "input": {}}],
                "ts": 1700000002,
            }
        ),
    ]
    chat.write_text("\n".join(lines))
    return tmp_path


class TestJSONLChatSource:
    def test_jsonl_chat_parses_fixture_file(self, jsonl_root: Path) -> None:
        source = JSONLChatSource(path=jsonl_root)
        chat = jsonl_root / "chat.jsonl"
        conv = source.parse(chat)

        assert conv is not None
        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 3
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hi there"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[2].tool_calls is not None
        assert conv.messages[2].tool_calls[0]["name"] == "search"

    def test_jsonl_chat_discover_finds_files(self, jsonl_root: Path) -> None:
        source = JSONLChatSource(path=jsonl_root)
        found = list(source.discover())
        names = {f.name for f in found}
        assert "chat.jsonl" in names

    def test_jsonl_chat_empty_file_returns_none(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")

        source = JSONLChatSource(path=tmp_path)
        result = source.parse(empty)
        assert result is None

    def test_jsonl_chat_session_link_client_set(self) -> None:
        assert JSONLChatSource._session_link_client is None

    def test_jsonl_chat_content_hash_populated(self, jsonl_root: Path) -> None:
        source = JSONLChatSource(path=jsonl_root)
        chat = jsonl_root / "chat.jsonl"
        conv = source.parse(chat)
        assert conv is not None
        assert len(conv.content_hash) == 64

    def test_jsonl_chat_discover_single_file(self, tmp_path: Path) -> None:
        """When path points to a single file, discover yields that file."""
        chat = tmp_path / "single.jsonl"
        chat.write_text(json.dumps({"role": "user", "content": "Hey"}) + "\n")
        source = JSONLChatSource(path=chat)
        found = list(source.discover())
        assert len(found) == 1
        assert found[0] == chat

    def test_jsonl_chat_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines in the JSONL file are ignored (line 54)."""
        chat = tmp_path / "blanks.jsonl"
        chat.write_text(
            "\n"
            + json.dumps({"role": "user", "content": "Hello"})
            + "\n\n"
            + json.dumps({"role": "assistant", "content": "Hi"})
            + "\n"
        )
        source = JSONLChatSource(path=tmp_path)
        conv = source.parse(chat)
        assert conv is not None
        assert len(conv.messages) == 2

    def test_jsonl_chat_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        """Malformed JSON lines are silently skipped (line 57-58)."""
        chat = tmp_path / "broken.jsonl"
        chat.write_text(
            json.dumps({"role": "user", "content": "Good"})
            + "\nnot-json!!!\n"
            + json.dumps({"role": "assistant", "content": "Also good"})
        )
        source = JSONLChatSource(path=tmp_path)
        conv = source.parse(chat)
        assert conv is not None
        assert len(conv.messages) == 2

    def test_jsonl_chat_skips_non_dict_json_lines(self, tmp_path: Path) -> None:
        """Lines whose JSON is not a dict are skipped (line 59-60)."""
        chat = tmp_path / "nondicts.jsonl"
        chat.write_text('["an", "array"]\n' + json.dumps({"role": "user", "content": "valid"}))
        source = JSONLChatSource(path=tmp_path)
        conv = source.parse(chat)
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].content == "valid"

    def test_jsonl_chat_timestamp_field_fallback(self, tmp_path: Path) -> None:
        """'timestamp' key is accepted as an alternative to 'ts' (line 63)."""
        chat = tmp_path / "ts_alt.jsonl"
        chat.write_text(json.dumps({"role": "user", "content": "Hi", "timestamp": 1700000000}))
        source = JSONLChatSource(path=tmp_path)
        conv = source.parse(chat)
        assert conv is not None
        assert conv.messages[0].ts == pytest.approx(1700000000.0)

    def test_jsonl_chat_missing_timestamp_falls_back_to_mtime(self, tmp_path: Path) -> None:
        """No ts/timestamp → falls back to file mtime (line 64-65)."""
        chat = tmp_path / "nomtime.jsonl"
        chat.write_text(json.dumps({"role": "user", "content": "no ts"}))
        source = JSONLChatSource(path=tmp_path)
        conv = source.parse(chat)
        assert conv is not None
        assert conv.messages[0].ts == pytest.approx(chat.stat().st_mtime, abs=1.0)
