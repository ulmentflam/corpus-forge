"""Unit tests for D-08: HtmlExtractor.

Strategy: ``readability.Document(raw_html).summary()`` (boilerplate
stripping) → ``markdownify.markdownify(...)`` with ATX headings and
dash bullets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.html import HtmlExtractor

_ARTICLE_HTML = """<!doctype html>
<html><head><title>The Test Page</title></head>
<body>
<nav>navigation cruft</nav>
<script>evil();</script>
<style>body{color:red}</style>
<article>
  <h1>Main Heading</h1>
  <p>Lead paragraph that should easily survive the boilerplate stripper.</p>
  <h2>Subhead</h2>
  <p>Another paragraph with <strong>bold</strong> and <em>italic</em>.</p>
  <ul><li>one</li><li>two</li><li>three</li></ul>
</article>
<footer>footer cruft</footer>
</body></html>
"""


def test_extractor_protocol_conformance():
    ex: Extractor = HtmlExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = HtmlExtractor()
    assert set(ex.supported_extensions) == {".html", ".htm", ".xhtml"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "markdown"


def test_extract_text_contains_content(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    # Main content survives.
    assert "Main Heading" in doc.text
    assert "Lead paragraph" in doc.text


def test_extract_strips_script_and_style(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert "evil()" not in doc.text
    assert "color:red" not in doc.text


def test_extract_uses_atx_headings(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    # ATX heading style → "# Main Heading" rather than underline.
    assert "# Main Heading" in doc.text


def test_extract_uses_dash_bullets(tmp_path: Path):
    """Bullets must be rendered as ``-`` not ``*`` or ``+`` (markdownify
    default). Use a list-only fixture so readability does not prune the
    UL as "boilerplate" — its boilerplate detector trims small fragments
    on pages that are mostly prose."""
    p = tmp_path / "page.html"
    list_html = """<!doctype html>
<html><head><title>Top Items</title></head>
<body>
<article>
  <h1>Top Five Programming Languages</h1>
  <p>According to recent surveys these languages topped the chart this year:</p>
  <ul>
    <li>one is the loneliest number</li>
    <li>two for the show</li>
    <li>three for the money</li>
    <li>four for me</li>
    <li>five alive and kicking</li>
  </ul>
  <p>That concludes the list.</p>
</article>
</body></html>"""
    p.write_text(list_html, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    # markdownify with bullets="-" renders <li> as "- item".
    assert "- one is the loneliest number" in doc.text
    # Negative: no asterisk-style bullets leaked through.
    assert "* one is the loneliest" not in doc.text


def test_extract_metadata_title(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    # readability returns the document title.
    assert doc.metadata.get("title") == "The Test Page"


def test_extract_metadata_extractor_tag(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert doc.metadata.get("extractor") == "html"


def test_extract_labels(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert ("format", "html") in {(ns, val) for ns, val in doc.labels}


def test_extract_htm_extension(tmp_path: Path):
    p = tmp_path / "page.htm"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert doc.chunker_hint == "markdown"
    assert "Main Heading" in doc.text


def test_extract_xhtml_extension(tmp_path: Path):
    p = tmp_path / "page.xhtml"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert doc.chunker_hint == "markdown"


def test_extract_language_is_none(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text(_ARTICLE_HTML, encoding="utf-8")
    doc = HtmlExtractor().extract(p)
    assert doc.language is None


def test_lazy_import_does_not_load_on_module_import():
    """Importing the extractor module must not load readability/markdownify.

    Uses an isolated subprocess so we don't poison ``sys.modules`` for
    later tests (the registry tests assert class-identity equality with
    the already-imported ``HtmlExtractor``).
    """
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.html as m; "
        "assert 'readability' not in sys.modules, sorted(sys.modules); "
        "assert 'markdownify' not in sys.modules, sorted(sys.modules); "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_registry_wires_html_extractor(tmp_path: Path):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / "x.html"
    p.write_text("<p>hi</p>", encoding="utf-8")
    extractor = reg.get_for(p)
    assert isinstance(extractor, HtmlExtractor)


@pytest.mark.parametrize("ext", [".html", ".htm", ".xhtml"])
def test_registry_handles_all_html_extensions(tmp_path: Path, ext: str):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / f"x{ext}"
    p.write_text("<p>hi</p>", encoding="utf-8")
    extractor = reg.get_for(p)
    assert isinstance(extractor, HtmlExtractor)


def test_extract_minimal_html(tmp_path: Path):
    p = tmp_path / "tiny.html"
    p.write_text(
        "<html><body><h1>X</h1><p>Some real prose body text right here.</p></body></html>",
        encoding="utf-8",
    )
    doc = HtmlExtractor().extract(p)
    assert doc.text.strip()
