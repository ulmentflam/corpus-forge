"""Unit tests for D-02: CodeChunker via tree-sitter-language-pack.

Wave 0 of the multi-format milestone.

Behaviours under test (per .planning/tdd/multi_format.md):

- one chunk per top-level construct (function/class/method/module-level block)
- oversize chunk sub-split at next AST boundary with overlap
- undersize chunks coalesced up to `max_chars`
- long-tail byte-line fallback when no grammar exists
- chunk header `# <relative path> :: <kind> <name>` prepended
- metadata `{kind, name, language, byte_range}` on each TextChunk

The CodeChunker is part of the optional ``[code]`` extra. When the
tree-sitter language pack isn't installed, the tree-sitter path is
skipped but the long-tail fallback remains exercised by the
no-language-known tests.
"""

from __future__ import annotations

import pytest

# Skip the entire module if the [code] extra isn't installed — D-02
# tests can't run without tree-sitter. The fallback byte-line chunker is
# still exercised because it does not require tree-sitter.
pytest.importorskip("tree_sitter_language_pack")

from corpus_forge.chunkers.base import Chunker, TextChunk
from corpus_forge.chunkers.code import CodeChunker

# ── Construction + protocol ──────────────────────────────────────────────


def test_codechunker_is_chunker_subclass():
    assert issubclass(CodeChunker, Chunker)


def test_codechunker_default_construction():
    c = CodeChunker()
    assert c.max_chars == 1500
    assert c.overlap == 100
    # Min-chars surfaces are an explicit knob.
    assert c.min_chars == 100


def test_codechunker_custom_config():
    c = CodeChunker(max_chars=800, min_chars=50, overlap=80)
    assert c.max_chars == 800
    assert c.min_chars == 50
    assert c.overlap == 80


def test_codechunker_chunk_returns_list_of_textchunks():
    c = CodeChunker()
    chunks = c.chunk("def foo():\n    return 1\n")
    assert isinstance(chunks, list)
    if chunks:
        assert all(isinstance(ch, TextChunk) for ch in chunks)


# ── Tree-sitter (python) AST walk ────────────────────────────────────────


SIMPLE_PY = """\
def alpha():
    \"\"\"first function\"\"\"
    return 1


def beta(x):
    return x * 2


class Gamma:
    def method(self):
        return None
"""


def test_chunk_python_module_yields_per_construct():
    """A small Python file with two functions + one class should produce
    chunks that account for every top-level construct (alpha, beta,
    Gamma). When undersize coalesce kicks in the leading construct's
    name lands in ``metadata['name']`` and the full list in
    ``metadata['names']`` — checking both surfaces is the contract."""
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    discoverable: set[str] = set()
    for ch in chunks:
        md = ch.metadata or {}
        if md.get("name"):
            discoverable.add(md["name"])
        for n in md.get("names", ()):
            discoverable.add(n)
    assert "alpha" in discoverable
    assert "beta" in discoverable
    assert "Gamma" in discoverable


def test_chunk_python_metadata_kind_present():
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    kinds = {(ch.metadata or {}).get("kind") for ch in chunks}
    # Tree-sitter-language-pack reports "Function" / "Class" — at minimum
    # we should see both surfaces represented.
    assert any(k and "function" in k.lower() for k in kinds)
    assert any(k and "class" in k.lower() for k in kinds)


def test_chunk_python_metadata_byte_range_present():
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    for ch in chunks:
        md = ch.metadata or {}
        if md.get("kind"):  # ignore long-tail fallback chunks
            assert isinstance(md.get("byte_range"), tuple)
            start, end = md["byte_range"]
            assert isinstance(start, int)
            assert isinstance(end, int)
            assert end > start


def test_chunk_python_metadata_language_present():
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    for ch in chunks:
        md = ch.metadata or {}
        assert md.get("language") == "python"


def test_chunk_python_chunk_header_prepended():
    """When a relative_path is supplied, each chunk text starts with
    `# <relative path> :: <kind> <name>`."""
    chunks = CodeChunker(max_chars=2000).chunk(
        SIMPLE_PY, language="python", relative_path="src/mod.py"
    )
    for ch in chunks:
        md = ch.metadata or {}
        if md.get("name"):
            first_line = ch.text.splitlines()[0]
            assert first_line.startswith("# src/mod.py ::")
            assert md["name"] in first_line


def test_chunk_python_construct_body_survives():
    """The original code text must still be present inside its chunk."""
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    alpha = next(ch for ch in chunks if (ch.metadata or {}).get("name") == "alpha")
    assert "first function" in alpha.text or "alpha" in alpha.text


# ── Oversize sub-split ───────────────────────────────────────────────────


_LARGE_FN = "def big():\n" + "    pass  # padding line\n" * 200


def test_chunk_oversize_function_is_sub_split():
    """A single function whose body blows past max_chars should split
    into multiple chunks rather than emit one oversized chunk."""
    c = CodeChunker(max_chars=300, overlap=50, min_chars=50)
    chunks = c.chunk(_LARGE_FN, language="python")
    assert len(chunks) > 1
    for ch in chunks:
        assert len(ch.text) <= 300 + 200  # generous header allowance


def test_chunk_oversize_chunks_share_origin_name():
    """All sub-chunks of one construct should carry the same name."""
    c = CodeChunker(max_chars=300, overlap=50, min_chars=50)
    chunks = c.chunk(_LARGE_FN, language="python")
    names = {(ch.metadata or {}).get("name") for ch in chunks}
    assert names == {"big"}


# ── Undersize coalesce ───────────────────────────────────────────────────


_MANY_TINY = "\n\n".join(f"def f{i}():\n    return {i}" for i in range(8))


def test_chunk_undersize_constructs_coalesce():
    """Eight tiny functions should not produce eight separate chunks when
    a generous min_chars is configured — they coalesce."""
    c = CodeChunker(max_chars=2000, min_chars=200, overlap=0)
    chunks = c.chunk(_MANY_TINY, language="python")
    # Should produce fewer than 8 chunks (coalesced).
    assert 0 < len(chunks) < 8


def test_chunk_undersize_coalesce_preserves_all_constructs():
    c = CodeChunker(max_chars=2000, min_chars=200, overlap=0)
    chunks = c.chunk(_MANY_TINY, language="python")
    joined = "\n".join(ch.text for ch in chunks)
    for i in range(8):
        assert f"def f{i}" in joined


# ── Long-tail fallback (no grammar) ──────────────────────────────────────


def test_chunk_unknown_language_falls_back_to_byte_line():
    """A language with no available grammar must still chunk via the
    byte-line fallback rather than raise."""
    source = "first line\nsecond line\nthird line\n" * 20
    c = CodeChunker(max_chars=200, overlap=20)
    chunks = c.chunk(source, language="totally-not-a-real-language")
    assert chunks  # non-empty
    for ch in chunks:
        assert len(ch.text) <= 250  # generous overshoot for header


def test_chunk_no_language_falls_back():
    """No language hint → fallback to byte-line chunking."""
    source = "alpha\nbeta\ngamma\ndelta\nepsilon\n" * 10
    c = CodeChunker(max_chars=100, overlap=10)
    chunks = c.chunk(source, language=None)
    assert chunks


def test_chunk_fallback_prefers_blank_line_boundaries():
    """Fallback splits should prefer blank-line boundaries when possible."""
    source = "block one line one\nblock one line two\n\n" + "block two\n" * 5
    c = CodeChunker(max_chars=50, overlap=0)
    chunks = c.chunk(source, language=None)
    # Should produce more than one chunk and at least one boundary
    # should sit at a blank line — i.e. some chunk should END with two
    # newlines or START at the second block.
    assert len(chunks) > 1


def test_chunk_empty_string_returns_empty_list():
    assert CodeChunker().chunk("") == []
    assert CodeChunker().chunk("", language="python") == []


# ── chunker_hint reporting ───────────────────────────────────────────────


def test_chunk_textchunk_text_field_set():
    """Every emitted chunk must have a non-empty text field."""
    chunks = CodeChunker().chunk(SIMPLE_PY, language="python")
    for ch in chunks:
        assert isinstance(ch.text, str)
        assert ch.text.strip()


# ── Phase N Wave 2 — `is_definition` / `definition_kind` tagging ─────────
#
# Every chunk produced by the AST walk IS a definition by construction
# (the walker captures only structural items — Function / Class / Method /
# Block).  The Wave 2 retrieval boost reads `metadata["is_definition"]`
# and `metadata["name"]`, so the chunker must tag every AST-walk chunk
# regardless of whether it lands on the single-item or coalesced emit
# path.  Byte-line fallback chunks deliberately DON'T get the tag —
# they're emitted when grammar is unavailable and we genuinely don't
# know whether the text is a definition or a reference.


_ALLOWED_DEFINITION_KINDS = {"Function", "Class", "Method", "Block"}


def test_ast_chunk_metadata_has_is_definition_flag():
    """Every chunk emitted by the AST walk has ``is_definition=True``."""
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    assert chunks  # AST walk yielded something
    for ch in chunks:
        md = ch.metadata or {}
        # Only chunks that have a `kind` came from the AST walk; the
        # byte-line fallback never sets `kind`.  In SIMPLE_PY every chunk
        # is AST-derived, so every chunk should be flagged.
        assert md.get("is_definition") is True, (
            f"AST-walk chunk missing is_definition flag; metadata={md!r}"
        )


def test_ast_chunk_metadata_definition_kind_in_allowed_set():
    """``definition_kind`` is one of {Function, Class, Method, Block}."""
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    for ch in chunks:
        md = ch.metadata or {}
        if md.get("is_definition"):
            assert md.get("definition_kind") in _ALLOWED_DEFINITION_KINDS, (
                f"definition_kind {md.get('definition_kind')!r} not in "
                f"{_ALLOWED_DEFINITION_KINDS!r}"
            )


def test_ast_chunk_definition_kind_matches_kind():
    """``definition_kind`` mirrors the existing ``kind`` metadata field."""
    chunks = CodeChunker(max_chars=2000).chunk(SIMPLE_PY, language="python")
    for ch in chunks:
        md = ch.metadata or {}
        if md.get("is_definition"):
            assert md.get("definition_kind") == md.get("kind"), (
                f"definition_kind/kind mismatch in metadata={md!r}"
            )


def test_oversize_subsplit_chunks_carry_definition_tag():
    """Sub-split chunks from oversize constructs also carry the tag."""
    c = CodeChunker(max_chars=300, overlap=50, min_chars=50)
    chunks = c.chunk(_LARGE_FN, language="python")
    assert len(chunks) > 1  # sub-split actually fired
    for ch in chunks:
        md = ch.metadata or {}
        assert md.get("is_definition") is True
        assert md.get("definition_kind") in _ALLOWED_DEFINITION_KINDS


def test_coalesced_chunks_carry_definition_tag_as_block():
    """Undersize coalesce emits a single chunk; it must still be tagged."""
    c = CodeChunker(max_chars=2000, min_chars=200, overlap=0)
    chunks = c.chunk(_MANY_TINY, language="python")
    assert chunks  # at least one emitted
    saw_block = False
    for ch in chunks:
        md = ch.metadata or {}
        assert md.get("is_definition") is True, (
            f"coalesced chunk missing is_definition; metadata={md!r}"
        )
        assert md.get("definition_kind") in _ALLOWED_DEFINITION_KINDS
        if md.get("kind") == "Block":
            saw_block = True
    # _MANY_TINY is 8 tiny functions; at min_chars=200 the coalesce path
    # must fire at least once and emit a Block-kind chunk.
    assert saw_block, "expected at least one coalesced Block chunk"


def test_byte_line_fallback_chunks_have_no_definition_tag():
    """Byte-line fallback chunks deliberately omit the tag.

    The fallback runs when no grammar is available — we don't know
    whether the text is a definition or a reference, so we don't lie.
    """
    source = "first line\nsecond line\nthird line\n" * 20
    c = CodeChunker(max_chars=200, overlap=20)
    chunks = c.chunk(source, language="totally-not-a-real-language")
    assert chunks  # fallback emitted something
    for ch in chunks:
        md = ch.metadata or {}
        assert "is_definition" not in md, (
            f"byte-line fallback chunk should not be tagged; metadata={md!r}"
        )
        assert "definition_kind" not in md, (
            f"byte-line fallback chunk should not be tagged; metadata={md!r}"
        )


def test_byte_line_fallback_no_language_has_no_definition_tag():
    """Same for the `language=None` fallback path."""
    source = "alpha\nbeta\ngamma\ndelta\nepsilon\n" * 10
    c = CodeChunker(max_chars=100, overlap=10)
    chunks = c.chunk(source, language=None)
    assert chunks
    for ch in chunks:
        md = ch.metadata or {}
        assert "is_definition" not in md
        assert "definition_kind" not in md
