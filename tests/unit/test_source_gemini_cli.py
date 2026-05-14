"""Unit tests for corpus_forge.sources.gemini_cli."""

import json
from pathlib import Path

import pytest

from corpus_forge.sources.base import RawConversation
from corpus_forge.sources.gemini_cli import GeminiCLISource

pytestmark = pytest.mark.unit


@pytest.fixture
def gemini_root(tmp_path: Path) -> Path:
    """Return a fake ~/.gemini/tmp root with one project/chats/session.json."""
    project_dir = tmp_path / "abc123" / "chats"
    project_dir.mkdir(parents=True)
    session = project_dir / "session.json"
    session.write_text(
        json.dumps(
            [
                {"role": "user", "content": "Hello Gemini", "timestamp": "2024-01-01T00:00:00"},
                {"role": "model", "content": "Hello, human!", "timestamp": "2024-01-01T00:00:01"},
            ]
        )
    )
    return tmp_path


class TestGeminiCLISource:
    def test_gemini_cli_parses_fixture_file(self, gemini_root: Path) -> None:
        source = GeminiCLISource(projects_root=gemini_root)
        session = gemini_root / "abc123" / "chats" / "session.json"
        conv = source.parse(session)

        assert conv is not None
        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello Gemini"
        assert conv.messages[1].role == "assistant"  # model → assistant
        assert conv.messages[1].content == "Hello, human!"

    def test_gemini_cli_discover_finds_files(self, gemini_root: Path) -> None:
        source = GeminiCLISource(projects_root=gemini_root)
        found = list(source.discover())
        assert len(found) == 1
        assert found[0].name == "session.json"

    def test_gemini_cli_empty_file_returns_none(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj" / "chats"
        project_dir.mkdir(parents=True)
        empty = project_dir / "empty.json"
        empty.write_text("[]")  # valid JSON, but empty list

        source = GeminiCLISource(projects_root=tmp_path)
        result = source.parse(empty)
        assert result is None

    def test_gemini_cli_session_link_client_set(self) -> None:
        assert GeminiCLISource._session_link_client == "gemini-cli"

    def test_gemini_cli_content_hash_populated(self, gemini_root: Path) -> None:
        source = GeminiCLISource(projects_root=gemini_root)
        session = gemini_root / "abc123" / "chats" / "session.json"
        conv = source.parse(session)
        assert conv is not None
        assert len(conv.content_hash) == 64  # sha256 hex

    def test_gemini_cli_external_id_is_stem(self, gemini_root: Path) -> None:
        source = GeminiCLISource(projects_root=gemini_root)
        session = gemini_root / "abc123" / "chats" / "session.json"
        conv = source.parse(session)
        assert conv is not None
        assert conv.external_id == "session"
