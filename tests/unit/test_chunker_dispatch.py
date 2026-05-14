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
