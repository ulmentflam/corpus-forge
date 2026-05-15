"""Phase F (F-01) — Content-Defined Chunker via FastCDC.

The chunker replaces positional ``MarkdownChunker``/``PassthroughChunker``
slicing for prose classes (``book``, ``textbook``, ``paper``, ``article``,
``note``, ``other``). Boundaries are content-defined: small edits ripple
≤ 2-3 chunks (proven via property-based tests in
``tests/unit/test_cdc_stability.py``). This makes the Phase C content-
hash embedding-reuse path achieve its design potential — most chunks
survive a small edit.

Algorithm:
    The underlying `fastcdc <https://pypi.org/project/fastcdc/>`_ package
    (MIT) implements the FastCDC rolling-hash algorithm. We feed the
    UTF-8 byte representation of the input, then re-decode each emitted
    byte range back to ``str``. Boundary cuts that land mid-codepoint
    are rewound to the nearest preceding codepoint start so the decode
    never raises ``UnicodeDecodeError`` and the chunk texts reassemble
    byte-perfect to the input.

Defaults (per planning doc):
    - ``min_size = 256``
    - ``avg_size = 1024``
    - ``max_size = 4096``

Hash function:
    FastCDC's rolling hash (Gear) is internal and not user-configurable.
    The ``hf`` argument to ``fastcdc.fastcdc`` is the *content fingerprint*
    hash function applied to each emitted chunk's bytes — we pin
    :data:`hashlib.sha256` for determinism and store the hex digest
    in ``TextChunk.metadata["cdc_fingerprint"]``. Storing a stable
    fingerprint is what unlocks future cross-document dedup (P2).

Metadata shape:
    Each emitted :class:`~corpus_forge.chunkers.base.TextChunk` carries

    .. code-block:: python

       metadata = {
           "cdc_fingerprint": "<hex sha256 of chunk bytes>",
           "byte_range": (offset, offset + length),
       }
"""

from __future__ import annotations

import hashlib

from .base import Chunker, TextChunk

# ── UTF-8 byte-class constants ───────────────────────────────────────────
#
# Centralised here so the byte-boundary scrubber below stays readable
# (and lint-clean — ruff's PLR2004 yelps at inline hex literals).

#: ASCII bytes (0x00..0x7F) — always a complete 1-byte codepoint.
_UTF8_ASCII_HI = 0x80
#: Lower bound of the UTF-8 continuation-byte range (10xxxxxx).
_UTF8_CONT_LO = 0x80
#: Upper bound of the UTF-8 continuation-byte range.
_UTF8_CONT_HI = 0xBF
#: Lower bound of 2-byte UTF-8 leading bytes (110xxxxx). Anything in
#: ``0x80..0xBF`` is a continuation, not a lead — handled separately.
_UTF8_LEAD_2BYTE = 0xC0
#: Lower bound of 3-byte UTF-8 leading bytes (1110xxxx).
_UTF8_LEAD_3BYTE = 0xE0
#: Lower bound of 4-byte UTF-8 leading bytes (11110xxx).
_UTF8_LEAD_4BYTE = 0xF0


class CDCChunker(Chunker):
    """Content-defined chunker using FastCDC rolling-hash boundaries.

    See module docstring for algorithm + metadata contract.
    """

    def __init__(
        self,
        min_size: int = 256,
        avg_size: int = 1024,
        max_size: int = 4096,
    ) -> None:
        # Validate the size ordering up-front so misconfiguration fails
        # fast at construction rather than mid-ingest.
        if not (min_size < avg_size < max_size):
            raise ValueError(
                "CDCChunker requires min_size < avg_size < max_size; "
                f"got min={min_size}, avg={avg_size}, max={max_size}"
            )
        # We deliberately do NOT call ``super().__init__()`` — the base
        # ``Chunker`` carries positional-slicing params (``max_chars``,
        # ``overlap``) that are meaningless for content-defined chunking.
        # ``CDCChunker.chunk()`` overrides the base method entirely, so
        # the base state would only be dead weight.
        self.min_size = min_size
        self.avg_size = avg_size
        self.max_size = max_size

    def chunk(self, text: str) -> list[TextChunk]:
        """Split ``text`` into content-defined chunks.

        Edge cases:

        - Empty input → ``[]``.
        - Input smaller than ``min_size`` bytes → a single chunk
          containing the whole input.
        - UTF-8 multi-byte codepoints: any cut that lands mid-codepoint
          is rewound to the preceding codepoint start. Trailing bytes
          rolled off one chunk are prepended to the next.
        """
        if not text:
            return []

        # Lazy import — keeps `from corpus_forge.chunkers import ...` cheap
        # for environments without the [multi-format] extra (CDC is only
        # exercised after `corpus-forge classify` runs).
        import fastcdc  # noqa: PLC0415

        data = text.encode("utf-8")

        # Short-text fast path: under min_size means FastCDC would still
        # emit a single chunk, but we short-circuit so the unit test
        # contract (one chunk for sub-min-size input) is explicit.
        if len(data) < self.min_size:
            fingerprint = hashlib.sha256(data).hexdigest()
            return [
                TextChunk(
                    text=text,
                    metadata={
                        "cdc_fingerprint": fingerprint,
                        "byte_range": (0, len(data)),
                    },
                )
            ]

        raw_chunks = list(
            fastcdc.fastcdc(
                data,
                min_size=self.min_size,
                avg_size=self.avg_size,
                max_size=self.max_size,
                fat=True,  # emit chunk bytes inline so we can decode them
                hf=hashlib.sha256,
            )
        )

        # FastCDC chunks are contiguous byte ranges. We rewind any cut
        # that lands mid-codepoint to the previous codepoint start, then
        # carry the orphaned trailing bytes forward to the next chunk so
        # the reassembled text is byte-identical to the input.
        out: list[TextChunk] = []
        carry = b""
        for i, ch in enumerate(raw_chunks):
            # Concatenate bytes carried over from the previous chunk's
            # trailing partial codepoint, then walk back from the end to
            # the nearest codepoint boundary. For the *last* chunk we
            # never carry forward — any trailing bytes must decode.
            body = carry + bytes(ch.data)
            is_last = i == len(raw_chunks) - 1
            if is_last:
                safe_end = len(body)
                next_carry = b""
            else:
                safe_end = _codepoint_safe_end(body)
                next_carry = body[safe_end:]

            chunk_bytes = body[:safe_end]
            try:
                chunk_text = chunk_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # Defensive — shouldn't happen given the rewind. If a
                # malformed sequence slips through (e.g. lone surrogate
                # from upstream), fall back to ``replace`` so ingest
                # never crashes on a single bad byte.
                chunk_text = chunk_bytes.decode("utf-8", errors="replace")

            byte_start = ch.offset - len(carry)
            byte_end = byte_start + len(chunk_bytes)
            fingerprint = hashlib.sha256(chunk_bytes).hexdigest()

            out.append(
                TextChunk(
                    text=chunk_text,
                    metadata={
                        "cdc_fingerprint": fingerprint,
                        "byte_range": (byte_start, byte_end),
                    },
                )
            )

            carry = next_carry

        return out


def _codepoint_safe_end(data: bytes) -> int:
    """Return the largest index ``n <= len(data)`` such that
    ``data[:n]`` is a complete UTF-8 byte sequence.

    UTF-8 continuation bytes start with ``0b10`` (i.e. ``0x80..0xBF``).
    Walking backwards over continuations and then off the lead byte
    yields the nearest preceding codepoint start. If the input ends on
    a clean codepoint boundary, the function returns ``len(data)``.

    A pure ASCII suffix always returns ``len(data)`` (no continuations).
    """
    n = len(data)
    if n == 0:
        return 0
    # If the final byte is a lone ASCII / start byte (top bit 0 or
    # 0b11xxxxxx leading byte) the chunk is already aligned.
    last = data[n - 1]
    if last < _UTF8_ASCII_HI:  # ASCII byte
        return n

    # Walk back over continuation bytes.
    i = n - 1
    while i > 0 and _UTF8_CONT_LO <= data[i] <= _UTF8_CONT_HI:
        i -= 1
    # ``i`` now points at the leading byte of a codepoint that may or
    # may not be complete. Determine the expected codepoint length from
    # the leading byte:
    lead = data[i]
    if lead < _UTF8_ASCII_HI:
        expected = 1
    elif lead < _UTF8_LEAD_2BYTE:
        # Stray continuation as a "leading" byte — fall through and rewind
        # to before it.
        return i
    elif lead < _UTF8_LEAD_3BYTE:
        expected = 2
    elif lead < _UTF8_LEAD_4BYTE:
        expected = 3
    else:
        expected = 4

    if n - i >= expected:
        # The trailing codepoint is complete; the full buffer is safe.
        return n
    # Truncate just before the leading byte so the partial codepoint
    # carries forward to the next chunk.
    return i
