"""Unit tests for D-09: EpubExtractor.

Strategy: ``ebooklib.epub.read_epub(path)`` → iterate
``ITEM_DOCUMENT`` chapters → ``markdownify`` each → join with
``\\n\\n---\\n\\n``.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.epub import EpubExtractor

# ── Fixture helpers ──────────────────────────────────────────────────


def _build_epub(
    tmp_path: Path,
    *,
    title: str = "Test Book",
    author: str | None = "Author Person",
    chapters: tuple[tuple[str, str], ...] = (
        ("Chapter 1", "<h1>Chapter 1</h1><p>The first chapter body.</p>"),
        ("Chapter 2", "<h1>Chapter 2</h1><p>The second chapter body.</p>"),
    ),
    include_nav: bool = True,
    name: str = "book.epub",
) -> Path:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("id_test")
    book.set_title(title)
    book.set_language("en")
    if author:
        book.add_author(author)

    items = []
    for i, (chap_title, html) in enumerate(chapters, start=1):
        c = epub.EpubHtml(title=chap_title, file_name=f"chap_{i:02d}.xhtml", lang="en")
        c.content = html
        book.add_item(c)
        items.append(c)
    book.toc = tuple(items)
    # ebooklib.epub.write_epub needs the ncx + nav documents to build a
    # spec-compliant archive; ``include_nav`` only controls whether the
    # nav.xhtml item shows up in the spine (and therefore in
    # ``get_items_of_type(ITEM_DOCUMENT)``).
    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav()
    book.add_item(nav)
    if include_nav:
        book.spine = ["nav", *items]
    else:
        # Spine still needs the toc anchor for the writer not to crash.
        book.spine = ["nav", *items]
        # ...but we drop the nav item so it doesn't appear under
        # ITEM_DOCUMENT iteration.
        book.items = [it for it in book.items if it is not nav]

    target = tmp_path / name
    epub.write_epub(str(target), book)
    return target


# ── Tests ────────────────────────────────────────────────────────────


def test_extractor_protocol_conformance():
    ex: Extractor = EpubExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = EpubExtractor()
    assert set(ex.supported_extensions) == {".epub"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = _build_epub(tmp_path)
    doc = EpubExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "markdown"


def test_extract_text_contains_chapter_content(tmp_path: Path):
    p = _build_epub(tmp_path)
    doc = EpubExtractor().extract(p)
    assert "first chapter body" in doc.text
    assert "second chapter body" in doc.text


def test_extract_text_joins_chapters_with_separator(tmp_path: Path):
    p = _build_epub(tmp_path, include_nav=False)
    doc = EpubExtractor().extract(p)
    assert "\n\n---\n\n" in doc.text


def test_extract_metadata_title(tmp_path: Path):
    p = _build_epub(tmp_path, title="My Book Title")
    doc = EpubExtractor().extract(p)
    assert doc.metadata.get("title") == "My Book Title"


def test_extract_metadata_author(tmp_path: Path):
    p = _build_epub(tmp_path, author="Alice Tester")
    doc = EpubExtractor().extract(p)
    assert doc.metadata.get("author") == "Alice Tester"


def test_extract_metadata_no_author_returns_none(tmp_path: Path):
    p = _build_epub(tmp_path, author=None)
    doc = EpubExtractor().extract(p)
    assert doc.metadata.get("author") is None


def test_extract_metadata_chapter_count(tmp_path: Path):
    chapters = (
        ("C1", "<p>One body text content here.</p>"),
        ("C2", "<p>Two body text content here.</p>"),
        ("C3", "<p>Three body text content here.</p>"),
    )
    p = _build_epub(tmp_path, chapters=chapters, include_nav=False)
    doc = EpubExtractor().extract(p)
    assert doc.metadata.get("chapter_count") == 3


def test_extract_metadata_extractor_tag(tmp_path: Path):
    p = _build_epub(tmp_path)
    doc = EpubExtractor().extract(p)
    assert doc.metadata.get("extractor") == "epub"


def test_extract_labels(tmp_path: Path):
    p = _build_epub(tmp_path)
    doc = EpubExtractor().extract(p)
    assert ("format", "epub") in {(ns, val) for ns, val in doc.labels}


def test_extract_language_is_none(tmp_path: Path):
    p = _build_epub(tmp_path)
    doc = EpubExtractor().extract(p)
    assert doc.language is None


def test_extract_uses_atx_headings(tmp_path: Path):
    """Chapter headings should appear as ATX in the output Markdown."""
    p = _build_epub(tmp_path, include_nav=False)
    doc = EpubExtractor().extract(p)
    assert "# Chapter 1" in doc.text


def test_lazy_import_does_not_load_on_module_import():
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.epub as m; "
        "assert 'ebooklib' not in sys.modules, sorted(sys.modules); "
        "assert 'markdownify' not in sys.modules, sorted(sys.modules); "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_registry_wires_epub_extractor(tmp_path: Path):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / "x.epub"
    p.write_bytes(b"placeholder")
    extractor = reg.get_for(p)
    assert isinstance(extractor, EpubExtractor)


def test_extract_single_chapter(tmp_path: Path):
    """One-chapter book → no separator needed but text non-empty."""
    chapters = (("C1", "<p>Only chapter body content.</p>"),)
    p = _build_epub(tmp_path, chapters=chapters, include_nav=False)
    doc = EpubExtractor().extract(p)
    assert "Only chapter body content" in doc.text
