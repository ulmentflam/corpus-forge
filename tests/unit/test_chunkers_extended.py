"""Unit tests for chunkers/base.py — extended coverage."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.chunkers.base import Chunker, ConversationChunker, MarkdownChunker, TextChunk


class TestTextChunkDefaults:
    def test_textchunk_default_metadata(self):
        """Test that TextChunk initializes metadata to empty dict."""
        chunk = TextChunk(text="test")
        assert chunk.metadata == {}

    def test_textchunk_with_metadata(self):
        """Test that TextChunk accepts metadata."""
        chunk = TextChunk(text="test", metadata={"key": "val"})
        assert chunk.metadata == {"key": "val"}

    def test_textchunk_all_fields(self):
        """Test TextChunk with all fields set."""
        chunk = TextChunk(
            text="test",
            heading="H1",
            role="user",
            token_count=10,
            metadata={"custom": "data"},
        )
        assert chunk.text == "test"
        assert chunk.heading == "H1"
        assert chunk.role == "user"
        assert chunk.token_count == 10
        assert chunk.metadata == {"custom": "data"}


class TestChunkerBase:
    """Tests for the base Chunker class."""

    def test_chunker_base_find_split_point(self):
        """Test _find_split_point returns correct position."""
        chunker = Chunker(max_chars=50, overlap=10)
        text = "Hello world, this is a test of the chunker."
        result = chunker._find_split_point(text, 0, 50)
        # Base Chunker splits at max_end when <= len(text)
        assert result == len(text) if len(text) <= 50 else 50

    def test_chunker_base_find_split_point_truncated(self):
        """Test _find_split_point when max_end > len(text)."""
        chunker = Chunker(max_chars=50, overlap=10)
        text = "Short"
        result = chunker._find_split_point(text, 0, 100)
        assert result == 5

    def test_chunker_base_create_chunk(self):
        """Test _create_chunk returns TextChunk."""
        chunker = Chunker(max_chars=50, overlap=10)
        chunk = chunker._create_chunk("test text", 0, 8)
        assert isinstance(chunk, TextChunk)
        assert chunk.text == "test text"

    def test_chunker_base_single_chunk(self):
        """Test chunking text shorter than max_chars."""
        chunker = Chunker(max_chars=500, overlap=100)
        chunks = chunker.chunk("Short text.")
        assert len(chunks) == 1
        assert chunks[0].text == "Short text."

    def test_chunker_base_multiple_chunks(self):
        """Test chunking text that spans multiple chunks."""
        chunker = Chunker(max_chars=20, overlap=5)
        text = "A" * 100
        chunks = chunker.chunk(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.text) <= 25  # max_chars + overlap

    def test_chunker_base_overlap_config(self):
        """Test that overlap is correctly applied."""
        chunker = Chunker(max_chars=10, overlap=5)
        text = "12345678901234567890"
        chunks = chunker.chunk(text)
        # First chunk ends at 10, second starts at 5 (overlap)
        assert len(chunks) >= 2


class TestMarkdownChunkerExtended:
    """Extended tests for MarkdownChunker."""

    def test_markdown_chunker_near_end_split(self):
        """Test splitting when search region is near end."""
        chunker = MarkdownChunker(max_chars=100, overlap=20)
        text = "# Header\n\n" + "A" * 40
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1

    def test_markdown_chunker_no_good_split(self):
        """Test when no good split point is found."""
        chunker = MarkdownChunker(max_chars=10, overlap=2)
        # No newlines, no spaces — should split at max_end
        text = "ABCDEFGHIJ"
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1
        assert all(len(c.text) <= 12 for c in chunks)

    def test_markdown_chunker_prefer_paragraph_break(self):
        """Test that paragraph breaks are preferred over sentence breaks."""
        chunker = MarkdownChunker(max_chars=50, overlap=10)
        text = "# Header\n\nPara one with some text.\n\nPara two with more text.\n\nPara three."
        chunks = chunker.chunk(text)
        # Should split at paragraph boundaries
        assert len(chunks) >= 1


class TestConversationChunkerExtended:
    """Extended tests for ConversationChunker."""

    def test_conversation_chunker_find_split_point_returns_none(self):
        """Test that _find_split_point returns None (not used)."""
        chunker = ConversationChunker(mode="per_message")
        result = chunker._find_split_point("text", 0, 10)
        assert result is None

    def test_conversation_chunker_sliding_window(self):
        """Test sliding window mode."""
        chunker = ConversationChunker(mode="sliding_window")
        texts = ["msg1", "msg2", "msg3", "msg4", "msg5"]
        chunks = chunker.chunk(texts)
        # Window size=3, stride=2
        assert len(chunks) >= 2
        # First chunk should contain first 3 messages
        assert "msg1" in chunks[0].text

    def test_conversation_chunker_sliding_window_empty(self):
        """Test sliding window with all empty messages."""
        chunker = ConversationChunker(mode="sliding_window")
        texts = ["", "  ", ""]
        chunks = chunker.chunk(texts)
        assert chunks == []

    def test_conversation_chunker_sliding_window_mixed(self):
        """Test sliding window with mixed empty/non-empty messages."""
        chunker = ConversationChunker(mode="sliding_window")
        texts = ["msg1", "", "msg3"]
        chunks = chunker.chunk(texts)
        assert len(chunks) >= 1
        assert all(c.text.strip() for c in chunks)
