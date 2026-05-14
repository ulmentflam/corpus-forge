"""Unit tests for corpus_forge.sources.chatgpt_export."""

import json
from pathlib import Path

import pytest

from corpus_forge.sources.base import RawConversation
from corpus_forge.sources.chatgpt_export import ChatGPTExportSource

pytestmark = pytest.mark.unit

# Minimal ChatGPT export mapping tree:
# root -> system node -> user turn -> assistant turn (current)
_SAMPLE_CONVERSATIONS = [
    {
        "id": "conv-001",
        "title": "Test Chat",
        "current_node": "node-asst",
        "mapping": {
            "node-root": {
                "id": "node-root",
                "parent": None,
                "message": None,
            },
            "node-user": {
                "id": "node-user",
                "parent": "node-root",
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["What is 2+2?"]},
                    "create_time": 1700000000.0,
                },
            },
            "node-asst": {
                "id": "node-asst",
                "parent": "node-user",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": ["It is 4."]},
                    "create_time": 1700000001.0,
                },
            },
        },
    }
]


@pytest.fixture
def export_root(tmp_path: Path) -> Path:
    """Return a fake ChatGPT export directory with conversations.json."""
    (tmp_path / "conversations.json").write_text(json.dumps(_SAMPLE_CONVERSATIONS))
    return tmp_path


class TestChatGPTExportSource:
    def test_chatgpt_export_parses_fixture_file(self, export_root: Path) -> None:
        source = ChatGPTExportSource(export_root=export_root)
        conv_file = export_root / "conversations.json"
        conv = source.parse(conv_file)

        assert conv is not None
        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert "2+2" in conv.messages[0].content
        assert conv.messages[1].role == "assistant"
        assert "4" in conv.messages[1].content

    def test_chatgpt_export_discover_finds_files(self, export_root: Path) -> None:
        source = ChatGPTExportSource(export_root=export_root)
        found = list(source.discover())
        assert len(found) == 1
        assert found[0].name == "conversations.json"

    def test_chatgpt_export_empty_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "conversations.json").write_text("[]")
        source = ChatGPTExportSource(export_root=tmp_path)
        conv_file = tmp_path / "conversations.json"
        result = source.parse(conv_file)
        assert result is None

    def test_chatgpt_export_session_link_client_set(self) -> None:
        assert ChatGPTExportSource._session_link_client == "chatgpt-export"

    def test_chatgpt_export_title_preserved(self, export_root: Path) -> None:
        source = ChatGPTExportSource(export_root=export_root)
        conv_file = export_root / "conversations.json"
        conv = source.parse(conv_file)
        assert conv is not None
        assert conv.title == "Test Chat"

    def test_chatgpt_export_scan_yields_per_conversation(self, export_root: Path) -> None:
        """scan() should yield one RawConversation per conversation object."""
        source = ChatGPTExportSource(export_root=export_root)
        results = list(source.scan())
        assert len(results) == 1
        assert results[0].external_id == "conv-001"

    def test_chatgpt_export_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """parse() returns None for invalid JSON (line 108-109)."""
        (tmp_path / "conversations.json").write_text("{not valid json!!!")
        source = ChatGPTExportSource(export_root=tmp_path)
        result = source.parse(tmp_path / "conversations.json")
        assert result is None

    def test_chatgpt_export_non_list_json_returns_none(self, tmp_path: Path) -> None:
        """parse() returns None when JSON root is a dict, not a list (line 111)."""
        (tmp_path / "conversations.json").write_text('{"key": "value"}')
        source = ChatGPTExportSource(export_root=tmp_path)
        result = source.parse(tmp_path / "conversations.json")
        assert result is None

    def test_chatgpt_export_non_dict_conv_object_skipped(self, tmp_path: Path) -> None:
        """Non-dict items in the conversation list are skipped by _parse_conversation."""
        data = [
            "a plain string",  # skipped
            _SAMPLE_CONVERSATIONS[0],  # valid
        ]
        (tmp_path / "conversations.json").write_text(json.dumps(data))
        source = ChatGPTExportSource(export_root=tmp_path)
        result = source.parse(tmp_path / "conversations.json")
        # The string is skipped; valid conv is returned
        assert result is not None

    def test_chatgpt_export_empty_mapping_returns_none(self, tmp_path: Path) -> None:
        """Conversation with empty mapping produces no messages → _parse_conversation None."""
        data = [
            {
                "id": "conv-empty",
                "title": "Empty",
                "current_node": None,
                "mapping": {},
            }
        ]
        (tmp_path / "conversations.json").write_text(json.dumps(data))
        source = ChatGPTExportSource(export_root=tmp_path)
        result = source.parse(tmp_path / "conversations.json")
        assert result is None

    def test_chatgpt_export_no_conversations_json_discover_empty(self, tmp_path: Path) -> None:
        """discover() yields nothing if conversations.json is absent (line 91-93)."""
        source = ChatGPTExportSource(export_root=tmp_path)
        found = list(source.discover())
        assert found == []

    def test_chatgpt_export_scan_skips_malformed_json_file(self, tmp_path: Path) -> None:
        """scan() continues past files with invalid JSON (line 177-178)."""
        (tmp_path / "conversations.json").write_text("{bad json")
        source = ChatGPTExportSource(export_root=tmp_path)
        results = list(source.scan())
        assert results == []

    def test_chatgpt_export_scan_skips_non_list_json(self, tmp_path: Path) -> None:
        """scan() skips file if JSON root is not a list (line 179-180)."""
        (tmp_path / "conversations.json").write_text('{"not": "a list"}')
        source = ChatGPTExportSource(export_root=tmp_path)
        results = list(source.scan())
        assert results == []

    def test_chatgpt_export_missing_timestamp_falls_back_to_mtime(self, tmp_path: Path) -> None:
        """Messages with no create_time fall back to file mtime (line 140-141)."""
        data = [
            {
                "id": "conv-no-ts",
                "title": "No TS",
                "current_node": "node-asst",
                "mapping": {
                    "node-user": {
                        "id": "node-user",
                        "parent": None,
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": ["hi"]},
                            "create_time": None,
                        },
                    },
                    "node-asst": {
                        "id": "node-asst",
                        "parent": "node-user",
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["hello"]},
                            "create_time": None,
                        },
                    },
                },
            }
        ]
        f = tmp_path / "conversations.json"
        f.write_text(json.dumps(data))
        source = ChatGPTExportSource(export_root=tmp_path)
        result = source.parse(f)
        assert result is not None
        # All timestamps should be the file's mtime
        mtime = f.stat().st_mtime
        for msg in result.messages:
            assert msg.ts == pytest.approx(mtime, abs=1.0)
