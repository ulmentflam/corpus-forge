"""Jupyter notebook extractor.

Phase D / Wave 1 — D-11.

Strategy: ``jupytext.read(path)`` parses ``.ipynb`` into an in-memory
notebook object whose cells we walk in source order.

- Markdown cells: emitted verbatim.
- Code cells: wrapped in a fenced block tagged with the kernel
  language (default ``python`` when ``kernelspec`` is missing).
- Output cells (``cell.outputs``): dropped — execution output is RAG
  noise that drowns out the actual prose / code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ExtractedDocument

_DEFAULT_KERNEL_LANGUAGE = "python"


def _kernel_language(notebook: Any) -> str:
    """Return ``notebook.metadata.kernelspec.language`` or the default."""
    meta = getattr(notebook, "metadata", None)
    if meta is None:
        return _DEFAULT_KERNEL_LANGUAGE
    # `metadata` is dict-like (jupytext returns nbformat.NotebookNode).
    kernelspec = meta.get("kernelspec") if hasattr(meta, "get") else None
    if not kernelspec:
        return _DEFAULT_KERNEL_LANGUAGE
    lang = kernelspec.get("language") if hasattr(kernelspec, "get") else None
    return str(lang) if lang else _DEFAULT_KERNEL_LANGUAGE


def _render_cell(cell: Any, language: str) -> str:
    """Render a single cell. Markdown verbatim, code in a fenced block."""
    src = cell.source if hasattr(cell, "source") else cell.get("source", "")
    if isinstance(src, list):
        # nbformat occasionally hands us a list of lines.
        src = "".join(src)
    if cell.cell_type == "markdown":
        return src
    if cell.cell_type == "code":
        return f"```{language}\n{src}\n```"
    # "raw" cells: emit as plain text without a fence — matches Jupyter's
    # own convention.
    return src


class NotebookExtractor:
    """Reads ``.ipynb`` → Markdown (cells in source order, outputs dropped)."""

    supported_extensions: tuple[str, ...] = (".ipynb",)

    def extract(self, path: Path) -> ExtractedDocument:
        import jupytext  # noqa: PLC0415

        # ``jupytext.read`` is untyped — its return type is
        # ``nbformat.NotebookNode`` (a dict-with-attribute-access) but
        # pyrefly resolves it to ``list``. Treat as Any locally.
        notebook: Any = jupytext.read(str(path))
        language = _kernel_language(notebook)

        cells = list(notebook.cells)
        rendered_cells: list[str] = [_render_cell(cell, language) for cell in cells]

        text = "\n\n".join(rendered_cells)

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata={
                "cell_count": len(cells),
                "kernel": language,
                "extractor": "jupytext",
            },
            labels=[("format", "ipynb"), ("kernel", language)],
        )
