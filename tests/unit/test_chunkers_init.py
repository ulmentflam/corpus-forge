"""Cover the ``corpus_forge.chunkers.__getattr__`` lazy-import shim.

Production code paths import from ``corpus_forge.chunkers.code`` or
``corpus_forge.chunkers.cdc`` directly, but users (and downstream
extension packages) commonly do ``from corpus_forge.chunkers import
CodeChunker`` / ``CDCChunker``. The ``__getattr__`` hook in
``corpus_forge/chunkers/__init__.py`` is what makes that work without
forcing tree-sitter / fastcdc imports at package load. These tests
pin the lazy path so a regression (eg. dropping a member from the
hook) gets caught.
"""

from __future__ import annotations

import pytest


def test_lazy_resolves_code_chunker() -> None:
    """``from corpus_forge.chunkers import CodeChunker`` resolves
    via ``__getattr__`` → ``.code`` submodule."""
    from corpus_forge.chunkers import CodeChunker
    from corpus_forge.chunkers.code import CodeChunker as ConcreteCodeChunker

    assert CodeChunker is ConcreteCodeChunker


def test_lazy_resolves_cdc_chunker() -> None:
    """``from corpus_forge.chunkers import CDCChunker`` resolves
    via ``__getattr__`` → ``.cdc`` submodule."""
    from corpus_forge.chunkers import CDCChunker
    from corpus_forge.chunkers.cdc import CDCChunker as ConcreteCDCChunker

    assert CDCChunker is ConcreteCDCChunker


def test_lazy_unknown_attribute_raises() -> None:
    """Any other attribute access raises ``AttributeError`` with a
    message that names both the module and the missing attribute."""
    import corpus_forge.chunkers as chunkers_pkg

    with pytest.raises(AttributeError) as exc_info:
        _ = chunkers_pkg.NotAChunker  # type: ignore[attr-defined]
    msg = str(exc_info.value)
    assert "corpus_forge.chunkers" in msg
    assert "NotAChunker" in msg


def test_eager_re_exports_still_work() -> None:
    """The eager re-exports (Chunker / TextChunk / MarkdownChunker /
    ConversationChunker / PassthroughChunker) bypass ``__getattr__``
    and resolve at import time. Verify the surface."""
    from corpus_forge.chunkers import (
        Chunker,
        ConversationChunker,
        MarkdownChunker,
        PassthroughChunker,
        TextChunk,
    )

    assert all(
        cls is not None
        for cls in (Chunker, ConversationChunker, MarkdownChunker, PassthroughChunker, TextChunk)
    )


def test_dunder_all_lists_every_public_member() -> None:
    """``__all__`` must enumerate every public chunker so static
    analysis tools (and ``help(...)``) see the canonical surface."""
    import corpus_forge.chunkers as chunkers_pkg

    expected = {
        "CDCChunker",
        "Chunker",
        "CodeChunker",
        "ConversationChunker",
        "MarkdownChunker",
        "PassthroughChunker",
        "TextChunk",
        "enforce_chunk_hard_max",
    }
    assert set(chunkers_pkg.__all__) == expected
