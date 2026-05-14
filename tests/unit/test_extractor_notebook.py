"""Unit tests for D-11: NotebookExtractor.

Strategy: ``jupytext.read(path)`` → iterate cells; markdown cells emit
verbatim, code cells become fenced blocks tagged with the kernel
language. Output cells are dropped (RAG noise).
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.notebook import NotebookExtractor

# ── Fixture helpers ──────────────────────────────────────────────────


def _build_notebook(
    tmp_path: Path,
    *,
    cells: tuple[tuple[str, str], ...] = (
        ("markdown", "# Title\nSome markdown content"),
        ("code", 'print("hi")'),
    ),
    kernel_language: str | None = "python",
    name: str = "nb.ipynb",
) -> Path:
    import nbformat

    nb = nbformat.v4.new_notebook()
    if kernel_language is not None:
        nb.metadata["kernelspec"] = {
            "language": kernel_language,
            "name": kernel_language,
            "display_name": kernel_language.capitalize(),
        }
    for kind, src in cells:
        if kind == "markdown":
            cell = nbformat.v4.new_markdown_cell(src)
        elif kind == "code":
            cell = nbformat.v4.new_code_cell(src)
            # Attach a fake output that the extractor must drop.
            cell.outputs = [
                nbformat.v4.new_output("stream", name="stdout", text="should-be-dropped")
            ]
        else:  # pragma: no cover — defensive
            raise ValueError(f"unknown cell kind {kind!r}")
        nb.cells.append(cell)
    target = tmp_path / name
    with target.open("w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    return target


# ── Tests ────────────────────────────────────────────────────────────


def test_extractor_protocol_conformance():
    ex: Extractor = NotebookExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = NotebookExtractor()
    assert set(ex.supported_extensions) == {".ipynb"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = _build_notebook(tmp_path)
    doc = NotebookExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "markdown"


def test_extract_markdown_cell_verbatim(tmp_path: Path):
    p = _build_notebook(
        tmp_path,
        cells=(("markdown", "# Heading\nSome **bold** prose."),),
    )
    doc = NotebookExtractor().extract(p)
    assert "# Heading" in doc.text
    assert "**bold**" in doc.text


def test_extract_code_cell_in_fenced_block(tmp_path: Path):
    p = _build_notebook(
        tmp_path,
        cells=(("code", "x = 1\ny = 2\nprint(x + y)"),),
    )
    doc = NotebookExtractor().extract(p)
    assert "```python" in doc.text
    assert "x = 1" in doc.text
    assert "print(x + y)" in doc.text
    # Closing fence present.
    assert "```" in doc.text.split("```python", 1)[1]


def test_extract_drops_outputs(tmp_path: Path):
    """Code-cell outputs are RAG noise — must not appear in the markdown."""
    p = _build_notebook(tmp_path, cells=(("code", 'print("hi")'),))
    doc = NotebookExtractor().extract(p)
    assert "should-be-dropped" not in doc.text


def test_extract_metadata_cell_count(tmp_path: Path):
    p = _build_notebook(
        tmp_path,
        cells=(
            ("markdown", "# A"),
            ("code", "1+1"),
            ("markdown", "# B"),
            ("code", "2+2"),
        ),
    )
    doc = NotebookExtractor().extract(p)
    assert doc.metadata.get("cell_count") == 4


def test_extract_metadata_kernel(tmp_path: Path):
    p = _build_notebook(tmp_path, kernel_language="python")
    doc = NotebookExtractor().extract(p)
    assert doc.metadata.get("kernel") == "python"


def test_extract_metadata_kernel_other_language(tmp_path: Path):
    p = _build_notebook(tmp_path, kernel_language="julia")
    doc = NotebookExtractor().extract(p)
    assert doc.metadata.get("kernel") == "julia"


def test_extract_metadata_kernel_defaults_to_python_when_missing(tmp_path: Path):
    """When kernelspec is missing, the extractor defaults to Python."""
    p = _build_notebook(tmp_path, kernel_language=None)
    doc = NotebookExtractor().extract(p)
    assert doc.metadata.get("kernel") == "python"


def test_extract_metadata_extractor_tag(tmp_path: Path):
    p = _build_notebook(tmp_path)
    doc = NotebookExtractor().extract(p)
    assert doc.metadata.get("extractor") == "jupytext"


def test_extract_labels(tmp_path: Path):
    p = _build_notebook(tmp_path, kernel_language="python")
    doc = NotebookExtractor().extract(p)
    labels = {(ns, val) for ns, val in doc.labels}
    assert ("format", "ipynb") in labels
    assert ("kernel", "python") in labels


def test_extract_code_fence_uses_kernel_language(tmp_path: Path):
    p = _build_notebook(
        tmp_path,
        kernel_language="r",
        cells=(("code", "x <- 1"),),
    )
    doc = NotebookExtractor().extract(p)
    assert "```r" in doc.text


def test_extract_language_is_none(tmp_path: Path):
    """``ExtractedDocument.language`` is reserved for the code chunker
    surface. Notebooks return markdown overall — no language."""
    p = _build_notebook(tmp_path)
    doc = NotebookExtractor().extract(p)
    assert doc.language is None


def test_extract_mixed_cells_interleave(tmp_path: Path):
    """Output must preserve the source order of cells."""
    p = _build_notebook(
        tmp_path,
        cells=(
            ("markdown", "# Section A"),
            ("code", "print('one')"),
            ("markdown", "# Section B"),
            ("code", "print('two')"),
        ),
    )
    doc = NotebookExtractor().extract(p)
    pos_a = doc.text.index("Section A")
    pos_b = doc.text.index("Section B")
    pos_one = doc.text.index("'one'")
    pos_two = doc.text.index("'two'")
    assert pos_a < pos_one < pos_b < pos_two


def test_lazy_import_does_not_load_jupytext_on_module_import():
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.notebook as m; "
        "assert 'jupytext' not in sys.modules, sorted(sys.modules); "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_registry_wires_notebook_extractor(tmp_path: Path):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / "x.ipynb"
    p.write_bytes(b"{}")
    extractor = reg.get_for(p)
    assert isinstance(extractor, NotebookExtractor)
