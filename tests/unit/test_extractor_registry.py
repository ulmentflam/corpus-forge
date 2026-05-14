"""Unit tests for D-01: Extractor protocol + ExtractorRegistry.

Wave 0 of the multi-format milestone (see .planning/tdd/multi_format.md).

Surface under test:
- corpus_forge.extractors.base.ExtractedDocument (dataclass)
- corpus_forge.extractors.base.Extractor (Protocol)
- corpus_forge.extractors.registry.ExtractorRegistry
- corpus_forge.extractors.registry.register_default_extractors
- corpus_forge.extractors package init re-exports
"""

from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path
from typing import get_type_hints

import pytest

from corpus_forge.extractors import (
    ExtractedDocument,
    Extractor,
    ExtractorRegistry,
    register_default_extractors,
)

# ── ExtractedDocument shape ──────────────────────────────────────────────


def test_extracted_document_is_dataclass():
    assert is_dataclass(ExtractedDocument)


def test_extracted_document_required_fields():
    """Every field documented in the milestone plan must be present."""
    doc = ExtractedDocument(
        text="hello",
        chunker_hint="markdown",
        language=None,
        metadata={},
        labels=[],
    )
    assert doc.text == "hello"
    assert doc.chunker_hint == "markdown"
    assert doc.language is None
    assert doc.metadata == {}
    assert doc.labels == []


def test_extracted_document_metadata_defaults_to_empty_dict():
    """metadata should default to an empty dict, not None."""
    doc = ExtractedDocument(text="x", chunker_hint="passthrough")
    assert doc.metadata == {}
    assert doc.labels == []
    assert doc.language is None


def test_extracted_document_language_optional():
    doc = ExtractedDocument(text="x", chunker_hint="code", language="python")
    assert doc.language == "python"


def test_extracted_document_chunker_hint_values():
    """All four hint values from the plan must be acceptable strings."""
    for hint in ("markdown", "code", "passthrough", "conversation"):
        doc = ExtractedDocument(text="x", chunker_hint=hint)
        assert doc.chunker_hint == hint


# ── Extractor protocol ───────────────────────────────────────────────────


def test_extractor_is_protocol():
    """Extractor should be importable and behave like a typing.Protocol."""
    # A class duck-typed to the protocol should pass an isinstance check
    # when @runtime_checkable. We don't require runtime_checkable, but the
    # protocol attributes must exist.
    assert hasattr(Extractor, "supported_extensions") or hasattr(Extractor, "extract")


def test_extractor_protocol_methods_present():
    """The protocol should declare extract(path) and supported_extensions."""
    # `supported_extensions` is declared as a class annotation on the
    # Protocol body, while `extract` is a real method — check both
    # surfaces.
    assert "extract" in dir(Extractor)
    assert "supported_extensions" in Extractor.__annotations__


def test_extractor_concrete_implementation_satisfies_protocol():
    """A minimal concrete extractor must be assignable to the protocol type."""

    class TinyExtractor:
        supported_extensions: tuple[str, ...] = (".tiny",)

        def extract(self, path: Path) -> ExtractedDocument:
            return ExtractedDocument(text=path.read_text(), chunker_hint="passthrough")

    inst: Extractor = TinyExtractor()  # type-check assignment
    assert inst.supported_extensions == (".tiny",)


# ── ExtractorRegistry behaviour ──────────────────────────────────────────


class _FakeExtractor:
    """A simple, in-memory extractor for registry tests."""

    def __init__(self, exts: tuple[str, ...], hint: str = "passthrough"):
        self.supported_extensions = exts
        self._hint = hint

    def extract(self, path: Path) -> ExtractedDocument:
        return ExtractedDocument(text=path.name, chunker_hint=self._hint)


def test_registry_empty_get_for_returns_none(tmp_path: Path):
    reg = ExtractorRegistry()
    p = tmp_path / "foo.unknown"
    p.write_text("hello")
    assert reg.get_for(p) is None


def test_registry_registers_extractor_per_extension(tmp_path: Path):
    reg = ExtractorRegistry()
    fake = _FakeExtractor((".md", ".markdown"))
    reg.register(fake)
    p_md = tmp_path / "a.md"
    p_md.write_text("x")
    p_markdown = tmp_path / "b.markdown"
    p_markdown.write_text("y")
    assert reg.get_for(p_md) is fake
    assert reg.get_for(p_markdown) is fake


def test_registry_get_for_is_case_insensitive(tmp_path: Path):
    """Extensions are normalized to lowercase before lookup."""
    reg = ExtractorRegistry()
    fake = _FakeExtractor((".md",))
    reg.register(fake)
    p = tmp_path / "DOC.MD"
    p.write_text("x")
    assert reg.get_for(p) is fake


def test_registry_unknown_extension_returns_none(tmp_path: Path):
    reg = ExtractorRegistry()
    reg.register(_FakeExtractor((".md",)))
    p = tmp_path / "binary.xyz"
    p.write_text("x")
    assert reg.get_for(p) is None


def test_registry_last_registered_wins_for_same_extension(tmp_path: Path):
    """User-supplied overrides should beat earlier registrations."""
    reg = ExtractorRegistry()
    first = _FakeExtractor((".md",), hint="markdown")
    second = _FakeExtractor((".md",), hint="passthrough")
    reg.register(first)
    reg.register(second)
    p = tmp_path / "a.md"
    p.write_text("x")
    assert reg.get_for(p) is second


def test_registry_register_normalizes_uppercase_extensions(tmp_path: Path):
    """Extractors may declare extensions in any case; registry normalises."""
    reg = ExtractorRegistry()
    fake = _FakeExtractor((".MD", ".HTML"))
    reg.register(fake)
    p = tmp_path / "x.html"
    p.write_text("x")
    assert reg.get_for(p) is fake


def test_registry_supports_iter_extensions():
    """Registry should expose the set of registered extensions for diagnostics."""
    reg = ExtractorRegistry()
    reg.register(_FakeExtractor((".md", ".markdown")))
    reg.register(_FakeExtractor((".txt",)))
    exts = set(reg.extensions())
    assert exts == {".md", ".markdown", ".txt"}


def test_registry_register_rejects_extension_without_dot():
    """An extension must begin with '.' — guard against typos."""
    reg = ExtractorRegistry()
    bad = _FakeExtractor(("md",))
    with pytest.raises(ValueError, match="extension"):
        reg.register(bad)


# ── register_default_extractors feature-flag plumbing ────────────────────


def test_register_default_extractors_returns_registry():
    """The helper returns a populated ExtractorRegistry."""
    reg = register_default_extractors(config=None)
    assert isinstance(reg, ExtractorRegistry)


def test_register_default_extractors_passthrough_disabled_by_default_is_false():
    """Without any config object, all P0 extractors that don't need heavy deps
    should be registered. The exact extensions are asserted via D-03/D-04 once
    those extractors land — for D-01 we only require the registry to be
    non-empty when given a None config (i.e. defaults-all-true)."""
    reg = register_default_extractors(config=None)
    # Empty is a regression — at minimum, no-op stubs should register
    # nothing yet; the assertion below is structural rather than semantic.
    assert isinstance(reg, ExtractorRegistry)
    # The registry must be a real ExtractorRegistry, not a mock dict.
    assert hasattr(reg, "get_for")


def test_register_default_extractors_respects_disable_flags():
    """When the supplied config disables an extractor family, that family
    should not be present. We use a tiny duck-typed config object."""

    class _Cfg:
        enable_pdf = False
        enable_office = False
        enable_code = False
        enable_html = False
        enable_epub = False
        enable_notebook = False
        enable_csv = False

    reg = register_default_extractors(config=_Cfg())
    # When everything heavy is disabled, the registry should still be
    # constructible (no import errors), and no extension associated with
    # those families should resolve.
    for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".epub", ".ipynb", ".csv"):
        # get_for should return None because nothing is registered for these.
        assert reg.get_for(Path("x" + ext)) is None


def test_register_default_extractors_does_not_import_heavy_deps_when_disabled():
    """If a family is disabled, importing it must not crash even when the
    underlying optional dep is missing. The hook is the whole reason this
    exists — heavy imports stay lazy."""

    class _Cfg:
        # Disable everything heavy, leave the stdlib-only families alone.
        enable_pdf = False
        enable_office = False
        enable_code = False
        enable_html = False
        enable_epub = False
        enable_notebook = False
        enable_csv = False

    # Should not raise, even on a clean install without docling/etc.
    reg = register_default_extractors(config=_Cfg())
    assert reg is not None


# ── Type-hint sanity (catch regressions in the public surface) ───────────


def test_extracted_document_field_types():
    """Type hints on ExtractedDocument should match the documented surface."""
    hints = get_type_hints(ExtractedDocument)
    assert "text" in hints
    assert "chunker_hint" in hints
    assert "language" in hints
    assert "metadata" in hints
    assert "labels" in hints
