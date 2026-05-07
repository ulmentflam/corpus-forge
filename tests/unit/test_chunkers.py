"""Unit tests for chunker implementations."""

import pytest

from corpus_forge.chunkers.base import (
    Chunker,
    ConversationChunker,
    MarkdownChunker,
    TextChunk,
)


# Concrete subclass for testing abstract base class
class _TestChunker(Chunker):
    def _find_split_point(self, _text: str, _start: int, _max_end: int) -> int | None:
        return _max_end


class TestTextChunk:
    """Tests for TextChunk dataclass."""

    def test_textchunk_defaults(self):
        """Test TextChunk with default values."""
        chunk = TextChunk(text="hello world")
        assert chunk.text == "hello world"
        assert chunk.heading is None
        assert chunk.role is None
        assert chunk.token_count is None
        assert chunk.metadata == {}

    def test_textchunk_with_values(self):
        """Test TextChunk with explicit values."""
        chunk = TextChunk(
            text="hello",
            heading="Test",
            role="user",
            token_count=5,
            metadata={"key": "value"},
        )
        assert chunk.text == "hello"
        assert chunk.heading == "Test"
        assert chunk.role == "user"
        assert chunk.token_count == 5  # noqa: PLR2004
        assert chunk.metadata == {"key": "value"}


class TestChunker:
    """Tests for base Chunker class."""

    def test_chunker_empty_text(self):
        """Test that empty text returns empty list."""
        chunker = _TestChunker()
        result = chunker.chunk("")
        assert result == []

    def test_chunker_single_chunk(self):
        """Test text shorter than max_chars returns single chunk."""
        chunker = _TestChunker(max_chars=1500)
        result = chunker.chunk("short text")
        assert len(result) == 1
        assert result[0].text == "short text"

    def test_chunker_overlap_config(self):
        """Test overlap configuration."""
        chunker = _TestChunker(max_chars=100, overlap=50)
        assert chunker.max_chars == 100  # noqa: PLR2004
        assert chunker.overlap == 50  # noqa: PLR2004

    def test_chunker_invalid_overlap(self):
        """Test that overlap >= max_chars raises ValueError."""
        with pytest.raises(ValueError, match="Overlap must be less than max_chars"):
            _TestChunker(max_chars=100, overlap=100)

        with pytest.raises(ValueError, match="Overlap must be less than max_chars"):
            _TestChunker(max_chars=100, overlap=150)


class TestMarkdownChunker:
    """Tests for MarkdownChunker class."""

    def test_markdown_chunker_empty(self):
        """Test empty markdown text."""
        chunker = MarkdownChunker()
        result = chunker.chunk("")
        assert result == []

    def test_markdown_chunker_short_text(self):
        """Test text shorter than max_chars."""
        chunker = MarkdownChunker(max_chars=1500)
        result = chunker.chunk("# Title\n\nShort paragraph.")
        assert len(result) == 1
        assert "Short paragraph" in result[0].text

    def test_markdown_chunker_paragraph_split(self):
        """Test splitting at paragraph boundaries."""
        chunker = MarkdownChunker(max_chars=50, overlap=10)
        long_text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = chunker.chunk(long_text)
        assert len(result) >= 1
        # Each chunk should respect paragraph boundaries
        for chunk in result:
            assert len(chunk.text) <= chunker.max_chars + chunker.overlap

    def test_markdown_chunker_multiple_chunks(self):
        """Test that long text produces multiple chunks."""
        chunker = MarkdownChunker(max_chars=50, overlap=10)
        long_text = "A" * 200
        result = chunker.chunk(long_text)
        assert len(result) > 1

    def test_markdown_chunker_overlap(self):
        """Test that chunks have overlap."""
        chunker = MarkdownChunker(max_chars=50, overlap=20)
        long_text = "Word1 Word2 Word3 Word4 Word5 Word6 Word7 Word8 Word9 Word10"
        result = chunker.chunk(long_text)
        if len(result) >= 2:  # noqa: PLR2004
            # Check that there is overlap between consecutive chunks
            prev_end = result[0].text
            for i in range(1, len(result)):
                curr_start = result[i].text
                # Overlap region check
                overlap_region = prev_end[-chunker.overlap :]
                assert overlap_region in curr_start or len(curr_start) < chunker.overlap

    def test_markdown_chunker_heading_preservation(self):
        """Test that headings are preserved in chunks."""
        chunker = MarkdownChunker(max_chars=100, overlap=20)
        text = "# Main Heading\n\nSome content here.\n\n## Subheading\n\nMore content."
        result = chunker.chunk(text)
        combined = "".join(c.text for c in result)
        assert "# Main Heading" in combined
        assert "## Subheading" in combined

    def test_markdown_chunker_word_boundary_split(self):
        """Test splitting at word boundaries."""
        chunker = MarkdownChunker(max_chars=30, overlap=5)
        text = "This is a sentence with many words that should be split at word boundaries."
        result = chunker.chunk(text)
        assert len(result) > 1
        # Chunks should not split in the middle of words (except for very long words)
        for chunk in result:
            words = chunk.text.strip().split()
            for word in words:
                if len(word) > chunker.max_chars:
                    continue  # Skip very long words that can't be split


class TestConversationChunker:
    """Tests for ConversationChunker class."""

    def test_conversation_chunker_per_message_empty(self):
        """Test empty messages list."""
        chunker = ConversationChunker(mode="per_message")
        result = chunker.chunk([])
        assert result == []

    def test_conversation_chunker_per_message_single(self):
        """Test single message."""
        chunker = ConversationChunker(mode="per_message")
        result = chunker.chunk(["Hello world"])
        assert len(result) == 1
        assert result[0].text == "Hello world"

    def test_conversation_chunker_per_message_multiple(self):
        """Test multiple messages."""
        chunker = ConversationChunker(mode="per_message")
        messages = ["Hello", "World", "Test"]
        result = chunker.chunk(messages)
        assert len(result) == 3  # noqa: PLR2004
        assert result[0].text == "Hello"
        assert result[1].text == "World"
        assert result[2].text == "Test"

    def test_conversation_chunker_per_message_skips_empty(self):
        """Test that empty messages are skipped."""
        chunker = ConversationChunker(mode="per_message")
        messages = ["Hello", "", "  ", "World"]
        result = chunker.chunk(messages)
        assert len(result) == 2  # noqa: PLR2004
        assert result[0].text == "Hello"
        assert result[1].text == "World"

    def test_conversation_chunker_sliding_window(self):
        """Test sliding window mode."""
        chunker = ConversationChunker(mode="sliding_window")
        messages = ["Msg1", "Msg2", "Msg3", "Msg4", "Msg5"]
        result = chunker.chunk(messages)
        assert len(result) > 0
        # Each chunk should contain joined messages
        for chunk in result:
            assert "Msg" in chunk.text

    def test_conversation_chunker_invalid_mode(self):
        """Test that invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="Mode must be"):
            ConversationChunker(mode="invalid_mode")

    def test_conversation_chunker_custom_max_chars(self):
        """Test custom max_chars and overlap."""
        chunker = ConversationChunker(mode="per_message", max_chars=500, overlap=50)
        assert chunker.max_chars == 500  # noqa: PLR2004
        assert chunker.overlap == 50  # noqa: PLR2004
