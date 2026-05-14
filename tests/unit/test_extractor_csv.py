"""Unit tests for D-12: CsvExtractor.

Strategy: ``pandas.read_csv(path, sep=...)`` → ``df.to_markdown`` with
a row-cap (``csv_max_rows``, default 200). Larger tables sample
``head(max_rows)`` and flag ``truncated=True`` in metadata.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.csv import CsvExtractor


def _write_csv(path: Path, rows: list[tuple], header: tuple = ("name", "value")) -> None:
    """Write a tiny CSV by hand (no pandas dep on test side)."""
    lines = [",".join(header)]
    for r in rows:
        lines.append(",".join(str(c) for c in r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_tsv(path: Path, rows: list[tuple], header: tuple = ("name", "value")) -> None:
    lines = ["\t".join(header)]
    for r in rows:
        lines.append("\t".join(str(c) for c in r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_extractor_protocol_conformance():
    ex: Extractor = CsvExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = CsvExtractor()
    assert set(ex.supported_extensions) == {".csv", ".tsv"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1), ("b", 2)])
    doc = CsvExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "markdown"


def test_extract_csv_renders_markdown_table(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("alpha", 1), ("beta", 2)])
    doc = CsvExtractor().extract(p)
    # Markdown table header pipe-row.
    assert "| name" in doc.text or "|name" in doc.text
    assert "alpha" in doc.text
    assert "beta" in doc.text


def test_extract_tsv_uses_tab_separator(tmp_path: Path):
    p = tmp_path / "data.tsv"
    _write_tsv(p, [("foo", 10), ("bar", 20)])
    doc = CsvExtractor().extract(p)
    assert "foo" in doc.text
    assert "bar" in doc.text


def test_extract_metadata_row_count(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1), ("b", 2), ("c", 3), ("d", 4)])
    doc = CsvExtractor().extract(p)
    assert doc.metadata.get("row_count") == 4


def test_extract_metadata_column_count(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1, "x"), ("b", 2, "y")], header=("name", "value", "tag"))
    doc = CsvExtractor().extract(p)
    assert doc.metadata.get("column_count") == 3


def test_extract_metadata_extractor_tag(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1)])
    doc = CsvExtractor().extract(p)
    assert doc.metadata.get("extractor") == "pandas"


def test_extract_metadata_truncated_false_for_small_table(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1), ("b", 2)])
    doc = CsvExtractor().extract(p)
    assert doc.metadata.get("truncated") is False


def test_extract_csv_label(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1)])
    doc = CsvExtractor().extract(p)
    assert ("format", "csv") in {(ns, val) for ns, val in doc.labels}


def test_extract_tsv_label(tmp_path: Path):
    p = tmp_path / "data.tsv"
    _write_tsv(p, [("a", 1)])
    doc = CsvExtractor().extract(p)
    assert ("format", "tsv") in {(ns, val) for ns, val in doc.labels}


def test_extract_language_is_none(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1)])
    doc = CsvExtractor().extract(p)
    assert doc.language is None


def test_extract_truncates_long_table(tmp_path: Path):
    """Tables exceeding ``max_rows`` are sampled via ``head(max_rows)``."""
    p = tmp_path / "big.csv"
    rows = [(f"r{i}", i) for i in range(300)]
    _write_csv(p, rows)
    doc = CsvExtractor(max_rows=50).extract(p)
    assert doc.metadata.get("truncated") is True
    assert doc.metadata.get("total_rows") == 300
    assert doc.metadata.get("row_count") == 50


def test_extract_no_truncation_when_under_max_rows(tmp_path: Path):
    p = tmp_path / "small.csv"
    rows = [(f"r{i}", i) for i in range(10)]
    _write_csv(p, rows)
    doc = CsvExtractor(max_rows=50).extract(p)
    assert doc.metadata.get("truncated") is False
    # total_rows absent or equal to row_count.
    assert doc.metadata.get("total_rows", 10) == 10


def test_extract_respects_default_max_rows(tmp_path: Path):
    """Default ``max_rows`` is 200 — table just over → truncated."""
    p = tmp_path / "big.csv"
    rows = [(f"r{i}", i) for i in range(250)]
    _write_csv(p, rows)
    doc = CsvExtractor().extract(p)
    assert doc.metadata.get("truncated") is True
    assert doc.metadata.get("row_count") == 200


def test_lazy_import_does_not_load_pandas_on_module_import():
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.csv as m; "
        "assert 'pandas' not in sys.modules, sorted(sys.modules); "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_registry_wires_csv_extractor(tmp_path: Path):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    extractor = reg.get_for(p)
    assert isinstance(extractor, CsvExtractor)


def test_registry_wires_tsv_extractor(tmp_path: Path):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / "x.tsv"
    p.write_text("a\tb\n1\t2\n", encoding="utf-8")
    extractor = reg.get_for(p)
    assert isinstance(extractor, CsvExtractor)


def test_extract_metadata_format_tag_csv(tmp_path: Path):
    p = tmp_path / "data.csv"
    _write_csv(p, [("a", 1)])
    doc = CsvExtractor().extract(p)
    # No mandatory format key requested in the contract, but `labels`
    # must distinguish. Cross-check the labels-side assertion lives
    # elsewhere; here we just ensure the labels list is non-empty.
    assert doc.labels


def test_csv_max_rows_config_field_is_honoured_when_passed_via_extractor():
    """Sanity: ``ExtractionConfig.csv_max_rows`` is the canonical knob.

    The extractor's ``max_rows`` constructor argument is the runtime
    plumbing for that config value. Wave 2 will wire the two together
    via the source layer; this test just verifies the extractor reads
    the constructor argument.
    """
    ex = CsvExtractor(max_rows=42)
    assert ex.max_rows == 42
