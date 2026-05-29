"""Hard-max character cap post-chunker filter.

Provides :func:`enforce_chunk_hard_max`, a lazy generator that passes
through any :class:`~corpus_forge.chunkers.base.TextChunk` whose text is
within the configured character limit and splits oversized chunks into
equal-width substring slices.

Production motivation: a 1.66 MB single chunk from a synthetic-eval
benchmark file wedged nomic-embed-text with NaN cascades and triggered
the EmbedderWedged circuit-breaker after 50 consecutive sub-chunk
failures. Splitting at the chunker level (before the embedding call)
eliminates that class of failure entirely.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator

from .base import TextChunk


def enforce_chunk_hard_max(
    chunks: Iterable[TextChunk],
    max_chars: int,
) -> Iterator[TextChunk]:
    """Yield chunks, splitting any whose text exceeds *max_chars* chars.

    Parameters
    ----------
    chunks:
        An iterable of :class:`TextChunk` objects (consumed lazily).
    max_chars:
        Hard upper bound on ``len(chunk.text)`` for any emitted chunk.
        Must be ``> 0``; raises :exc:`ValueError` otherwise.

    Yields
    ------
    TextChunk
        - Chunks whose text is already ``<= max_chars`` are yielded as the
          **same object** (identity preserved, no copy, no metadata mutation).
        - Oversized chunks are split into ``ceil(len/max_chars)`` new
          :class:`TextChunk` objects, each carrying a substring of the
          original text and a fresh metadata dict built from
          ``{**original.metadata, "hard_max_split": True}``.

    Raises
    ------
    ValueError
        When *max_chars* is ``<= 0``.
    """
    if max_chars <= 0:
        raise ValueError(f"max_chars must be > 0, got {max_chars!r}")

    for chunk in chunks:
        text = chunk.text
        text_len = len(text)

        if text_len <= max_chars:
            yield chunk
            continue

        # Oversized: split into ceil(text_len / max_chars) equal-width slices.
        n_pieces = math.ceil(text_len / max_chars)
        # TextChunk.__post_init__ guarantees metadata is never None at runtime,
        # but the field annotation is ``dict | None``; guard here for the type-checker.
        original_meta: dict = chunk.metadata or {}
        for i in range(n_pieces):
            start = i * max_chars
            end = min(start + max_chars, text_len)
            yield TextChunk(
                text=text[start:end],
                heading=chunk.heading,
                role=chunk.role,
                token_count=chunk.token_count,
                metadata={**original_meta, "hard_max_split": True},
            )
