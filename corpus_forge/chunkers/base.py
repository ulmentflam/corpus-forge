"""Chunker base class for corpus-forge."""

from dataclasses import dataclass


@dataclass
class TextChunk:
    """A chunk of text with metadata."""

    text: str
    heading: str | None = None
    role: str | None = None
    token_count: int | None = None
    metadata: dict | None = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class Chunker:
    """
    Chunker base — owns size-bounding + overlap; subclass overrides only the split-point predicate.
    """

    def __init__(self, max_chars: int = 1500, overlap: int = 200):
        if overlap >= max_chars:
            raise ValueError("Overlap must be less than max_chars")
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk(self, text: str) -> list[TextChunk]:
        """
        Split text into chunks using size-bounding with overlap.
        Subclasses override only `should_split_here()` to determine split points.
        """
        if not text:
            return []

        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            # Find the end position for this chunk
            end = start + self.max_chars

            # If we're not at the end of text, try to find a good split point
            if end < text_len:
                # Look backwards from max_chars position for a good split point
                split_pos = self._find_split_point(text, start, end)
                if split_pos is not None and split_pos > start:
                    end = split_pos
            else:
                # We're at the end of text
                end = text_len

            # Extract the chunk
            chunk_text = text[start:end]

            # Create chunk object (subclasses can customize this)
            chunk = self._create_chunk(chunk_text, start, end)
            chunks.append(chunk)

            # Move start position forward, accounting for overlap
            if end >= text_len:
                break
            start = end - self.overlap

            # Ensure we make progress
            start = max(start, 0)

        return chunks

    def _find_split_point(self, text: str, start: int, max_end: int) -> int | None:  # noqa: ARG002 — start unused in default impl; subclasses use it
        """
        Find the best split point between start and max_end.
        Subclasses should override this to implement their splitting logic.
        """
        # Default: split at max_end
        return max_end if max_end <= len(text) else len(text)

    def _create_chunk(self, text: str, _start: int, _end: int) -> TextChunk:
        """Create a chunk object from text."""
        return TextChunk(text=text)


class MarkdownChunker(Chunker):
    """Markdown chunker: heading-aware split, paragraph-bounded, soft char cap, overlap."""

    @staticmethod
    def _extract_heading(text: str) -> str | None:
        """Return the text of the first markdown heading found in *text*, or None."""
        for line in text.splitlines():
            stripped = line.lstrip("#").strip()
            if line.startswith("#") and stripped:
                return stripped
        return None

    def _create_chunk(self, text: str, _start: int, _end: int) -> TextChunk:
        """Create a chunk with the heading extracted from the chunk text."""
        return TextChunk(text=text, heading=self._extract_heading(text))

    def _find_split_point(self, text: str, start: int, max_end: int) -> int | None:
        """
        Find split point respecting markdown structure:
        - Prefer to split at paragraph boundaries (double newline)
        - Then at sentence boundaries (period + space)
        - Then at word boundaries (space)
        - Avoid splitting in the middle of words if possible
        """
        # Look at the region we're considering for splitting
        search_region = text[start:max_end]

        # If we're already at or near the end, just split here
        if len(search_region) < self.max_chars // 2:
            return max_end

        # Look for paragraph break (double newline) going backwards from max_end
        double_newline_pos = text.rfind("\n\n", start, max_end)
        if double_newline_pos != -1 and double_newline_pos > start:
            # Found paragraph break, split after it
            return double_newline_pos + 2

        # Look for sentence break (period + space or newline)
        for i in range(max_end - 1, start, -1):
            if text[i] == "." and (i + 1 >= len(text) or text[i + 1] in " \n\t"):
                return i + 1  # Split after the period

        # Look for word boundary (space or newline)
        for i in range(max_end - 1, start, -1):
            if text[i] in " \n\t":
                return i  # Split at the whitespace

        # No good split point found, split at max_end
        return max_end


class ConversationChunker(Chunker):
    """Conversation chunker: per_message or sliding_window modes."""

    def __init__(self, mode: str = "per_message", **kwargs):
        super().__init__(**kwargs)
        self.mode = mode
        if mode not in ("per_message", "sliding_window"):
            raise ValueError("Mode must be 'per_message' or 'sliding_window'")

    def _find_split_point(self, text: str, start: int, max_end: int) -> int | None:  # noqa: ARG002 — Conversation overrides chunk() entirely; this is unreachable
        """Not used — ConversationChunker overrides chunk() entirely."""
        return None

    def chunk(self, texts: list[str]) -> list[TextChunk]:  # type: ignore[bad-override-param-name]  # intentional: chat input is list[str]
        """
        Override base chunk method to handle list of texts (messages).
        """
        if not texts:
            return []

        if self.mode == "per_message":
            return self._chunk_per_message(texts)
        else:  # sliding_window
            return self._chunk_sliding_window(texts)

    def _chunk_per_message(self, texts: list[str]) -> list[TextChunk]:
        """One chunk per message."""
        chunks = []
        for _i, text in enumerate(texts):
            if text.strip():  # Only create chunks for non-empty text
                chunk = TextChunk(text=text)
                chunks.append(chunk)
        return chunks

    def _chunk_sliding_window(self, texts: list[str]) -> list[TextChunk]:
        """Sliding window over messages."""
        # This would use window_turns and stride_turns from config
        # For now, simplified implementation
        window_size = 3  # Would come from config
        stride = 2  # Would come from config

        chunks = []
        start = 0

        while start < len(texts):
            end = min(start + window_size, len(texts))
            # Join messages in window with newlines
            window_text = "\n\n".join(texts[start:end])

            if window_text.strip():
                chunk = TextChunk(text=window_text)
                chunks.append(chunk)

            if end >= len(texts):
                break
            start += stride

        return chunks
