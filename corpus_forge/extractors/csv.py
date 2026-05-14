"""CSV / TSV extractor.

Phase D / Wave 1 — D-12.

Strategy: ``pandas.read_csv`` (with ``sep="\\t"`` for ``.tsv``) →
``df.to_markdown(index=False)``. Tables longer than ``max_rows`` (default
200, configurable via :attr:`ExtractionConfig.csv_max_rows`) are sampled
via ``head(max_rows)`` and the result flagged ``metadata.truncated =
True`` so callers can decide whether to follow up.

pandas pulls a meaningful chunk of NumPy/Arrow on import — lazy-import
inside :meth:`extract` keeps the core install light.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument

# Mirrors :class:`corpus_forge.config.ExtractionConfig.csv_max_rows`.
_DEFAULT_MAX_ROWS = 200

# Map file extension → (separator-for-pandas, format-label).
_SEP_BY_EXT: dict[str, tuple[str, str]] = {
    ".csv": (",", "csv"),
    ".tsv": ("\t", "tsv"),
}


class CsvExtractor:
    """Reads ``.csv`` / ``.tsv`` → Markdown table via pandas."""

    supported_extensions: tuple[str, ...] = (".csv", ".tsv")

    def __init__(self, max_rows: int = _DEFAULT_MAX_ROWS):
        if max_rows <= 0:
            raise ValueError(f"max_rows must be > 0, got {max_rows}")
        self.max_rows = max_rows

    def extract(self, path: Path) -> ExtractedDocument:
        import pandas as pd  # noqa: PLC0415

        ext = path.suffix.lower()
        sep, fmt = _SEP_BY_EXT.get(ext, (",", "csv"))

        df = pd.read_csv(path, sep=sep)
        total_rows = len(df)

        truncated = total_rows > self.max_rows
        if truncated:
            df = df.head(self.max_rows)

        text = df.to_markdown(index=False)
        # pandas can return None for empty frames; guard it.
        if text is None:  # pragma: no cover — defensive
            text = ""

        metadata: dict = {
            "row_count": len(df),
            "column_count": len(df.columns),
            "extractor": "pandas",
            "truncated": truncated,
        }
        if truncated:
            metadata["total_rows"] = total_rows

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata=metadata,
            labels=[("format", fmt)],
        )
