"""Unit tests for D-05: ChunkerDispatcher in corpus_forge.ingest.

Wave 0 of the multi-format milestone. The dispatcher is keyed by
``RawDocument.metadata["chunker_hint"]`` and falls back to the existing
per-source chunker resolution when no hint is present — existing sources
(``markdown_vault``, ``claude_code``, ``opencode``) keep working.
"""

from __future__ import annotations

import pytest

from corpus_forge.chunkers.base import Chunker, TextChunk
from corpus_forge.chunkers.conversation import ConversationChunker
from corpus_forge.chunkers.markdown import MarkdownChunker
from corpus_forge.ingest import ChunkerDispatcher

# ── Construction ─────────────────────────────────────────────────────────


def test_dispatcher_default_instantiates():
    """No-arg construction should produce a usable dispatcher."""
    d = ChunkerDispatcher()
    assert d is not None


def test_dispatcher_for_hint_returns_chunker_instance():
    d = ChunkerDispatcher()
    chunker = d.for_hint("markdown")
    assert isinstance(chunker, Chunker)


# ── Hint → Chunker mapping ───────────────────────────────────────────────


def test_dispatcher_markdown_hint_returns_markdown_chunker():
    d = ChunkerDispatcher()
    assert isinstance(d.for_hint("markdown"), MarkdownChunker)


def test_dispatcher_conversation_hint_returns_conversation_chunker():
    d = ChunkerDispatcher()
    assert isinstance(d.for_hint("conversation"), ConversationChunker)


def test_dispatcher_passthrough_hint_returns_a_chunker():
    """Passthrough hint produces a chunker that emits the input as one
    chunk (or size-bounded segments) — implementation detail, but it
    must at minimum be a Chunker instance."""
    d = ChunkerDispatcher()
    chunker = d.for_hint("passthrough")
    assert isinstance(chunker, Chunker)
    chunks = chunker.chunk("hello world")
    assert isinstance(chunks, list)
    assert len(chunks) >= 1
    assert isinstance(chunks[0], TextChunk)
    # The whole input must survive somewhere in the output.
    joined = "".join(c.text for c in chunks)
    assert "hello world" in joined


def test_dispatcher_code_hint_returns_a_chunker_or_falls_back():
    """When the [code] extra isn't installed the dispatcher must still
    return *something* sensible — the byte-line long-tail fallback is
    fine. Failing here would break ingest on a clean install."""
    d = ChunkerDispatcher()
    try:
        chunker = d.for_hint("code")
    except ImportError:
        pytest.skip("CodeChunker (D-02) not yet landed")
    assert isinstance(chunker, Chunker)


def test_dispatcher_unknown_hint_raises():
    d = ChunkerDispatcher()
    with pytest.raises((ValueError, KeyError)):
        d.for_hint("does-not-exist")


# ── Backwards compatibility: dispatch_for(raw, fallback) ─────────────────


class _FakeRaw:
    """Bare-minimum stand-in for RawDocument exposing only what the
    dispatcher reads."""

    def __init__(self, metadata: dict | None):
        self.metadata = metadata or {}


def test_dispatch_for_uses_hint_when_present():
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()  # would be ignored
    raw = _FakeRaw(metadata={"chunker_hint": "passthrough"})
    chunker = d.dispatch_for(raw, fallback=fallback)
    assert isinstance(chunker, Chunker)
    # The fallback was markdown; the hint was passthrough — they must differ.
    assert chunker is not fallback


def test_dispatch_for_falls_back_when_no_hint():
    """Old sources (markdown_vault / claude_code / opencode) do not set
    chunker_hint — the dispatcher must return the supplied fallback."""
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()
    raw = _FakeRaw(metadata={})
    chunker = d.dispatch_for(raw, fallback=fallback)
    assert chunker is fallback


def test_dispatch_for_falls_back_when_metadata_is_none():
    """Defensive: some RawDocument constructors might pass metadata=None."""
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()
    raw = _FakeRaw(metadata=None)
    chunker = d.dispatch_for(raw, fallback=fallback)
    assert chunker is fallback


def test_dispatch_for_falls_back_on_empty_string_hint():
    """An explicit empty-string hint should be treated as no-hint, not
    as an unknown hint."""
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()
    raw = _FakeRaw(metadata={"chunker_hint": ""})
    chunker = d.dispatch_for(raw, fallback=fallback)
    assert chunker is fallback


# ── No-regression: get_chunker_for_source still callable ─────────────────


def test_get_chunker_for_source_unchanged_surface():
    """The existing helper at corpus_forge.ingest.get_chunker_for_source
    must still be importable and callable — D-05 is additive."""
    from corpus_forge.ingest import get_chunker_for_source

    assert callable(get_chunker_for_source)


# ── Phase F (F-02): class-aware routing ──────────────────────────────────


def test_dispatcher_for_class_code_returns_code_chunker():
    """class=code → CodeChunker (same as Phase D 'code' hint)."""
    from corpus_forge.chunkers.code import CodeChunker

    d = ChunkerDispatcher()
    chunker = d.for_class("code")
    assert isinstance(chunker, CodeChunker)


def test_dispatcher_for_class_chat_returns_conversation_chunker():
    d = ChunkerDispatcher()
    chunker = d.for_class("chat")
    assert isinstance(chunker, ConversationChunker)


def test_dispatcher_for_class_reference_returns_passthrough_chunker():
    from corpus_forge.chunkers.base import PassthroughChunker

    d = ChunkerDispatcher()
    chunker = d.for_class("reference")
    assert isinstance(chunker, PassthroughChunker)


@pytest.mark.parametrize("class_value", ["book", "textbook", "paper", "article", "note", "other"])
def test_dispatcher_for_class_prose_routes_to_cdc(class_value: str):
    """Every prose class lands on the new CDCChunker (F-01)."""
    from corpus_forge.chunkers.cdc import CDCChunker

    d = ChunkerDispatcher()
    chunker = d.for_class(class_value)
    assert isinstance(chunker, CDCChunker)


def test_dispatcher_for_class_unknown_raises():
    d = ChunkerDispatcher()
    with pytest.raises(ValueError):
        d.for_class("not-a-class")


# ── Phase F: dispatch_for resolution order (class_hint > chunker_hint > fallback) ─


def test_dispatch_for_class_hint_wins_over_chunker_hint():
    """When both ``class_hint`` and ``chunker_hint`` are present,
    ``class_hint`` takes precedence — it's the post-classification
    signal, more authoritative than the source-emitted format hint."""
    from corpus_forge.chunkers.cdc import CDCChunker

    d = ChunkerDispatcher()
    fallback = MarkdownChunker()  # never used here
    # class=book → CDC; chunker_hint="passthrough" would lose.
    raw = _FakeRaw(metadata={"class_hint": "book", "chunker_hint": "passthrough"})
    chunker = d.dispatch_for(raw, fallback=fallback)
    assert isinstance(chunker, CDCChunker)


def test_dispatch_for_class_hint_routes_chat_to_conversation():
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()
    raw = _FakeRaw(metadata={"class_hint": "chat"})
    chunker = d.dispatch_for(raw, fallback=fallback)
    assert isinstance(chunker, ConversationChunker)


def test_dispatch_for_empty_class_hint_falls_through_to_chunker_hint():
    """Empty-string class_hint mirrors empty-string chunker_hint
    semantics — treat as absent, not unknown."""
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()
    raw = _FakeRaw(metadata={"class_hint": "", "chunker_hint": "passthrough"})
    chunker = d.dispatch_for(raw, fallback=fallback)
    # Passthrough hint wins because class_hint is empty.
    from corpus_forge.chunkers.base import PassthroughChunker

    assert isinstance(chunker, PassthroughChunker)


def test_dispatch_for_chunker_hint_still_works_without_class_hint():
    """Backwards-compat: pre-Phase-F ingest sets only ``chunker_hint``,
    no ``class_hint`` yet — must keep behaving as before."""
    d = ChunkerDispatcher()
    fallback = MarkdownChunker()
    raw = _FakeRaw(metadata={"chunker_hint": "passthrough"})
    chunker = d.dispatch_for(raw, fallback=fallback)
    from corpus_forge.chunkers.base import PassthroughChunker

    assert isinstance(chunker, PassthroughChunker)


def test_dispatch_for_caches_for_class_lookups():
    """``for_class`` reuses the same chunker instance across calls
    — important so we don't reinstantiate (e.g. CodeChunker, which pulls
    tree-sitter) for every prose document."""
    d = ChunkerDispatcher()
    a = d.for_class("book")
    b = d.for_class("book")
    assert a is b
