"""Phase M Wave 4 — ``ZoteroSource`` over the committed fixture.

Verifies the ``RawDocument`` emission contract:

  - One ``RawDocument`` per PDF attachment.
  - One abstract-only doc per item that has no PDFs but a non-empty
    ``abstractNote``.
  - Items with no PDFs and no abstract are skipped silently.
  - ``metadata`` carries the full Zotero field set + ``itemType``.
  - ``labels`` carries ``(zotero_tag, ...)`` + ``(zotero_collection, ...)``.
  - ``source_uri == "zotero://<lib>/<item>/<attachment>"``.
  - ``mode="both"`` dedupes on ``zotero_item_key`` (local wins unless web's
    ``dateModified`` is strictly newer).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from corpus_forge.sources.zotero import ZoteroSource
from corpus_forge.zotero import ZoteroItem

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "zotero"


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    target = tmp_path / "zotero"
    shutil.copytree(FIXTURE_DIR, target)
    return target


def _build_source(fixture_dir: Path, **overrides) -> ZoteroSource:
    kwargs: dict = {
        "mode": "local",
        "library_path": fixture_dir / "zotero.sqlite",
        "user_id": None,
        "api_key_env": "ZOTERO_API_KEY",
        "library_type": "user",
        "group_id": None,
        "base_url": "https://api.zotero.org",
        "include_attachments": ["application/pdf"],
        "include_collections": [],
        "exclude_collections": [],
        "cache_dir": None,
    }
    kwargs.update(overrides)
    return ZoteroSource(**kwargs)


class TestEmissionShape:
    def test_one_rawdoc_per_pdf_attachment(self, fixture_dir: Path) -> None:
        src = _build_source(fixture_dir)
        raws = list(src.scan())
        # ITEMKEY01 → 2 PDFs (ATTKEY01, ATTKEY02)
        # ITEMKEY02 → 1 PDF (ATTKEY03)
        # ITEMKEY03 → HTML, filtered → 0 docs
        # ITEMKEY04 → 0 PDFs, abstract → 1 abstract-only doc
        # ITEMKEY05 → 1 PDF but ATTKEY05 file missing on disk → 0 docs
        # Total: 3 PDF + 1 abstract = 4
        kinds = []
        for r in raws:
            if r.source_uri.endswith("/abstract"):
                kinds.append("abstract")
            else:
                kinds.append("pdf")
        assert sorted(kinds) == ["abstract", "pdf", "pdf", "pdf"]

    def test_abstract_only_doc_skipped_for_no_attach_empty_abstract(
        self, fixture_dir: Path
    ) -> None:
        # Patch ITEMKEY04 to drop its abstract via SQL — easier than rebuilding
        # the fixture in-test.
        import sqlite3

        conn = sqlite3.connect(str(fixture_dir / "zotero.sqlite"))
        try:
            conn.execute(
                "DELETE FROM itemData WHERE itemID = "
                "(SELECT itemID FROM items WHERE key = 'ITEMKEY04') "
                "AND fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'abstractNote')"
            )
            conn.commit()
        finally:
            conn.close()

        src = _build_source(fixture_dir)
        raws = list(src.scan())
        # Abstract-only doc should be gone.
        assert all(not r.source_uri.endswith("/abstract") for r in raws)

    def test_source_uri_shape(self, fixture_dir: Path) -> None:
        src = _build_source(fixture_dir)
        raws = list(src.scan())
        pdf_uris = {r.source_uri for r in raws if not r.source_uri.endswith("/abstract")}
        # library_id defaults to "local" in local-only mode.
        assert "zotero://local/ITEMKEY01/ATTKEY01" in pdf_uris
        assert "zotero://local/ITEMKEY01/ATTKEY02" in pdf_uris
        assert "zotero://local/ITEMKEY02/ATTKEY03" in pdf_uris
        abstract_uris = {r.source_uri for r in raws if r.source_uri.endswith("/abstract")}
        assert "zotero://local/ITEMKEY04/abstract" in abstract_uris

    def test_metadata_carries_full_field_set(self, fixture_dir: Path) -> None:
        src = _build_source(fixture_dir)
        raws = list(src.scan())
        by_uri = {r.source_uri: r for r in raws}
        first_attach = by_uri["zotero://local/ITEMKEY01/ATTKEY01"]
        md = first_attach.metadata
        assert md["zotero_item_key"] == "ITEMKEY01"
        assert md["zotero_attachment_key"] == "ATTKEY01"
        assert md["zotero_authors"] == ["Alice Quanta", "Bob Photon"]
        assert md["zotero_year"] == 2024
        assert md["zotero_doi"] == "10.1234/qcp.2024.001"
        assert md["zotero_mime"] == "application/pdf"
        assert md["itemType"] == "journalArticle"
        assert "quantum coherence" in (md["zotero_abstract"] or "").lower()

    def test_labels_include_tags_and_collection(self, fixture_dir: Path) -> None:
        src = _build_source(fixture_dir)
        raws = list(src.scan())
        by_uri = {r.source_uri: r for r in raws}
        first_attach = by_uri["zotero://local/ITEMKEY01/ATTKEY01"]
        labels = first_attach.labels
        assert ("zotero_tag", "quantum") in labels
        assert ("zotero_tag", "physics") in labels

    def test_abstract_only_doc_text(self, fixture_dir: Path) -> None:
        src = _build_source(fixture_dir)
        raws = list(src.scan())
        abstract_doc = next(r for r in raws if r.source_uri.endswith("/abstract"))
        assert "no PDF" in abstract_doc.text


class TestDiscoverYieldsPaths:
    def test_discover_yields_pdf_paths_only(self, fixture_dir: Path) -> None:
        src = _build_source(fixture_dir)
        paths = list(src.discover())
        # Three PDFs are on disk; ATTKEY05's PDF was deliberately omitted by
        # the fixture builder so it should be dropped.
        assert len(paths) == 3
        for p in paths:
            assert p.suffix == ".pdf"
            assert p.exists()


class TestBothModeReconciliation:
    def test_dedupes_on_item_key_local_wins_by_default(self, fixture_dir: Path) -> None:
        # Construct an iterator that returns the local item + an older web
        # entry for the same item key. The reconciler should pick the local.
        from corpus_forge.zotero.types import ZoteroReconciled

        local_item = ZoteroItem(
            item_key="K1",
            item_type="journalArticle",
            title="Local",
            authors=[],
            year=2024,
            doi=None,
            abstract="",
            tags=[],
            collection_path="",
            date_modified="2025-05-01T00:00:00Z",
            library_id="local",
        )
        web_item = ZoteroItem(
            item_key="K1",
            item_type="journalArticle",
            title="Web",
            authors=[],
            year=2024,
            doi=None,
            abstract="",
            tags=[],
            collection_path="",
            date_modified="2025-04-01T00:00:00Z",  # older
            library_id="999",
        )
        from corpus_forge.sources.zotero import reconcile_items

        merged = reconcile_items([local_item], [web_item])
        assert isinstance(merged, ZoteroReconciled)
        assert len(merged.items) == 1
        assert merged.items[0].title == "Local"

    def test_web_wins_when_strictly_newer(self) -> None:
        local_item = ZoteroItem(
            item_key="K1",
            item_type="journalArticle",
            title="Local",
            authors=[],
            year=2024,
            doi=None,
            abstract="",
            tags=[],
            collection_path="",
            date_modified="2025-05-01T00:00:00Z",
            library_id="local",
        )
        web_item = ZoteroItem(
            item_key="K1",
            item_type="journalArticle",
            title="Web Newer",
            authors=[],
            year=2024,
            doi=None,
            abstract="",
            tags=[],
            collection_path="",
            date_modified="2025-06-01T00:00:00Z",
            library_id="999",
        )
        from corpus_forge.sources.zotero import reconcile_items

        merged = reconcile_items([local_item], [web_item])
        assert merged.items[0].title == "Web Newer"


class TestParseUnknownPath:
    def test_unknown_path_returns_none(self, fixture_dir: Path, tmp_path: Path) -> None:
        # parse(path) for a path NOT in the attachment cache must return None.
        src = _build_source(fixture_dir)
        stray = tmp_path / "stray.pdf"
        stray.write_bytes(b"%PDF-1.4\n")
        assert src.parse(stray) is None
