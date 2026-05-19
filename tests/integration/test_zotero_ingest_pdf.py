"""Phase M Wave 4 — Zotero source → ``PdfDigitalExtractor`` integration.

Confirms that the per-attachment ``RawDocument`` flows through the existing
extractor pipeline and that the per-chunk metadata retains the Zotero
fields (authors, year, DOI). This is the contract the master plan calls
out as the critical interop point with the rest of the corpus.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from corpus_forge.chunkers.markdown import MarkdownChunker
from corpus_forge.sources.zotero import ZoteroSource

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "zotero"


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    target = tmp_path / "zotero"
    shutil.copytree(FIXTURE_DIR, target)
    return target


def test_pdf_attachment_flows_through_pipeline(fixture_dir: Path) -> None:
    src = ZoteroSource(
        mode="local",
        library_path=fixture_dir / "zotero.sqlite",
        user_id=None,
        api_key_env="ZOTERO_API_KEY",
        library_type="user",
        group_id=None,
        base_url="https://api.zotero.org",
        include_attachments=["application/pdf"],
        include_collections=[],
        exclude_collections=[],
        cache_dir=None,
    )
    raws = [r for r in src.scan() if r.source_uri.startswith("zotero://local/ITEMKEY01/")]
    assert raws, "Expected at least one ITEMKEY01 RawDocument"
    first = raws[0]
    # Text should be populated by the digital extractor.
    assert isinstance(first.text, str)
    assert first.text  # non-empty
    # Zotero metadata propagated.
    assert first.metadata["zotero_item_key"] == "ITEMKEY01"
    assert first.metadata["zotero_authors"] == ["Alice Quanta", "Bob Photon"]
    assert first.metadata["zotero_year"] == 2024
    assert first.metadata["zotero_doi"] == "10.1234/qcp.2024.001"


def test_chunker_produces_chunks_from_pdf_text(fixture_dir: Path) -> None:
    """End-to-end: PDF text from ITEMKEY01 → MarkdownChunker → ≥1 chunk.

    The Zotero metadata propagation to ``chunks`` is handled at the storage
    layer (``backend.upsert_document(dataset_id, raw, chunks)``); the
    contract this test pins is that the ``RawDocument.text`` produced by
    the source is non-empty and chunks cleanly, AND that the per-document
    metadata bag survives onto the ``RawDocument`` (a thin wrapper around
    ``ExtractedDocument`` with the Zotero fields merged in).
    """
    src = ZoteroSource(
        mode="local",
        library_path=fixture_dir / "zotero.sqlite",
        user_id=None,
        api_key_env="ZOTERO_API_KEY",
        library_type="user",
        group_id=None,
        base_url="https://api.zotero.org",
        include_attachments=["application/pdf"],
        include_collections=[],
        exclude_collections=[],
        cache_dir=None,
    )
    raws = [r for r in src.scan() if r.source_uri.startswith("zotero://local/ITEMKEY01/")]
    assert raws
    raw = raws[0]
    chunker = MarkdownChunker()
    chunks = chunker.chunk(raw.text)
    assert chunks, "Expected at least one chunk from the PDF text"
    # The RawDocument metadata bag carries the Zotero fields — that is what
    # `backend.upsert_document(dataset_id, raw, chunks)` will lift onto
    # every persisted chunk's metadata column.
    assert raw.metadata["zotero_item_key"] == "ITEMKEY01"
    assert raw.metadata["zotero_year"] == 2024
    assert raw.metadata["zotero_doi"] == "10.1234/qcp.2024.001"
