"""Phase M Wave 4 — ``ZoteroLocalReader`` against the committed fixture.

Fixture: ``tests/fixtures/zotero/zotero.sqlite`` (5 items, 5 attachments —
3 PDFs, 1 HTML snapshot, 1 PDF with a deliberately-missing on-disk file).
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from corpus_forge.zotero import ZoteroAttachment, ZoteroItem, ZoteroLocalReader
from corpus_forge.zotero.local import ZoteroSchemaUnsupported

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "zotero"
FIXTURE_DB = FIXTURE_DIR / "zotero.sqlite"


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    """Copy the committed fixture into a tmp path so tests can mutate."""
    target = tmp_path / "zotero"
    shutil.copytree(FIXTURE_DIR, target)
    return target


def _reader(fixture_dir: Path) -> ZoteroLocalReader:
    return ZoteroLocalReader(fixture_dir / "zotero.sqlite", library_id="local")


class TestIterItems:
    def test_yields_five_items(self, fixture_dir: Path) -> None:
        items = list(_reader(fixture_dir).iter_items())
        # Five parent items; attachment rows are NOT items here.
        assert len(items) == 5
        keys = {it.item_key for it in items}
        assert keys == {
            "ITEMKEY01",
            "ITEMKEY02",
            "ITEMKEY03",
            "ITEMKEY04",
            "ITEMKEY05",
        }

    def test_authors_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        item1 = items["ITEMKEY01"]
        assert item1.authors == ["Alice Quanta", "Bob Photon"]

    def test_year_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        # ITEMKEY01 has date '2024-06-01' — year should parse to 2024.
        assert items["ITEMKEY01"].year == 2024
        assert items["ITEMKEY02"].year == 2022

    def test_doi_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        assert items["ITEMKEY01"].doi == "10.1234/qcp.2024.001"
        assert items["ITEMKEY02"].doi is None

    def test_abstract_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        assert "quantum coherence" in items["ITEMKEY01"].abstract.lower()
        assert items["ITEMKEY04"].abstract.startswith("This item has no PDF")

    def test_tags_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        assert set(items["ITEMKEY01"].tags) == {"quantum", "physics"}
        assert set(items["ITEMKEY02"].tags) == {"cooking"}

    def test_collection_path_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        # ITEMKEY05 lives inside Research/Quantum.
        assert items["ITEMKEY05"].collection_path == "Research/Quantum"

    def test_item_type_lifted(self, fixture_dir: Path) -> None:
        items = {it.item_key: it for it in _reader(fixture_dir).iter_items()}
        assert items["ITEMKEY01"].item_type == "journalArticle"
        assert items["ITEMKEY02"].item_type == "bookSection"
        assert items["ITEMKEY03"].item_type == "webpage"

    def test_library_id_propagated(self, fixture_dir: Path) -> None:
        items = list(_reader(fixture_dir).iter_items())
        assert {it.library_id for it in items} == {"local"}


class TestIterAttachments:
    def test_yields_attachments_filtered_by_default_mime(self, fixture_dir: Path) -> None:
        # Default include_attachments = ["application/pdf"] should filter out
        # the HTML snapshot — 4 PDFs (one of which has no on-disk file) plus
        # the HTML, so default filter yields 4.
        atts = list(_reader(fixture_dir).iter_attachments())
        att_keys = {a.attachment_key for a in atts}
        assert att_keys == {"ATTKEY01", "ATTKEY02", "ATTKEY03", "ATTKEY05"}

    def test_html_included_when_mime_explicit(self, fixture_dir: Path) -> None:
        atts = list(
            _reader(fixture_dir).iter_attachments(
                include_attachments=["application/pdf", "text/html"],
            )
        )
        att_keys = {a.attachment_key for a in atts}
        assert "ATTKEY04" in att_keys

    def test_on_disk_path_resolution(self, fixture_dir: Path) -> None:
        atts = {a.attachment_key: a for a in _reader(fixture_dir).iter_attachments()}
        storage = fixture_dir / "storage"
        assert atts["ATTKEY01"].on_disk_path == storage / "ATTKEY01" / "qcp-main.pdf"
        assert atts["ATTKEY03"].on_disk_path == storage / "ATTKEY03" / "pancake.pdf"

    def test_parent_item_metadata_set(self, fixture_dir: Path) -> None:
        atts = {a.attachment_key: a for a in _reader(fixture_dir).iter_attachments()}
        att1 = atts["ATTKEY01"]
        assert att1.item_key == "ITEMKEY01"
        assert att1.parent_item_metadata.title == "Quantum Coherence in Photosynthesis"
        assert att1.parent_item_metadata.year == 2024

    def test_mime_propagated(self, fixture_dir: Path) -> None:
        atts = list(
            _reader(fixture_dir).iter_attachments(
                include_attachments=["application/pdf", "text/html"],
            )
        )
        by_key = {a.attachment_key: a for a in atts}
        assert by_key["ATTKEY01"].mime == "application/pdf"
        assert by_key["ATTKEY04"].mime == "text/html"

    def test_exclude_collections(self, fixture_dir: Path) -> None:
        # ITEMKEY02 lives in collection "Excluded"; excluding it should
        # drop ATTKEY03 from the iterator.
        atts = list(_reader(fixture_dir).iter_attachments(exclude_collections=["Excluded"]))
        att_keys = {a.attachment_key for a in atts}
        assert "ATTKEY03" not in att_keys
        # Item 1 lives outside any collection → kept.
        assert "ATTKEY01" in att_keys

    def test_library_id_propagated(self, fixture_dir: Path) -> None:
        atts = list(_reader(fixture_dir).iter_attachments())
        assert {a.library_id for a in atts} == {"local"}


class TestReadOnlyOpen:
    def test_open_with_sibling_wal_succeeds(self, fixture_dir: Path) -> None:
        # Touch a sibling .wal file — read-only `immutable=1` open should still
        # succeed (this is the exact production scenario when Zotero is running).
        wal = fixture_dir / "zotero.sqlite-wal"
        wal.write_bytes(b"")
        items = list(_reader(fixture_dir).iter_items())
        assert items, "iter_items should produce rows even with a sibling .wal"


class TestSchemaProbe:
    def test_missing_lastclient_raises(self, tmp_path: Path) -> None:
        # Build a sqlite that LOOKS like Zotero (has settings table) but is
        # missing the `lastclient` row. Reader should refuse with the
        # dedicated exception class.
        db_path = tmp_path / "fake.sqlite"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE settings (setting TEXT, key TEXT, value TEXT, "
                "PRIMARY KEY (setting, key))"
            )
            conn.commit()
        finally:
            conn.close()
        reader = ZoteroLocalReader(db_path, library_id="local")
        with pytest.raises(ZoteroSchemaUnsupported):
            list(reader.iter_items())


class TestDataclasses:
    def test_zotero_item_frozen(self) -> None:
        item = ZoteroItem(
            item_key="K",
            item_type="journalArticle",
            title="T",
            authors=[],
            year=None,
            doi=None,
            abstract="",
            tags=[],
            collection_path="",
            date_modified="2025-01-01T00:00:00",
            library_id="local",
        )
        with pytest.raises((AttributeError, TypeError, Exception)):
            item.title = "X"  # type: ignore[misc]

    def test_zotero_attachment_carries_parent_metadata(self) -> None:
        item = ZoteroItem(
            item_key="K1",
            item_type="journalArticle",
            title="T",
            authors=[],
            year=2024,
            doi=None,
            abstract="",
            tags=[],
            collection_path="",
            date_modified="2025-01-01T00:00:00",
            library_id="local",
        )
        att = ZoteroAttachment(
            attachment_key="A1",
            item_key="K1",
            parent_item_metadata=item,
            on_disk_path=Path("/tmp/x.pdf"),
            mime="application/pdf",
            library_id="local",
        )
        assert att.parent_item_metadata is item
