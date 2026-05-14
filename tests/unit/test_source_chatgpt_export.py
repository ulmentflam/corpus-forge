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
