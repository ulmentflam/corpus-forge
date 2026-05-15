"""Unit tests for F-01: ``CDCChunker`` (FastCDC rolling hash).

The chunker replaces positional ``MarkdownChunker``/``PassthroughChunker``
slicing for prose classes. Boundaries are content-defined: small edits
ripple ≤ 2-3 chunks (see :mod:`tests.unit.test_cdc_stability` for the
property-based stability proofs).

Coverage notes:
- Empty / small-text edge cases.
- Determinism (same input → same chunks).
- Size-bound contract (between ``min_size`` and ``max_size`` UTF-8 bytes,
  except for the very last chunk which may fall below ``min_size``).
- ``cdc_fingerprint`` (hex string) and ``byte_range`` metadata shape.
- UTF-8 boundary safety: multi-byte codepoints (Chinese / Arabic / emoji)
  never produce a ``UnicodeDecodeError`` because the boundary scrubber
  rewinds to the nearest preceding codepoint boundary.
"""

from __future__ import annotations

import pytest

from corpus_forge.chunkers.base import TextChunk
from corpus_forge.chunkers.cdc import CDCChunker

# ── Construction ─────────────────────────────────────────────────────────


def test_cdc_chunker_default_construction():
    """Default constructor uses the planning-doc-pinned defaults."""
    c = CDCChunker()
    assert c.min_size == 256
    assert c.avg_size == 1024
    assert c.max_size == 4096


def test_cdc_chunker_custom_construction():
    c = CDCChunker(min_size=128, avg_size=512, max_size=2048)
    assert c.min_size == 128
    assert c.avg_size == 512
    assert c.max_size == 2048


def test_cdc_chunker_invalid_min_avg_max_raises():
    """min_size must be < avg_size < max_size."""
    with pytest.raises(ValueError):
        CDCChunker(min_size=1024, avg_size=512, max_size=2048)
    with pytest.raises(ValueError):
        CDCChunker(min_size=128, avg_size=2048, max_size=1024)


# ── Edge cases ───────────────────────────────────────────────────────────


def test_empty_text_returns_empty_list():
    c = CDCChunker()
    assert c.chunk("") == []


def test_text_smaller_than_min_size_emits_single_chunk():
    """A 100-byte input with min_size=256 must come out as one chunk
    (the FastCDC ``min_size`` floor forces a single boundary)."""
    c = CDCChunker(min_size=256, avg_size=1024, max_size=4096)
    text = "hello world. " * 5  # ~65 bytes
    chunks = c.chunk(text)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_short_text_metadata_shape():
    """Even a single-chunk short doc carries cdc_fingerprint + byte_range."""
    c = CDCChunker()
    text = "short input"
    chunks = c.chunk(text)
    assert len(chunks) == 1
    md = chunks[0].metadata
    assert isinstance(md, dict)
    assert "cdc_fingerprint" in md
    assert isinstance(md["cdc_fingerprint"], str)
    assert len(md["cdc_fingerprint"]) > 0  # non-empty hex
    assert "byte_range" in md
    assert isinstance(md["byte_range"], tuple)
    assert len(md["byte_range"]) == 2
    assert md["byte_range"][0] == 0
    assert md["byte_range"][1] == len(text.encode("utf-8"))


# ── Size-bound contract ──────────────────────────────────────────────────


def _lorem(repeats: int = 500) -> str:
    """Generate ~10 KB of pseudo-prose. Deterministic."""
    paragraph = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat. "
    )
    return (paragraph + "\n\n") * repeats


def test_long_text_emits_multiple_chunks():
    c = CDCChunker(min_size=256, avg_size=1024, max_size=4096)
    text = _lorem(50)  # ~10 KB
    chunks = c.chunk(text)
    assert len(chunks) > 1


def test_chunks_respect_size_bounds():
    """Every chunk's UTF-8 byte length lies in [min_size, max_size],
    except the trailing chunk which may fall below ``min_size``."""
    c = CDCChunker(min_size=256, avg_size=1024, max_size=4096)
    text = _lorem(50)
    chunks = c.chunk(text)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        byte_len = len(chunk.text.encode("utf-8"))
        # UTF-8 boundary rewind may shave a byte or two below min_size on
        # rare alignment — allow a small tolerance.
        assert byte_len <= c.max_size, f"chunk too large: {byte_len} bytes"
        assert byte_len >= c.min_size - 4, f"chunk too small: {byte_len} bytes"
    # Last chunk may be smaller than min_size (trailing remainder).
    last_len = len(chunks[-1].text.encode("utf-8"))
    assert last_len <= c.max_size


def test_chunks_cover_full_input():
    """Concatenating all chunks back must reproduce the input."""
    c = CDCChunker(min_size=256, avg_size=1024, max_size=4096)
    text = _lorem(50)
    chunks = c.chunk(text)
    reassembled = "".join(ch.text for ch in chunks)
    assert reassembled == text


def test_byte_range_covers_full_input():
    c = CDCChunker(min_size=256, avg_size=1024, max_size=4096)
    text = _lorem(50)
    chunks = c.chunk(text)
    # byte_range tuples must be contiguous and cover [0, len(utf-8 bytes)]
    expected_total = len(text.encode("utf-8"))
    last_end = 0
    for ch in chunks:
        start, end = ch.metadata["byte_range"]
        assert start == last_end, f"non-contiguous byte_range: {start} != {last_end}"
        assert end > start
        last_end = end
    assert last_end == expected_total


# ── Determinism ──────────────────────────────────────────────────────────


def test_same_input_yields_same_chunks():
    """The whole point of CDC: deterministic boundaries for identical input."""
    c = CDCChunker(min_size=256, avg_size=1024, max_size=4096)
    text = _lorem(50)
    chunks_a = c.chunk(text)
    chunks_b = c.chunk(text)
    assert len(chunks_a) == len(chunks_b)
    for a, b in zip(chunks_a, chunks_b, strict=True):
        assert a.text == b.text
        assert a.metadata["cdc_fingerprint"] == b.metadata["cdc_fingerprint"]
        assert a.metadata["byte_range"] == b.metadata["byte_range"]


def test_fresh_chunker_yields_same_chunks_as_reused_chunker():
    """Chunker instance state must not affect output (no hidden RNG)."""
    text = _lorem(30)
    chunks_a = CDCChunker().chunk(text)
    chunks_b = CDCChunker().chunk(text)
    assert [c.text for c in chunks_a] == [c.text for c in chunks_b]


# ── Metadata returns TextChunk instances ─────────────────────────────────


def test_chunks_are_textchunk_instances():
    c = CDCChunker()
    chunks = c.chunk(_lorem(50))
    for ch in chunks:
        assert isinstance(ch, TextChunk)


def test_fingerprint_is_hex():
    c = CDCChunker()
    chunks = c.chunk(_lorem(50))
    for ch in chunks:
        fp = ch.metadata["cdc_fingerprint"]
        # Hex string — must round-trip via int.
        int(fp, 16)  # raises on non-hex


# ── UTF-8 boundary safety ────────────────────────────────────────────────


def test_utf8_chinese_text_no_decode_errors():
    """Chinese prose: every char is 3 UTF-8 bytes — boundary scrubber
    must rewind off a mid-codepoint cut."""
    c = CDCChunker(min_size=256, avg_size=512, max_size=2048)
    # ~4 KB of CJK text (3 bytes per char x 1200 chars ~= 3.6 KB).
    text = "今日の天気はとてもいいです。" * 200
    chunks = c.chunk(text)
    # If any boundary landed mid-codepoint we'd see UnicodeDecodeError on
    # the chunk-text decode inside CDCChunker.chunk(); reaching here is
    # the assertion.
    assert len(chunks) >= 1
    # And every chunk's text must be valid (re-encodable).
    for ch in chunks:
        assert isinstance(ch.text, str)
        ch.text.encode("utf-8")


def test_utf8_arabic_text_no_decode_errors():
    c = CDCChunker(min_size=256, avg_size=512, max_size=2048)
    text = "العربية لغة سامية تكتب من اليمين إلى اليسار. " * 100
    chunks = c.chunk(text)
    assert len(chunks) >= 1
    for ch in chunks:
        ch.text.encode("utf-8")


def test_utf8_emoji_text_no_decode_errors():
    c = CDCChunker(min_size=256, avg_size=512, max_size=2048)
    # Emoji are 4 bytes in UTF-8 → high cut-on-byte risk.
    text = "🚀🔥💻 building a corpus " * 250
    chunks = c.chunk(text)
    assert len(chunks) >= 1
    for ch in chunks:
        ch.text.encode("utf-8")


def test_utf8_mixed_scripts_reassembles_exactly():
    """Mixed-script text must reassemble byte-perfect even though
    boundaries get rewound to codepoint starts."""
    c = CDCChunker(min_size=256, avg_size=512, max_size=2048)
    text = "ascii prose with some 日本語 mixed in 🌟 and العربية اخرى " * 80
    chunks = c.chunk(text)
    reassembled = "".join(ch.text for ch in chunks)
    assert reassembled == text


# ── Backwards / forwards compat ──────────────────────────────────────────


def test_cdc_chunker_inherits_from_chunker_base():
    """CDCChunker must satisfy ``isinstance(_, Chunker)`` so the
    dispatcher's typing contract still holds."""
    from corpus_forge.chunkers.base import Chunker

    assert isinstance(CDCChunker(), Chunker)
