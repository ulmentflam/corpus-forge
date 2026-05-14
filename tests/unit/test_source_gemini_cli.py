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

    def test_gemini_cli_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """parse() must return None when the file is not valid JSON (line 55-56)."""
        project_dir = tmp_path / "proj" / "chats"
        project_dir.mkdir(parents=True)
        bad = project_dir / "bad.json"
        bad.write_text("{not valid json at all!!!}")

        source = GeminiCLISource(projects_root=tmp_path)
        result = source.parse(bad)
        assert result is None

    def test_gemini_cli_non_list_json_returns_none(self, tmp_path: Path) -> None:
        """parse() must return None when JSON root is not a list (line 58-59)."""
        project_dir = tmp_path / "proj" / "chats"
        project_dir.mkdir(parents=True)
        obj_file = project_dir / "obj.json"
        obj_file.write_text('{"role": "user", "content": "hello"}')

        source = GeminiCLISource(projects_root=tmp_path)
        result = source.parse(obj_file)
        assert result is None

    def test_gemini_cli_skips_non_dict_entries(self, tmp_path: Path) -> None:
        """Non-dict entries in the list are skipped (line 63-64); valid ones still parsed."""
        project_dir = tmp_path / "proj" / "chats"
        project_dir.mkdir(parents=True)
        mixed = project_dir / "mixed.json"
        import json as _json

        mixed.write_text(
            _json.dumps(
                [
                    "a plain string",
                    42,
                    {"role": "user", "content": "valid", "timestamp": 1700000000.0},
                ]
            )
        )
        source = GeminiCLISource(projects_root=tmp_path)
        conv = source.parse(mixed)
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].content == "valid"

    def test_gemini_cli_missing_timestamp_falls_back_to_mtime(self, tmp_path: Path) -> None:
        """Entry without 'timestamp' falls back to file mtime (line 70-71)."""
        project_dir = tmp_path / "proj" / "chats"
        project_dir.mkdir(parents=True)
        session = project_dir / "notimestamp.json"
        import json as _json

        session.write_text(_json.dumps([{"role": "user", "content": "no ts"}]))

        source = GeminiCLISource(projects_root=tmp_path)
        conv = source.parse(session)
        assert conv is not None
        # ts should equal the file's mtime (within tolerance)
        assert conv.messages[0].ts == pytest.approx(session.stat().st_mtime, abs=1.0)

    def test_gemini_cli_discover_skips_non_directories(self, tmp_path: Path) -> None:
        """discover() skips non-directory entries in the root (line 43)."""
        # Place a file directly in the root — discover should not crash
        stray_file = tmp_path / "stray.txt"
        stray_file.write_text("I am not a directory")

        source = GeminiCLISource(projects_root=tmp_path)
        found = list(source.discover())
        assert found == []
