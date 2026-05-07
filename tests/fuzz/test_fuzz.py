"""Property-based (fuzz) tests using hypothesis."""

from hypothesis import given, settings, strategies as st

import pytest

from corpus_forge.chunkers.base import MarkdownChunker, ConversationChunker, TextChunk
from corpus_forge.identity import content_hash
from corpus_forge.sources.base import RawDocument, RawConversation, RawMessage

pytestmark = pytest.mark.fuzz


# ── MarkdownChunker (base class) ─────────────────────────────────────────────


class TestMarkdownChunkerProperties:
    """Property tests for the MarkdownChunker class."""

    @given(
        text=st.text(min_size=1, max_size=100, alphabet=st.characters(blacklist_categories=("Cs",)),),
        max_chars=st.integers(min_value=10, max_value=500),
        overlap=st.integers(min_value=0, max_value=199).map(lambda o: min(o, 9)),
    )
    @settings(max_examples=10)
    def test_all_chunks_non_empty(self, text, max_chars, overlap):
        chunker = MarkdownChunker(max_chars=max_chars, overlap=overlap)
        chunks = chunker.chunk(text)
        for chunk in chunks:
            assert len(chunk.text) > 0, f"Empty chunk body in text of length {len(text)}"

    @given(
        text=st.text(min_size=10, max_size=100, alphabet=st.characters(blacklist_categories=("Cs",)),),
        max_chars=st.integers(min_value=10, max_value=500),
        overlap=st.integers(min_value=0, max_value=199).map(lambda o: min(o, 9)),
    )
    @settings(max_examples=10)
    def test_chunk_size_bound(self, text, max_chars, overlap):
        chunker = MarkdownChunker(max_chars=max_chars, overlap=overlap)
        chunks = chunker.chunk(text)
        for chunk in chunks:
            assert len(chunk.text) <= max_chars + overlap, (
                f"Chunk {len(chunk.text)} exceeds max_chars({max_chars}) + overlap({overlap})"
            )

    @given(
        text=st.text(min_size=10, max_size=100, alphabet=st.characters(blacklist_categories=("Cs",)),),
        max_chars=st.integers(min_value=10, max_value=500),
        overlap=st.integers(min_value=0, max_value=199).map(lambda o: min(o, 9)),
    )
    @settings(max_examples=10)
    def test_chunks_are_text_chunks(self, text, max_chars, overlap):
        chunker = MarkdownChunker(max_chars=max_chars, overlap=overlap)
        chunks = chunker.chunk(text)
        for chunk in chunks:
            assert isinstance(chunk, TextChunk)
            assert isinstance(chunk.text, str)

    @given(
        text=st.text(alphabet=st.characters(blacklist_categories=("Cs",)),),
        max_chars=st.integers(min_value=10, max_value=500),
        overlap=st.integers(min_value=0, max_value=199).map(lambda o: min(o, 9)),
    )
    @settings(max_examples=10)
    def test_empty_text_returns_empty_list(self, text, max_chars, overlap):
        chunker = MarkdownChunker(max_chars=max_chars, overlap=overlap)
        chunks = chunker.chunk("")
        assert chunks == []

    @given(
        text=st.text(min_size=1, max_size=100, alphabet=st.characters(blacklist_categories=("Cs",)),),
        max_chars=st.integers(min_value=100, max_value=500),
        overlap=st.integers(min_value=0, max_value=199).map(lambda o: min(o, 99)),
    )
    @settings(max_examples=10)
    def test_max_chars_larger_than_text_returns_single_chunk(self, text, max_chars, overlap):
        """If max_chars >= len(text), should return at most one chunk."""
        chunker = MarkdownChunker(max_chars=max_chars, overlap=overlap)
        chunks = chunker.chunk(text)
        if len(text.strip()) > 0:
            assert len(chunks) <= 1

    def test_overlap_greater_than_max_chars_raises(self):
        """Overlap >= max_chars should always raise."""
        with pytest.raises(ValueError):
            MarkdownChunker(max_chars=100, overlap=100)
        with pytest.raises(ValueError):
            MarkdownChunker(max_chars=100, overlap=200)


# ── ConversationChunker ──────────────────────────────────────────────────────


class TestConversationChunkerProperties:
    @given(
        texts=st.lists(st.text(min_size=1, max_size=500), min_size=1, max_size=20),
        mode=st.just("per_message") | st.just("sliding_window"),
    )
    @settings(max_examples=50)
    def test_chunker_returns_text_chunks(self, texts, mode):
        chunker = ConversationChunker(mode=mode)
        chunks = chunker.chunk(texts)
        for chunk in chunks:
            assert isinstance(chunk, TextChunk)
            assert isinstance(chunk.text, str)
            assert len(chunk.text) > 0

    @given(
        texts=st.lists(st.text(min_size=1, max_size=500), min_size=1, max_size=20),
        mode=st.just("per_message") | st.just("sliding_window"),
    )
    @settings(max_examples=50)
    def test_chunker_empty_input(self, texts, mode):
        chunker = ConversationChunker(mode=mode)
        chunks = chunker.chunk([])
        assert chunks == []

    @given(
        texts=st.lists(st.text(min_size=1, max_size=500), min_size=1, max_size=20),
        mode=st.just("per_message") | st.just("sliding_window"),
    )
    @settings(max_examples=50)
    def test_chunker_all_non_empty(self, texts, mode):
        chunker = ConversationChunker(mode=mode)
        chunks = chunker.chunk(texts)
        for chunk in chunks:
            assert chunk.text.strip() != ""

    @given(
        mode=st.just("per_message") | st.just("sliding_window"),
    )
    @settings(max_examples=50)
    def test_chunker_invalid_mode_raises(self, mode):
        with pytest.raises(ValueError):
            ConversationChunker(mode="invalid_mode")


# ── Identity / hashing ───────────────────────────────────────────────────────


class TestIdentityProperties:
    @given(
        content=st.binary(min_size=1, max_size=10000),
    )
    @settings(max_examples=50)
    def test_content_hash_deterministic(self, content):
        h1 = content_hash(content)
        h2 = content_hash(content)
        assert h1 == h2

    @given(
        content_a=st.binary(min_size=1, max_size=10000),
        content_b=st.binary(min_size=1, max_size=10000),
    )
    @settings(max_examples=50)
    def test_different_content_different_hash(self, content_a, content_b):
        if content_a != content_b:
            h1 = content_hash(content_a)
            h2 = content_hash(content_b)
            assert h1 != h2

    @given(
        content=st.binary(min_size=1, max_size=10000),
    )
    @settings(max_examples=50)
    def test_content_hash_is_hex_string(self, content):
        h = content_hash(content)
        assert isinstance(h, str)
        assert all(c in "0123456789abcdef" for c in h)


# ── RawDocument / RawConversation dataclasses ────────────────────────────────


class TestDataclassProperties:
    @given(
        source_uri=st.text(min_size=1, max_size=100),
        content_hash=st.text(min_size=1, max_size=64),
        text=st.text(min_size=1, max_size=1000),
    )
    @settings(max_examples=50)
    def test_raw_document_frozen(self, source_uri, content_hash, text):
        doc = RawDocument(
            source_uri=source_uri,
            content_hash=content_hash,
            text=text,
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        with pytest.raises(Exception):
            doc.source_uri = "changed"

    @given(
        source_uri=st.text(min_size=1, max_size=100),
        content_hash=st.text(min_size=1, max_size=64),
    )
    @settings(max_examples=50)
    def test_raw_conversation_frozen(self, source_uri, content_hash):
        conv = RawConversation(
            source_uri=source_uri,
            external_id="ext1",
            content_hash=content_hash,
            title="Test",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[
                RawMessage(
                    external_uuid="m1",
                    parent_uuid=None,
                    role="user",
                    content="Hi",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                )
            ],
            metadata={},
            labels=[],
        )
        with pytest.raises(Exception):
            conv.source_uri = "changed"
