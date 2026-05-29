"""Unit tests for the chunker hard-max-chars cap.

Covers two implemented symbols:

- ``corpus_forge.config.ScanConfig.chunker_hard_max_chars`` —
  ``int`` field, default ``32768``, validated ``gt=0``, persisted in
  the ``[scan]`` TOML block.
- ``corpus_forge.chunkers.enforce_chunk_hard_max(chunks, max_chars)``
  (canonical home: ``corpus_forge.chunkers.hard_max``) — generator that
  yields original :class:`TextChunk` objects unchanged when
  ``len(chunk.text) <= max_chars``, and yields new ``TextChunk`` objects
  carrying ``metadata["hard_max_split"] = True`` for each split piece.
  ``max_chars <= 0`` raises :class:`ValueError`.
- ``corpus_forge.ingest.ingest_one(..., chunker_hard_max_chars=...)`` —
  optional wiring hook applied after ``_process_document`` /
  ``_process_conversation`` and before the backend ``upsert_*`` call.
  ``ingest_once`` passes ``config.scan.chunker_hard_max_chars`` so the
  cap is the default for the production ingest path.

The 1.66 MB regression fixture below mirrors the production failure
where a single oversize chunk from
``Llama3_2-Mamba2-distill_len_14026_depth_5600_context.txt`` tripped
the embedder's 50-consecutive-fail circuit breaker; the post-chunker
cap must split it cleanly with no data loss.
"""

from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# ScanConfig.chunker_hard_max_chars — field existence + default + validation
# ---------------------------------------------------------------------------


def test_scan_config_chunker_hard_max_chars_field_exists() -> None:
    """ScanConfig must have a chunker_hard_max_chars attribute after construction."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert hasattr(cfg, "chunker_hard_max_chars"), (
        "ScanConfig is missing the chunker_hard_max_chars field — must be added"
    )


def test_scan_config_chunker_hard_max_chars_default_is_32768() -> None:
    """ScanConfig().chunker_hard_max_chars must default to 32768."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert cfg.chunker_hard_max_chars == 32768, (
        f"Expected chunker_hard_max_chars default 32768, got {cfg.chunker_hard_max_chars!r}"
    )


def test_scan_config_chunker_hard_max_chars_default_is_int() -> None:
    """Default value must be a plain Python int."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert isinstance(cfg.chunker_hard_max_chars, int), (
        f"chunker_hard_max_chars must be int, got {type(cfg.chunker_hard_max_chars)!r}"
    )


def test_scan_config_chunker_hard_max_chars_in_model_fields() -> None:
    """chunker_hard_max_chars must appear in ScanConfig.model_fields (Pydantic v2)."""
    from corpus_forge.config import ScanConfig

    assert "chunker_hard_max_chars" in ScanConfig.model_fields, (
        "chunker_hard_max_chars not in ScanConfig.model_fields — must be declared as Field()"
    )


def test_scan_config_chunker_hard_max_chars_explicit_value_accepted() -> None:
    """chunker_hard_max_chars=65536 (2x default) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(chunker_hard_max_chars=65536)
    assert cfg.chunker_hard_max_chars == 65536


def test_scan_config_chunker_hard_max_chars_large_value_accepted() -> None:
    """chunker_hard_max_chars=2**31-1 (opt-out sentinel) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(chunker_hard_max_chars=2**31 - 1)
    assert cfg.chunker_hard_max_chars == 2**31 - 1


def test_scan_config_chunker_hard_max_chars_one_accepted() -> None:
    """chunker_hard_max_chars=1 (minimum valid value) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(chunker_hard_max_chars=1)
    assert cfg.chunker_hard_max_chars == 1


def test_scan_config_chunker_hard_max_chars_zero_rejected() -> None:
    """chunker_hard_max_chars=0 violates gt=0 — must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(chunker_hard_max_chars=0)


def test_scan_config_chunker_hard_max_chars_negative_rejected() -> None:
    """chunker_hard_max_chars=-1 violates gt=0 — must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(chunker_hard_max_chars=-1)


def test_scan_config_chunker_hard_max_chars_large_negative_rejected() -> None:
    """chunker_hard_max_chars=-32768 must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(chunker_hard_max_chars=-32768)


def test_scan_config_extra_fields_still_rejected_after_new_field() -> None:
    """extra='forbid' must remain on ScanConfig after adding chunker_hard_max_chars."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(chunker_hard_max_chars=32768, totally_unknown=True)  # type: ignore[call-arg]


def test_scan_config_chunker_hard_max_chars_coexists_with_workers() -> None:
    """chunker_hard_max_chars and workers can both be set simultaneously."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=4, chunker_hard_max_chars=16384)
    assert cfg.workers == 4
    assert cfg.chunker_hard_max_chars == 16384


def test_scan_config_chunker_hard_max_chars_survives_model_dump_round_trip() -> None:
    """model_dump() + model_validate() round-trip preserves chunker_hard_max_chars."""
    from corpus_forge.config import ScanConfig

    original = ScanConfig(chunker_hard_max_chars=8192)
    dumped = original.model_dump()
    restored = ScanConfig.model_validate(dumped)
    assert restored.chunker_hard_max_chars == 8192


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — importability
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_importable_from_chunkers_package() -> None:
    """enforce_chunk_hard_max must be importable from corpus_forge.chunkers."""
    from corpus_forge.chunkers import enforce_chunk_hard_max  # noqa: F401


def test_enforce_chunk_hard_max_importable_from_canonical_module() -> None:
    """enforce_chunk_hard_max must be importable from corpus_forge.chunkers.hard_max."""
    from corpus_forge.chunkers.hard_max import enforce_chunk_hard_max  # noqa: F401


def test_enforce_chunk_hard_max_same_object_both_import_paths() -> None:
    """Both import paths must refer to the exact same function object."""
    from corpus_forge.chunkers import enforce_chunk_hard_max as fn_pkg
    from corpus_forge.chunkers.hard_max import enforce_chunk_hard_max as fn_mod

    assert fn_pkg is fn_mod


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — invalid max_chars
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_zero_max_chars_raises() -> None:
    """max_chars=0 must raise ValueError (invariant: gt=0)."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunks = [TextChunk(text="hello")]
    with pytest.raises(ValueError):
        list(enforce_chunk_hard_max(chunks, max_chars=0))


def test_enforce_chunk_hard_max_negative_max_chars_raises() -> None:
    """max_chars=-1 must raise ValueError."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunks = [TextChunk(text="hello")]
    with pytest.raises(ValueError):
        list(enforce_chunk_hard_max(chunks, max_chars=-1))


def test_enforce_chunk_hard_max_large_negative_raises() -> None:
    """max_chars=-100000 must raise ValueError."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunks = [TextChunk(text="hello")]
    with pytest.raises(ValueError):
        list(enforce_chunk_hard_max(chunks, max_chars=-100_000))


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — empty / trivial inputs
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_empty_iterable_yields_nothing() -> None:
    """An empty chunk list produces an empty iterator."""
    from corpus_forge.chunkers import enforce_chunk_hard_max

    result = list(enforce_chunk_hard_max([], max_chars=32768))
    assert result == []


def test_enforce_chunk_hard_max_empty_text_chunk_passes_through() -> None:
    """A chunk with empty text (len=0) is <= any positive max_chars — pass through."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunk = TextChunk(text="")
    result = list(enforce_chunk_hard_max([chunk], max_chars=32768))
    assert len(result) == 1
    assert result[0] is chunk, "Empty-text chunk must be passed through as the same object"


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — happy path: small chunks pass through unchanged
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_short_chunk_passes_through_identity() -> None:
    """A chunk shorter than max_chars must be the SAME object (identity, not copy)."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunk = TextChunk(text="hello world")
    result = list(enforce_chunk_hard_max([chunk], max_chars=32768))
    assert len(result) == 1
    assert result[0] is chunk, "Under-max chunk must be returned as the SAME object"


def test_enforce_chunk_hard_max_exact_boundary_chunk_passes_through_identity() -> None:
    """A chunk of EXACTLY max_chars must be the same object (boundary is inclusive)."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 32768
    chunk = TextChunk(text="x" * max_chars)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) == 1
    assert result[0] is chunk, "Exact-boundary chunk must be returned as the SAME object"
    assert len(result[0].text) == max_chars


def test_enforce_chunk_hard_max_multiple_small_chunks_all_pass_through() -> None:
    """Multiple under-max chunks must all be returned unchanged as the same objects."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunks = [TextChunk(text=f"chunk {i}") for i in range(5)]
    result = list(enforce_chunk_hard_max(chunks, max_chars=32768))
    assert len(result) == 5
    for original, returned in zip(chunks, result, strict=False):
        assert returned is original, f"Chunk at position {chunks.index(original)} changed identity"


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — splitting: one-over boundary
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_one_over_splits_into_two() -> None:
    """A chunk of max_chars+1 must be split into exactly 2 pieces."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 32768
    chunk = TextChunk(text="a" * (max_chars + 1))
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) == 2, f"Expected 2 pieces, got {len(result)}"
    assert len(result[0].text) == max_chars
    assert len(result[1].text) == 1
    # Concatenation must reconstruct the original
    assert result[0].text + result[1].text == chunk.text


def test_enforce_chunk_hard_max_one_over_each_piece_lte_max() -> None:
    """Each piece from a one-over split is <= max_chars."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    chunk = TextChunk(text="z" * 101)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    for piece in result:
        assert len(piece.text) <= max_chars


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — splitting: standard large chunk
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_100k_yields_four_chunks() -> None:
    """A 100_000-char chunk with max_chars=32768 yields ceil(100000/32768) = 4 chunks."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 32768
    text_len = 100_000
    chunk = TextChunk(text="b" * text_len)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))

    expected_count = math.ceil(text_len / max_chars)  # ceil(100000/32768) = 4
    assert len(result) == expected_count, (
        f"Expected {expected_count} chunks for {text_len}-char input, got {len(result)}"
    )
    for piece in result:
        assert len(piece.text) <= max_chars, (
            f"Piece length {len(piece.text)} exceeds max_chars {max_chars}"
        )
    # Text must be lossless
    assert "".join(p.text for p in result) == chunk.text, "Splitting must be lossless"


def test_enforce_chunk_hard_max_all_pieces_lte_max_chars() -> None:
    """Every piece from any split must have len(text) <= max_chars."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 32768
    # Use a realistic-ish size — 500k chars (≈ a long academic PDF worth of text)
    chunk = TextChunk(text="c" * 500_000)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    for i, piece in enumerate(result):
        assert len(piece.text) <= max_chars, (
            f"Piece {i} has length {len(piece.text)}, exceeds max_chars {max_chars}"
        )


def test_enforce_chunk_hard_max_splitting_is_lossless() -> None:
    """Concatenation of all split pieces must exactly reconstruct the original text."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 32768
    original_text = "x" * 100_000
    chunk = TextChunk(text=original_text)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert "".join(p.text for p in result) == original_text


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — regression fixture: 1.66 MB production failure
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_1_66_mb_splits_cleanly() -> None:
    """Regression: 1.66 MB chunk (the production failure size) must split without error.

    The real file was MambaInLlama/benchmark/needle/contexts/
    Llama3_2-Mamba2-distill_len_14026_depth_5600_context.txt.
    That file's content caused NaN cascades in nomic-embed-text and triggered
    the EmbedderWedged circuit-breaker. This test ensures the chunker-level
    hard cap fires BEFORE the embedding call by verifying:
    - No exception is raised.
    - Every output piece is <= 32768 chars.
    - The full text is recovered by concatenation.
    """
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    # 1.66 MB expressed in Unicode code points (ASCII content — same byte count)
    text_len = 1_660_000
    max_chars = 32768
    original_text = "A" * text_len
    chunk = TextChunk(text=original_text)

    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))

    assert len(result) > 1, "1.66 MB chunk must be split into multiple pieces"
    for i, piece in enumerate(result):
        assert len(piece.text) <= max_chars, (
            f"Piece {i} has length {len(piece.text)}, exceeds max_chars {max_chars}"
        )
    reconstructed = "".join(p.text for p in result)
    assert len(reconstructed) == text_len, (
        f"Reconstructed text length {len(reconstructed)} != original {text_len}"
    )
    assert reconstructed == original_text, "1.66 MB chunk splitting must be lossless"


def test_enforce_chunk_hard_max_1_66_mb_expected_piece_count() -> None:
    """1.66 MB / 32768 = ceil(1660000/32768) = 51 pieces."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    text_len = 1_660_000
    max_chars = 32768
    chunk = TextChunk(text="M" * text_len)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    expected = math.ceil(text_len / max_chars)
    assert len(result) == expected, f"Expected {expected} pieces, got {len(result)}"


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — metadata propagation
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_passing_chunk_has_no_hard_max_split_key() -> None:
    """A chunk that passes through (under max) must NOT have hard_max_split injected."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunk = TextChunk(text="short", metadata={"source": "test.md"})
    result = list(enforce_chunk_hard_max([chunk], max_chars=32768))
    assert len(result) == 1
    assert result[0] is chunk
    assert "hard_max_split" not in result[0].metadata, (
        "Pass-through chunks must not have hard_max_split injected"
    )
    # Original metadata must be untouched
    assert result[0].metadata == {"source": "test.md"}


def test_enforce_chunk_hard_max_split_pieces_carry_hard_max_split_true() -> None:
    """Each piece of a split chunk must have metadata['hard_max_split'] == True."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    chunk = TextChunk(text="y" * 250, metadata={"source": "big.txt"})
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) > 1
    for i, piece in enumerate(result):
        assert piece.metadata.get("hard_max_split") is True, (
            f"Piece {i} missing hard_max_split=True in metadata: {piece.metadata!r}"
        )


def test_enforce_chunk_hard_max_split_pieces_inherit_original_metadata() -> None:
    """Each split piece must carry ALL keys from the original chunk's metadata."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    original_meta = {"source": "big.txt", "lang": "en", "doc_id": "abc123"}
    chunk = TextChunk(text="z" * 250, metadata=original_meta.copy())
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) > 1
    for i, piece in enumerate(result):
        for key, val in original_meta.items():
            assert piece.metadata.get(key) == val, (
                f"Piece {i} missing metadata key {key!r} (value {val!r}): {piece.metadata!r}"
            )


def test_enforce_chunk_hard_max_split_does_not_mutate_original_metadata() -> None:
    """Splitting a chunk must NOT mutate the original chunk's metadata dict."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    original_meta = {"source": "big.txt"}
    chunk = TextChunk(text="w" * 250, metadata=original_meta)
    list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    # The original chunk's metadata must be unchanged
    assert "hard_max_split" not in chunk.metadata, (
        "enforce_chunk_hard_max must not mutate the original chunk's metadata"
    )
    assert chunk.metadata == {"source": "big.txt"}


def test_enforce_chunk_hard_max_split_pieces_metadata_is_independent() -> None:
    """Each split piece must get its own metadata dict (not a shared reference)."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    chunk = TextChunk(text="v" * 250, metadata={"source": "big.txt"})
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) > 1
    # Mutating piece[0]'s metadata must not affect piece[1]'s metadata
    result[0].metadata["injected"] = "only_piece_0"
    assert "injected" not in result[1].metadata, (
        "Split pieces must have independent metadata dicts (not a shared reference)"
    )


def test_enforce_chunk_hard_max_chunk_with_no_metadata_split_gets_hard_max_split() -> None:
    """Even a chunk with metadata={} (the default) gets hard_max_split=True on split."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    chunk = TextChunk(text="q" * 250)  # metadata defaults to {}
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) > 1
    for piece in result:
        assert piece.metadata.get("hard_max_split") is True


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — mixed-size input (some pass through, some split)
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_mixed_input_ordering_preserved() -> None:
    """With mixed small/large chunks, output ordering must match input ordering.

    Chunks: [small, large, small, large]
    The small ones pass through; the large ones split into multiple pieces.
    Output must interleave correctly: small, [large-pieces...], small, [large-pieces...].
    """
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    small_a = TextChunk(text="small_a")
    big_b = TextChunk(text="B" * 250, metadata={"id": "big_b"})
    small_c = TextChunk(text="small_c")
    big_d = TextChunk(text="D" * 350, metadata={"id": "big_d"})

    result = list(enforce_chunk_hard_max([small_a, big_b, small_c, big_d], max_chars=max_chars))

    # small_a passes through
    assert result[0] is small_a

    # big_b splits into ceil(250/100)=3 pieces; they come after small_a
    big_b_pieces_count = math.ceil(250 / max_chars)
    big_b_pieces = result[1 : 1 + big_b_pieces_count]
    assert len(big_b_pieces) == big_b_pieces_count
    assert "".join(p.text for p in big_b_pieces) == big_b.text

    # small_c follows big_b's pieces
    small_c_idx = 1 + big_b_pieces_count
    assert result[small_c_idx] is small_c

    # big_d's pieces follow small_c
    big_d_pieces_count = math.ceil(350 / max_chars)
    big_d_pieces = result[small_c_idx + 1 : small_c_idx + 1 + big_d_pieces_count]
    assert len(big_d_pieces) == big_d_pieces_count
    assert "".join(p.text for p in big_d_pieces) == big_d.text


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — generator / lazy consumption
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_returns_iterator() -> None:
    """enforce_chunk_hard_max must return an iterator (lazy generator), not a list."""
    import collections.abc

    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    chunks = [TextChunk(text="hello")]
    result = enforce_chunk_hard_max(chunks, max_chars=32768)
    assert isinstance(result, collections.abc.Iterator), (
        "enforce_chunk_hard_max must return an Iterator (generator), not a list. "
        f"Got {type(result)!r}"
    )


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — non-ASCII / multibyte content
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_splits_at_char_boundaries_not_byte_boundaries() -> None:
    """Splitting must count Unicode characters (len()), not bytes.

    A chunk of 32769 CJK characters (each 3 bytes in UTF-8) must split
    into 2 pieces with the first having exactly 32768 chars.
    """
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 32768
    # CJK unified ideograph — 3 bytes each in UTF-8; confirms char counting, not byte counting.
    cjk_char = "中"  # 中
    text = cjk_char * (max_chars + 1)
    chunk = TextChunk(text=text)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) == 2
    assert len(result[0].text) == max_chars
    assert len(result[1].text) == 1
    assert result[0].text + result[1].text == text


def test_enforce_chunk_hard_max_rtl_text_splits_correctly() -> None:
    """RTL text (Arabic) splits at character boundaries without corruption."""
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 50
    # Arabic letter Alef — valid Unicode scalar, present in RTL text.
    # This test DELIBERATELY uses Arabic to verify char-boundary splitting
    # on non-Latin scripts; RUF001 (ambiguous-unicode) is a false positive.
    arabic = "ا" * 75  # noqa: RUF001  -- 75 chars > 50
    chunk = TextChunk(text=arabic)
    result = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    assert len(result) == math.ceil(75 / max_chars)
    assert "".join(p.text for p in result) == arabic


# ---------------------------------------------------------------------------
# enforce_chunk_hard_max — idempotency
# ---------------------------------------------------------------------------


def test_enforce_chunk_hard_max_idempotent_on_already_split_output() -> None:
    """Applying enforce_chunk_hard_max to its own output must be a no-op (idempotent).

    If every output piece from the first pass is already <= max_chars,
    the second pass must return those same objects unchanged (identity).
    """
    from corpus_forge.chunkers import TextChunk, enforce_chunk_hard_max

    max_chars = 100
    chunk = TextChunk(text="i" * 250)
    first_pass = list(enforce_chunk_hard_max([chunk], max_chars=max_chars))
    # Every piece from the first pass must be <= max_chars
    assert all(len(p.text) <= max_chars for p in first_pass)

    second_pass = list(enforce_chunk_hard_max(first_pass, max_chars=max_chars))
    assert len(second_pass) == len(first_pass)
    for orig, again in zip(first_pass, second_pass, strict=False):
        assert again is orig, "Second pass must return the same objects (idempotent)"


# ---------------------------------------------------------------------------
# Wiring: ingest_one(chunker_hard_max_chars=...) flows into upsert_document
# ---------------------------------------------------------------------------


def _make_doc_chunker_with_oversized_chunk(oversize_len: int) -> tuple[object, object]:
    """Build a (RawDocument, Chunker) pair where ``chunker.chunk`` returns
    a single oversize :class:`TextChunk`. Used by both wiring tests below.
    """
    from unittest.mock import MagicMock

    from corpus_forge.chunkers.base import TextChunk
    from corpus_forge.sources.base import RawDocument

    doc = RawDocument(
        source_uri="test://wiring.txt",
        content_hash="hash",
        text="x" * oversize_len,
        title="Wiring",
        modified_at=1000.0,
        metadata={},
        labels=[],
    )
    chunker = MagicMock()
    chunker.chunk.return_value = [TextChunk(text="x" * oversize_len)]
    return doc, chunker


def test_ingest_one_applies_hard_max_when_passed() -> None:
    """ingest_one(..., chunker_hard_max_chars=100) must split oversized
    chunks before they reach backend.upsert_document.

    Without the cap a 250-char chunk would land in the DB as-is and the
    embedder would try to encode it, which is the root cause of the
    ``EmbedderWedged`` crashes on multi-MB synthetic context fixtures.
    With the cap, upsert_document sees pieces all ``<= max_chars``.
    """
    from unittest.mock import MagicMock

    from corpus_forge.ingest import ingest_one

    doc, chunker = _make_doc_chunker_with_oversized_chunk(oversize_len=250)
    backend = MagicMock()
    backend.get_hash.return_value = None

    ingest_one(backend, doc, chunker, embedders=[], dataset_id=1, chunker_hard_max_chars=100)

    backend.upsert_document.assert_called_once()
    chunk_arg = backend.upsert_document.call_args.args[2]
    assert len(chunk_arg) >= 3, "250-char chunk must split into >=3 pieces at max_chars=100"
    assert all(len(c.text) <= 100 for c in chunk_arg), "every persisted piece must respect cap"
    # Reassembling the pieces must reproduce the original text (no data loss).
    assert "".join(c.text for c in chunk_arg) == "x" * 250


def test_ingest_one_default_skips_hard_max() -> None:
    """ingest_one() with no ``chunker_hard_max_chars`` (the legacy
    contract used by existing tests) must persist chunks untouched —
    no split, no ``hard_max_split`` metadata injection."""
    from unittest.mock import MagicMock

    from corpus_forge.ingest import ingest_one

    doc, chunker = _make_doc_chunker_with_oversized_chunk(oversize_len=250)
    backend = MagicMock()
    backend.get_hash.return_value = None

    ingest_one(backend, doc, chunker, embedders=[], dataset_id=1)

    chunk_arg = backend.upsert_document.call_args.args[2]
    assert len(chunk_arg) == 1, "default path must not split chunks"
    assert len(chunk_arg[0].text) == 250
    assert "hard_max_split" not in chunk_arg[0].metadata
