"""Code chunker — AST-aware via tree-sitter-language-pack.

Phase D / Wave 0 — D-02.

Strategy
========

1. **AST walk (preferred).** When a tree-sitter grammar for the supplied
   ``language`` is available locally (``available_languages()``), parse
   the source and emit one chunk per top-level structural item
   (function / class / method / module-level block). Each chunk carries
   ``metadata={"kind", "name", "language", "byte_range"}`` and is
   prefixed with a header line ``# <relative path> :: <kind> <name>``
   when ``relative_path`` is supplied — so the embedder always sees a
   self-describing context.

   - **Oversize constructs** are sub-split along the next AST boundary
     (a nested function/class child) with a configurable byte overlap.
     If no nested boundary exists, the construct is sub-split at line
     boundaries.
   - **Undersize constructs** are coalesced with their neighbours until
     the combined size reaches ``max_chars`` or the next chunk would
     overshoot.

2. **Long-tail fallback.** When ``language`` is ``None`` or the grammar
   isn't available, fall through to a byte-line chunker that prefers
   blank-line and brace-depth boundaries. Brace-depth tracking is a
   one-pass scan; chunks finalise when the next blank line / closing
   brace lands and ``max_chars`` is exceeded.

Tree-sitter imports happen lazily inside the methods that need them so
``import corpus_forge.chunkers.code`` stays cheap on a core install
without the ``[code]`` extra. The pattern mirrors
``corpus_forge/mcp/server.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .base import Chunker, TextChunk

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class CodeChunker(Chunker):
    """AST-aware code chunker.

    Args:
        max_chars: Maximum chunk length in characters. Constructs larger
            than this are sub-split. Default ``1500``.
        min_chars: Minimum target chunk length. Adjacent small
            constructs are coalesced up to ``max_chars``. Default
            ``100``.
        overlap: Byte overlap between adjacent sub-chunks when an
            oversize construct is split. Default ``100``.
    """

    def __init__(
        self,
        max_chars: int = 1500,
        min_chars: int = 100,
        overlap: int = 100,
    ):
        super().__init__(max_chars=max_chars, overlap=overlap)
        self.min_chars = min_chars

    # ── Public entry point ───────────────────────────────────────────

    def chunk(  # type: ignore[override]
        self,
        text: str,
        *,
        language: str | None = None,
        relative_path: str | None = None,
    ) -> list[TextChunk]:
        """Chunk ``text``.

        Args:
            text: Source code as a string.
            language: Tree-sitter language name (``"python"``, ``"rust"``,
                etc.). When ``None`` or unsupported, falls back to the
                byte-line chunker.
            relative_path: Optional path used to construct the chunk
                header. When omitted, no header is prepended.
        """
        if not text:
            return []

        if language is not None and self._ast_supported(language):
            try:
                return self._chunk_with_ast(text, language=language, relative_path=relative_path)
            except Exception as exc:  # pragma: no cover — paranoid fallback
                logger.warning(
                    "CodeChunker AST path failed for language=%s: %s — falling back",
                    language,
                    exc,
                )
        return self._chunk_byte_line(text, language=language, relative_path=relative_path)

    # ── AST path ─────────────────────────────────────────────────────

    @staticmethod
    def _ast_supported(language: str) -> bool:
        """Return True iff the named language has a grammar (downloaded or
        fetchable from the pack manifest).

        ``available_languages()`` only reports grammars already on disk —
        on a fresh wheel that's ``[]``, which would force every first
        invocation to fall back to byte-line chunking even for languages
        the pack KNOWS how to fetch. We also consult
        ``manifest_languages()`` so a lazy fetch can run on the AST path.
        """
        try:
            import tree_sitter_language_pack as pack  # noqa: PLC0415
        except ImportError:  # pragma: no cover — only when [code] missing
            return False
        try:
            if language in pack.available_languages():
                return True
        except Exception:  # pragma: no cover — defensive
            pass
        try:
            return language in pack.manifest_languages()
        except Exception:  # pragma: no cover — defensive
            return False

    def _chunk_with_ast(
        self,
        text: str,
        *,
        language: str,
        relative_path: str | None,
    ) -> list[TextChunk]:
        # Defensive lazy-fetch: tree-sitter-language-pack only pre-bundles a
        # subset of grammars per platform (Python is bundled in the macOS
        # wheel but NOT the Linux wheel as of 1.8.x). Calling pack.process
        # without ensuring the grammar is downloaded yields items with
        # kind=None/name=None on the missing-grammar platforms. This call is
        # idempotent — first invocation downloads, later invocations are
        # in-process cache hits.
        _ensure_grammar_for_chunker(language)

        import tree_sitter_language_pack as pack  # noqa: PLC0415

        cfg = pack.ProcessConfig(
            language=language,
            structure=True,
            chunk_max_size=None,  # we do our own size policy
        )
        # pyrefly sees `pack.ProcessConfig` as the public dataclass while
        # `pack.process` types its `config` argument as the native bindings'
        # version — they're the same shape at runtime.
        result = pack.process(text, cfg)  # pyrefly: ignore[bad-argument-type]
        # The TypedDict stub for ``ProcessResult`` does not expose
        # ``structure`` as an attribute even though the native
        # PyO3-wrapped Rust object returned at runtime does. Pinning the
        # ignore to ``missing-attribute`` mirrors the bad-argument-type
        # pin above (same stub/runtime mismatch in the package).
        structure = list(result.structure)  # pyrefly: ignore[missing-attribute]

        # Flatten one level deep — top-level constructs first, then any
        # methods inside a class as separate constructs (so per-method
        # chunks land in the corpus too).
        items: list[_StructItem] = []
        for it in structure:
            items.append(_StructItem.from_pack(it, parent_name=None))
            for child in it.children:
                items.append(_StructItem.from_pack(child, parent_name=it.name))

        # Bail to fallback if the AST yielded nothing useful (e.g. very
        # short module-level scripts with no functions/classes).
        if not items:
            return self._chunk_byte_line(text, language=language, relative_path=relative_path)

        # Sort by start_byte so coalesce/sub-split walks in source order.
        items.sort(key=lambda i: i.start_byte)

        # Two-pass: 1) sub-split oversize items, 2) coalesce only tiny
        # ADJACENT items that share a kind (e.g. a series of one-liner
        # module-level functions) when both are under min_chars. Each
        # named structural item normally gets its own chunk so callers
        # can locate it via `metadata["name"]`.
        out: list[TextChunk] = []
        i = 0
        while i < len(items):
            item = items[i]
            item_text = text[item.start_byte : item.end_byte]

            if len(item_text) > self.max_chars:
                # Oversize → AST-sub-split at line boundaries.
                out.extend(
                    self._subsplit_oversize(
                        item_text,
                        kind=item.kind,
                        name=item.name,
                        language=language,
                        relative_path=relative_path,
                        base_offset=item.start_byte,
                    )
                )
                i += 1
                continue

            if len(item_text) < self.min_chars:
                # Try to coalesce with the immediately-following items
                # as long as they (a) share a kind, (b) stay under
                # max_chars combined, and (c) are also under min_chars
                # individually. The combined chunk's metadata records
                # the leading item's name and `kind = "Block"` to signal
                # the coalesce.
                group_start_byte = item.start_byte
                group_end_byte = item.end_byte
                group_names = [item.name]
                j = i + 1
                while (
                    j < len(items)
                    and items[j].kind == item.kind
                    and len(text[items[j].start_byte : items[j].end_byte]) < self.min_chars
                    and (items[j].end_byte - group_start_byte) <= self.max_chars
                ):
                    group_end_byte = items[j].end_byte
                    group_names.append(items[j].name)
                    j += 1

                if j > i + 1:
                    # Real coalesce: emit one chunk for the run.
                    body = text[group_start_byte:group_end_byte]
                    md: dict[str, Any] = {
                        "kind": "Block",
                        "name": group_names[0],
                        "names": tuple(group_names),
                        "language": language,
                        "byte_range": (group_start_byte, group_end_byte),
                        # Phase N Wave 2: every AST-walk chunk IS a
                        # definition by construction.  The coalesce
                        # path emits a synthetic Block, so
                        # `definition_kind` is "Block" here.
                        "is_definition": True,
                        "definition_kind": "Block",
                    }
                    out.append(_make_textchunk(body, md, relative_path))
                    i = j
                    continue
                # No coalesce candidate — fall through to the single-item emit.

            # Single-item emit (default path).
            md = {
                "kind": item.kind,
                "name": item.name,
                "language": language,
                "byte_range": (item.start_byte, item.end_byte),
                # Phase N Wave 2: tag the chunk as a definition.  The
                # AST walker only captures structural items
                # (Function / Class / Method / Block) so this is always
                # safe to set here.
                "is_definition": True,
                "definition_kind": item.kind,
            }
            out.append(_make_textchunk(item_text, md, relative_path))
            i += 1

        return out

    def _subsplit_oversize(
        self,
        body: str,
        *,
        kind: str,
        name: str,
        language: str,
        relative_path: str | None,
        base_offset: int,
    ) -> list[TextChunk]:
        """Split an oversize construct along line boundaries with overlap."""
        lines = body.splitlines(keepends=True)
        chunks: list[TextChunk] = []
        buf: list[str] = []
        buf_len = 0
        chunk_start_in_body = 0
        cursor_in_body = 0  # running offset into `body` as we consume lines

        def _emit(start: int, end: int, payload: str) -> None:
            md = {
                "kind": kind,
                "name": name,
                "language": language,
                "byte_range": (base_offset + start, base_offset + end),
                # Phase N Wave 2: every sub-split shares the parent
                # construct's definition status.  ``kind`` here is the
                # *parent's* kind (Function/Class/Method/Block), so it
                # carries straight into ``definition_kind``.
                "is_definition": True,
                "definition_kind": kind,
            }
            chunks.append(_make_textchunk(payload, md, relative_path))

        for line in lines:
            if buf_len + len(line) > self.max_chars and buf:
                payload = "".join(buf)
                _emit(chunk_start_in_body, cursor_in_body, payload)
                # Overlap: keep tail bytes for the next chunk.
                if self.overlap > 0 and len(payload) > self.overlap:
                    keep = payload[-self.overlap :]
                    buf = [keep]
                    buf_len = len(keep)
                    chunk_start_in_body = cursor_in_body - len(keep)
                else:
                    buf = []
                    buf_len = 0
                    chunk_start_in_body = cursor_in_body
            buf.append(line)
            buf_len += len(line)
            cursor_in_body += len(line)

        if buf:
            payload = "".join(buf)
            _emit(chunk_start_in_body, cursor_in_body, payload)

        return chunks

    # ── Byte-line fallback ───────────────────────────────────────────

    def _chunk_byte_line(
        self,
        text: str,
        *,
        language: str | None,
        relative_path: str | None,
    ) -> list[TextChunk]:
        """Long-tail fallback. Prefers blank-line boundaries."""
        if not text:
            return []

        chunks: list[TextChunk] = []
        buf: list[str] = []
        buf_len = 0
        chunk_start = 0
        cursor = 0
        last_blank_break: int | None = None  # cursor offset of last blank-line break

        def _emit(start: int, end: int, payload: str) -> None:
            md: dict[str, Any] = {
                "language": language,
                "byte_range": (start, end),
            }
            chunks.append(_make_textchunk(payload, md, relative_path))

        # Walk line-by-line so we can detect blank-line boundaries.
        lines = text.splitlines(keepends=True)
        for line in lines:
            is_blank = line.strip() == ""

            if buf_len + len(line) > self.max_chars and buf:
                # Prefer to break at the last blank line if we saw one
                # since the chunk started.
                if last_blank_break is not None and last_blank_break > chunk_start:
                    # Emit up to last_blank_break.
                    head_len = last_blank_break - chunk_start
                    payload = "".join(buf)[:head_len]
                    tail = "".join(buf)[head_len:]
                    _emit(chunk_start, last_blank_break, payload)
                    # Re-seed buffer with the tail + apply overlap.
                    if self.overlap > 0 and len(payload) > self.overlap:
                        overlap_keep = payload[-self.overlap :]
                    else:
                        overlap_keep = ""
                    buf = [overlap_keep, tail] if overlap_keep else [tail]
                    buf_len = sum(len(x) for x in buf)
                    chunk_start = last_blank_break - len(overlap_keep)
                    last_blank_break = None
                else:
                    payload = "".join(buf)
                    _emit(chunk_start, cursor, payload)
                    if self.overlap > 0 and len(payload) > self.overlap:
                        keep = payload[-self.overlap :]
                        buf = [keep]
                        buf_len = len(keep)
                        chunk_start = cursor - len(keep)
                    else:
                        buf = []
                        buf_len = 0
                        chunk_start = cursor
                    last_blank_break = None

            buf.append(line)
            buf_len += len(line)
            cursor += len(line)
            if is_blank:
                last_blank_break = cursor

        if buf:
            payload = "".join(buf)
            if payload.strip():
                _emit(chunk_start, cursor, payload)

        return chunks


# ── Helpers ──────────────────────────────────────────────────────────────


class _StructItem:
    __slots__ = ("end_byte", "kind", "name", "start_byte")

    def __init__(self, kind: str, name: str, start_byte: int, end_byte: int):
        self.kind = kind
        self.name = name
        self.start_byte = start_byte
        self.end_byte = end_byte

    @classmethod
    def from_pack(cls, item: Any, parent_name: str | None) -> _StructItem:
        # Note: tree-sitter-language-pack reports kind as an enum value
        # (e.g. ``StructureKind.Function``); we stringify it for clarity.
        kind = str(item.kind).rsplit(".", 1)[-1]
        name = item.name or "<anon>"
        if parent_name:
            name = f"{parent_name}.{name}"
        return cls(
            kind=kind,
            name=name,
            start_byte=item.span.start_byte,
            end_byte=item.span.end_byte,
        )


def _make_textchunk(
    body: str,
    metadata: dict[str, Any],
    relative_path: str | None,
) -> TextChunk:
    """Build a TextChunk, prepending the standard header when path supplied."""
    if relative_path and metadata.get("name"):
        kind_label = metadata.get("kind", "Block")
        header = f"# {relative_path} :: {kind_label} {metadata['name']}\n"
        text = header + body
    else:
        text = body
    return TextChunk(text=text, metadata=dict(metadata))


# ── Lazy-fetch helper (mirror of the extractor's policy) ─────────────────
#
# Shared state with :mod:`corpus_forge.extractors.code._ensure_grammar`:
# both touch the same on-disk pack cache and the same in-process attempt
# cache. Keeping a thin local copy here means a direct ``CodeChunker(...).
# chunk(text, language="python")`` call works on Linux too — not just the
# extractor-mediated path.

_CHUNKER_FETCH_CACHE: dict[str, bool] = {}


def _ensure_grammar_for_chunker(language: str) -> None:
    """Idempotent grammar download — see ``extractors.code._ensure_grammar``.

    Mirrors the extractor's gate: a language is fetchable when it is
    either already in ``available_languages()`` OR still listed in
    ``manifest_languages()``. Using ``available_languages()`` alone
    suppresses the very first download on a fresh wheel (the set is
    empty until ``pack.download(...)`` populates it).
    """
    if language in _CHUNKER_FETCH_CACHE:
        return
    try:
        import tree_sitter_language_pack as pack  # noqa: PLC0415
    except ImportError:  # pragma: no cover — only when [code] missing
        _CHUNKER_FETCH_CACHE[language] = False
        return

    supported = False
    try:
        supported = language in pack.available_languages()
    except Exception:  # pragma: no cover — defensive
        supported = False
    if not supported:
        try:
            supported = language in pack.manifest_languages()
        except Exception:  # pragma: no cover — defensive
            supported = False

    if not supported:
        _CHUNKER_FETCH_CACHE[language] = False
        return

    try:
        logger.info("Ensuring tree-sitter grammar is cached: %s", language)
        pack.download([language])
        _CHUNKER_FETCH_CACHE[language] = True
    except Exception as exc:
        logger.warning(
            "Lazy-fetch of tree-sitter grammar %r failed (%s); "
            "CodeChunker byte-line fallback will be used.",
            language,
            exc,
        )
        _CHUNKER_FETCH_CACHE[language] = False


def __getattr__(name: str) -> Any:  # pragma: no cover — convenience
    """Convenience: tests can import :func:`available_languages` via this module."""
    if name == "available_languages":

        def _wrapper() -> Iterable[str]:
            import tree_sitter_language_pack as pack  # noqa: PLC0415

            return pack.available_languages()

        return _wrapper
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
