"""Unit tests for _flatten module."""

from corpus_forge.sources._flatten import flatten_message
from corpus_forge.sources.base import RawMessage


class TestFlattenMessage:
    """Tests for flatten_message function."""

    def test_flatten_text_blocks(self):
        """Test flattening text content blocks."""
        blocks = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world!"},
        ]
        result = flatten_message(blocks, source="claude_code")
        assert isinstance(result, RawMessage)
        assert result.content == "Hello world!"
        assert result.tool_calls is None
        assert result.tool_results is None
        assert result.metadata == {"source": "claude_code"}

    def test_flatten_tool_use_blocks(self):
        """Test flattening tool use blocks."""
        blocks = [
            {"type": "tool_use", "id": "tc1", "name": "read_file", "input": {"path": "/test"}},
            {"type": "text", "text": "Done"},
        ]
        result = flatten_message(blocks, source="claude_code")
        assert result.content == "Done"
        assert result.tool_calls == [{"id": "tc1", "name": "read_file", "input": {"path": "/test"}}]
        assert result.tool_results is None

    def test_flatten_tool_result_blocks(self):
        """Test flattening tool result blocks."""
        blocks = [
            {"type": "tool_result", "tool_use_id": "tc1", "content": "file contents"},
            {"type": "text", "text": "Result"},
        ]
        result = flatten_message(blocks, source="claude_code")
        assert result.content == "Result"
        assert result.tool_calls is None
        assert result.tool_results == [
            {"tool_use_id": "tc1", "content": "file contents", "is_error": False}
        ]

    def test_flatten_thinking_blocks(self):
        """Test flattening thinking type blocks (OpenCode)."""
        blocks = [
            {"type": "thinking", "content": "Let me think..."},
            {"type": "text", "text": "Answer"},
        ]
        result = flatten_message(blocks, source="opencode")
        assert "[thinking] Let me think..." in result.content
        assert "Answer" in result.content

    def test_flatten_comment_blocks(self):
        """Test flattening comment type blocks (OpenCode)."""
        blocks = [
            {"type": "comment", "content": "Note about this"},
            {"type": "text", "text": "Answer"},
        ]
        result = flatten_message(blocks, source="opencode")
        assert "[comment] Note about this" in result.content
        assert "Answer" in result.content

    def test_flatten_empty_blocks(self):
        """Test flattening empty blocks list."""
        result = flatten_message([], source="claude_code")
        assert result.content == ""
        assert isinstance(result, RawMessage)

    def test_flatten_mixed_blocks(self):
        """Test flattening mixed block types."""
        blocks = [
            {"type": "text", "text": "Start"},
            {"type": "tool_use", "id": "tc1", "name": "cmd", "input": {}},
            {"type": "tool_result", "tool_use_id": "tc1", "content": "output"},
            {"type": "text", "text": "End"},
        ]
        result = flatten_message(blocks, source="claude_code")
        assert result.content == "StartEnd"
        assert len(result.tool_calls) == 1
        assert len(result.tool_results) == 1

    def test_flatten_whitespace_trimming(self):
        """Test that content is stripped of leading/trailing whitespace."""
        blocks = [
            {"type": "text", "text": "  Hello  "},
            {"type": "text", "text": "  World  "},
        ]
        result = flatten_message(blocks, source="claude_code")
        # strip() is called on the joined result, not individual parts
        assert result.content == "Hello    World"

    def test_flatten_missing_text_key(self):
        """Test that missing text key returns empty string."""
        blocks = [
            {"type": "text"},
            {"type": "text", "text": "Hello"},
        ]
        result = flatten_message(blocks, source="claude_code")
        assert result.content == "Hello"

    def test_flatten_role_is_assistant(self):
        """Test that default role is assistant."""
        blocks = [{"type": "text", "text": "Hello"}]
        result = flatten_message(blocks, source="claude_code")
        assert result.role == "assistant"

    def test_flatten_uuids_are_none(self):
        """Test that external_uuid and parent_uuid are None."""
        blocks = [{"type": "text", "text": "Hello"}]
        result = flatten_message(blocks, source="claude_code")
        assert result.external_uuid is None
        assert result.parent_uuid is None

    def test_flatten_ts_is_none(self):
        """Test that ts is None."""
        blocks = [{"type": "text", "text": "Hello"}]
        result = flatten_message(blocks, source="claude_code")
        assert result.ts is None
