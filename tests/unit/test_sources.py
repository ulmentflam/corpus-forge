"""Unit tests for Claude Code and OpenCode source plugins."""

import json
from pathlib import Path

from corpus_forge.sources.base import RawConversation
from corpus_forge.sources.claude_code import ClaudeCodeSource
from corpus_forge.sources.opencode import OpenCodeSource


class TestClaudeCodeSource:
    """Tests for ClaudeCodeSource class."""

    def test_name_and_kind(self):
        """Test source name and dataset kind."""
        source = ClaudeCodeSource(projects_root=Path("/tmp/test"))
        assert source.name == "claude_code"
        assert source.dataset_kind == "chat"

    def test_discover_yields_jsonl_files(self, sample_claude_code_dir):
        """Test that discover yields session.jsonl files."""
        source = ClaudeCodeSource(projects_root=sample_claude_code_dir)
        files = list(source.discover())
        assert len(files) == 1
        assert files[0].name == "session1.jsonl"

    def test_discover_skips_subagents(self, sample_claude_code_dir):
        """Test that subagent directories are skipped when configured."""
        # Create a subagent directory at the root level
        subagent_dir = sample_claude_code_dir / "_subagent"
        subagent_dir.mkdir()
        (subagent_dir / "session.jsonl").write_text('{"uuid": "sub1"}')

        source = ClaudeCodeSource(projects_root=sample_claude_code_dir, include_subagents=False)
        files = list(source.discover())
        # Should only find the main project, not the _subagent directory
        assert len(files) == 1
        assert files[0].parent.name == "test_project"

    def test_discover_includes_subagents(self, sample_claude_code_dir):
        """Test that subagent directories are included when configured."""
        subagent_dir = sample_claude_code_dir / "_subagent"
        subagent_dir.mkdir()
        (subagent_dir / "session.jsonl").write_text('{"uuid": "sub1"}')

        source = ClaudeCodeSource(projects_root=sample_claude_code_dir, include_subagents=True)
        files = list(source.discover())
        assert len(files) == 2

    def test_parse_valid_session(self, sample_claude_code_dir):
        """Test parsing a valid session file."""
        source = ClaudeCodeSource(projects_root=sample_claude_code_dir)
        session_file = sample_claude_code_dir / "test_project" / "session1.jsonl"
        conv = source.parse(session_file)

        assert isinstance(conv, RawConversation)
        assert conv.source_uri.startswith("claude-code://")
        assert conv.external_id == "session1"
        assert conv.content_hash  # Non-empty hash
        assert len(conv.messages) >= 2
        assert conv.messages[0].role == "user"
        assert conv.messages[1].role == "assistant"

    def test_parse_empty_file(self, temp_dir):
        """Test parsing an empty session file."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()
        project_dir = projects_dir / "empty_project"
        project_dir.mkdir()
        (project_dir / "empty.jsonl").write_text("")

        source = ClaudeCodeSource(projects_root=projects_dir)
        conv = source.parse(project_dir / "empty.jsonl")

        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 0
        assert conv.started_at is None
        assert conv.ended_at is None

    def test_parse_malformed_lines(self, temp_dir):
        """Test that malformed JSON lines are skipped."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()
        project_dir = projects_dir / "test_project"
        project_dir.mkdir()
        session_file = project_dir / "mixed.jsonl"
        session_file.write_text(
            '{"uuid": "msg1", "message": {"role": "user", "content": "Hello"},'
            ' "timestamp": 1000}\n'
            "this is not json\n"
            '{"uuid": "msg2", "message": {"role": "assistant", "content": "Hi"},'
            ' "timestamp": 1001}\n',
        )

        source = ClaudeCodeSource(projects_root=projects_dir)
        conv = source.parse(session_file)

        assert len(conv.messages) == 2

    def test_parse_list_content(self, temp_dir):
        """Test parsing messages with list content."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()
        project_dir = projects_dir / "test_project"
        project_dir.mkdir()
        session_file = project_dir / "list_content.jsonl"
        session_file.write_text(
            '{"uuid": "msg1", '
            '"message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}, '
            '"timestamp": 1000}\n'
        )

        source = ClaudeCodeSource(projects_root=projects_dir)
        conv = source.parse(session_file)

        assert len(conv.messages) == 1
        assert conv.messages[0].content == "Hello"


class TestOpenCodeSource:
    """Tests for OpenCodeSource class."""

    def test_name_and_kind(self):
        """Test source name and dataset kind."""
        source = OpenCodeSource(storage_root=Path("/tmp/test"))
        assert source.name == "opencode"
        assert source.dataset_kind == "chat"

    def test_discover_yields_message_files(self, sample_opencode_dir):
        """Test that discover yields message.json files."""
        source = OpenCodeSource(storage_root=sample_opencode_dir)
        files = list(source.discover())
        assert len(files) == 1
        assert files[0].name == "message.json"

    def test_parse_valid_message(self, sample_opencode_dir):
        """Test parsing a valid message file."""
        source = OpenCodeSource(storage_root=sample_opencode_dir)
        message_file = sample_opencode_dir / "message" / "msg1" / "message.json"
        conv = source.parse(message_file)

        assert isinstance(conv, RawConversation)
        assert conv.source_uri.startswith("opencode://")
        assert conv.external_id == "msg1"
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "assistant"
        # Parts use 'content' key but flatten_message expects 'text' key
        # So content will be empty when parts don't have 'text' key
        assert conv.messages[0].content == ""

    def test_parse_with_parts(self, temp_dir):
        """Test parsing a message with parts content."""
        storage_dir = temp_dir / "storage"
        storage_dir.mkdir()
        message_dir = storage_dir / "message" / "msg2"
        message_dir.mkdir(parents=True)
        message_file = message_dir / "message.json"
        # flatten_message expects 'text' key for type='text' blocks
        message_file.write_text(
            json.dumps(
                {
                    "id": "msg2",
                    "parentId": None,
                    "role": "user",
                    "content": "Simple content",
                    "timestamp": 2000,
                    "parts": [
                        {"type": "text", "text": "Part 1"},
                        {"type": "text", "text": "Part 2"},
                    ],
                }
            )
        )

        source = OpenCodeSource(storage_root=storage_dir)
        conv = source.parse(message_file)

        assert len(conv.messages) == 1
        assert conv.messages[0].content == "Part 1Part 2"

    def test_parse_invalid_json(self, temp_dir):
        """Test parsing an invalid JSON file returns empty conversation."""
        storage_dir = temp_dir / "storage"
        storage_dir.mkdir()
        message_dir = storage_dir / "message" / "invalid"
        message_dir.mkdir(parents=True)
        message_file = message_dir / "message.json"
        message_file.write_text("not valid json {{{")

        source = OpenCodeSource(storage_root=storage_dir)
        conv = source.parse(message_file)

        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 0
        assert conv.content_hash == ""

    def test_parse_missing_key(self, temp_dir):
        """Test parsing a file with missing required keys."""
        storage_dir = temp_dir / "storage"
        storage_dir.mkdir()
        message_dir = storage_dir / "message" / "partial"
        message_dir.mkdir(parents=True)
        message_file = message_dir / "message.json"
        message_file.write_text(json.dumps({"id": "msg3"}))

        source = OpenCodeSource(storage_root=storage_dir)
        conv = source.parse(message_file)

        assert isinstance(conv, RawConversation)
        assert len(conv.messages) == 1
        assert conv.messages[0].content == ""
