"""Build the Phase M Wave 4 Zotero fixture sqlite from scratch.

Reference: https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access

Schema covered (only the tables ``corpus_forge.zotero.local.ZoteroLocalReader``
actually joins against):

- ``items``                — primary item row (typed by ``itemTypeID``).
- ``itemTypes``            — vocabulary of item types (``journalArticle``,
                              ``bookSection``, ``webpage``, ``attachment``, ...).
- ``itemData``             — link row: which fields a given item carries.
- ``itemDataValues``       — interned string values for ``itemData``.
- ``fields``               — vocabulary of field names (``title``, ``date``,
                              ``DOI``, ``abstractNote``, ...).
- ``creators``             — distinct (firstName, lastName) tuples.
- ``itemCreators``         — link row: which creators are attached to which
                              items in what order, and as which creatorType.
- ``creatorTypes``         — vocabulary of creator types (``author``,
                              ``editor``, ``translator``, ...).
- ``tags``                 — distinct tag names.
- ``itemTags``             — link rows (item, tag).
- ``collections``          — collections (folders), tree via ``parentCollectionID``.
- ``collectionItems``      — link rows (collection, item).
- ``itemAttachments``      — attachment metadata (``parentItemID``, ``path``,
                              ``contentType``, ``linkMode``).
- ``settings``             — key/value config; we set ``client.lastVersion``
                              and ``client.lastCompatibleVersion`` (matching
                              what real Zotero 7 writes on startup) so the
                              reader's schema-compatibility probe succeeds.

The fixture builds five items:

  1. journalArticle "Quantum Coherence in Photosynthesis"
     ├ author    "Alice Quanta"
     ├ author    "Bob Photon"
     ├ year      2024
     ├ DOI       "10.1234/qcp.2024.001"
     ├ abstract  "We demonstrate..."
     ├ tag       "quantum"
     ├ tag       "physics"
     ├ attachment KEY=ATTKEY01 (PDF, copy of digital-single-col.pdf)
     └ attachment KEY=ATTKEY02 (PDF, copy of scanned-paper.pdf)

  2. bookSection "Pancake Day Logistics"
     ├ author    "Charlie Editor"
     ├ year      2022
     ├ tag       "cooking"
     └ attachment KEY=ATTKEY03 (PDF, copy of digital-two-col-equations.pdf)

  3. webpage "An HTML Snapshot of a Blog Post"
     ├ year      2023
     └ attachment KEY=ATTKEY04 (HTML, content_type=text/html — filtered by default)

  4. journalArticle "Bare Abstract — No Attachments"
     ├ year      2021
     ├ abstract  "This item has no PDF, only an abstract..."
     └ (no attachments — should emit one abstract-only RawDocument)

  5. journalArticle "Sub-Collection Item" inside collection ``Research/Quantum``
     ├ year      2025
     └ attachment KEY=ATTKEY05 (no on-disk file — should be skipped by the source)

Run-once locally; the resulting ``zotero.sqlite`` plus the ``storage/`` tree
are committed.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "zotero.sqlite"
STORAGE_DIR = HERE / "storage"
REPO_PDF_DIR = HERE.parents[1] / "fixtures" / "multi_format_corpus" / "pdf"


def _schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # Vocabulary tables.
    cur.executescript(
        """
        CREATE TABLE itemTypes (
            itemTypeID  INTEGER PRIMARY KEY,
            typeName    TEXT NOT NULL UNIQUE,
            display     INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE fields (
            fieldID     INTEGER PRIMARY KEY,
            fieldName   TEXT NOT NULL UNIQUE,
            fieldFormatID INTEGER
        );
        CREATE TABLE creatorTypes (
            creatorTypeID INTEGER PRIMARY KEY,
            creatorType   TEXT NOT NULL UNIQUE
        );
        CREATE TABLE itemDataValues (
            valueID INTEGER PRIMARY KEY,
            value   TEXT NOT NULL
        );
        CREATE TABLE items (
            itemID    INTEGER PRIMARY KEY,
            itemTypeID INTEGER NOT NULL,
            dateAdded TEXT NOT NULL,
            dateModified TEXT NOT NULL,
            key       TEXT NOT NULL UNIQUE,
            libraryID INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE itemData (
            itemID    INTEGER NOT NULL,
            fieldID   INTEGER NOT NULL,
            valueID   INTEGER NOT NULL,
            PRIMARY KEY (itemID, fieldID)
        );
        CREATE TABLE creators (
            creatorID INTEGER PRIMARY KEY,
            firstName TEXT,
            lastName  TEXT,
            fieldMode INTEGER DEFAULT 0
        );
        CREATE TABLE itemCreators (
            itemID        INTEGER NOT NULL,
            creatorID     INTEGER NOT NULL,
            creatorTypeID INTEGER NOT NULL,
            orderIndex    INTEGER NOT NULL,
            PRIMARY KEY (itemID, creatorID, creatorTypeID)
        );
        CREATE TABLE tags (
            tagID INTEGER PRIMARY KEY,
            name  TEXT NOT NULL UNIQUE
        );
        CREATE TABLE itemTags (
            itemID INTEGER NOT NULL,
            tagID  INTEGER NOT NULL,
            type   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (itemID, tagID)
        );
        CREATE TABLE collections (
            collectionID INTEGER PRIMARY KEY,
            collectionName TEXT NOT NULL,
            parentCollectionID INTEGER,
            key TEXT NOT NULL UNIQUE,
            libraryID INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE collectionItems (
            collectionID INTEGER NOT NULL,
            itemID       INTEGER NOT NULL,
            orderIndex   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (collectionID, itemID)
        );
        CREATE TABLE itemAttachments (
            itemID         INTEGER PRIMARY KEY,
            parentItemID   INTEGER,
            linkMode       INTEGER NOT NULL DEFAULT 1,
            contentType    TEXT,
            charsetID      INTEGER,
            path           TEXT,
            syncState      INTEGER DEFAULT 0
        );
        CREATE TABLE settings (
            setting TEXT NOT NULL,
            key     TEXT NOT NULL,
            value   TEXT,
            PRIMARY KEY (setting, key)
        );
        """
    )
    conn.commit()


def _seed_vocab(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    cur = conn.cursor()

    item_types = [
        "journalArticle",
        "bookSection",
        "webpage",
        "attachment",
    ]
    type_ids: dict[str, int] = {}
    for i, name in enumerate(item_types, start=1):
        cur.execute("INSERT INTO itemTypes (itemTypeID, typeName) VALUES (?, ?)", (i, name))
        type_ids[name] = i

    fields = ["title", "date", "DOI", "abstractNote", "url"]
    field_ids: dict[str, int] = {}
    for i, name in enumerate(fields, start=1):
        cur.execute("INSERT INTO fields (fieldID, fieldName) VALUES (?, ?)", (i, name))
        field_ids[name] = i

    creator_types = ["author", "editor", "translator"]
    creator_type_ids: dict[str, int] = {}
    for i, name in enumerate(creator_types, start=1):
        cur.execute(
            "INSERT INTO creatorTypes (creatorTypeID, creatorType) VALUES (?, ?)",
            (i, name),
        )
        creator_type_ids[name] = i

    # Schema-version probe values — match what real Zotero 7 writes on
    # every startup. The schema check accepts any setting='client' row;
    # we insert both keys to mirror what a real install looks like and
    # so we're robust against future tightening that pins a specific key.
    cur.execute(
        "INSERT INTO settings (setting, key, value) VALUES ('client', 'lastVersion', '7.0.5')"
    )
    cur.execute(
        "INSERT INTO settings (setting, key, value) VALUES "
        "('client', 'lastCompatibleVersion', '7.0.5')"
    )
    conn.commit()

    return {
        "itemTypes": type_ids,
        "fields": field_ids,
        "creatorTypes": creator_type_ids,
    }


def _intern_value(conn: sqlite3.Connection, value: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT valueID FROM itemDataValues WHERE value = ?", (value,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO itemDataValues (value) VALUES (?)", (value,))
    return cur.lastrowid


def _put_field(conn: sqlite3.Connection, item_id: int, field_id: int, value: str) -> None:
    value_id = _intern_value(conn, value)
    conn.execute(
        "INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
        (item_id, field_id, value_id),
    )


def _ensure_creator(conn: sqlite3.Connection, first: str, last: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT creatorID FROM creators WHERE firstName = ? AND lastName = ?",
        (first, last),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO creators (firstName, lastName) VALUES (?, ?)",
        (first, last),
    )
    return cur.lastrowid


def _ensure_tag(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT tagID FROM tags WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    return cur.lastrowid


def _make_item(
    conn: sqlite3.Connection,
    *,
    item_type_id: int,
    key: str,
    date_modified: str = "2025-01-01 00:00:00",
) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO items (itemTypeID, dateAdded, dateModified, key) VALUES (?, ?, ?, ?)",
        (item_type_id, "2024-01-01 00:00:00", date_modified, key),
    )
    return cur.lastrowid


def _attach(
    conn: sqlite3.Connection,
    *,
    parent_item_id: int,
    key: str,
    content_type: str,
    relative_path: str,
) -> int:
    attach_item_id = _make_item(
        conn,
        item_type_id=4,  # 'attachment'
        key=key,
    )
    conn.execute(
        "INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path) "
        "VALUES (?, ?, 1, ?, ?)",
        (attach_item_id, parent_item_id, content_type, "storage:" + relative_path),
    )
    return attach_item_id


def _link_creator(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    creator_id: int,
    creator_type_id: int,
    order_index: int,
) -> None:
    conn.execute(
        "INSERT INTO itemCreators (itemID, creatorID, creatorTypeID, orderIndex) "
        "VALUES (?, ?, ?, ?)",
        (item_id, creator_id, creator_type_id, order_index),
    )


def _tag_item(conn: sqlite3.Connection, *, item_id: int, tag: str) -> None:
    tag_id = _ensure_tag(conn, tag)
    conn.execute(
        "INSERT INTO itemTags (itemID, tagID, type) VALUES (?, ?, 0)",
        (item_id, tag_id),
    )


def _add_collection(
    conn: sqlite3.Connection,
    *,
    name: str,
    key: str,
    parent_id: int | None = None,
) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO collections (collectionName, parentCollectionID, key) VALUES (?, ?, ?)",
        (name, parent_id, key),
    )
    return cur.lastrowid


def _put_in_collection(conn: sqlite3.Connection, *, collection_id: int, item_id: int) -> None:
    conn.execute(
        "INSERT INTO collectionItems (collectionID, itemID, orderIndex) VALUES (?, ?, 0)",
        (collection_id, item_id),
    )


def build(db_path: Path = DB_PATH, storage_dir: Path = STORAGE_DIR) -> None:
    """Build the fixture sqlite + storage tree from scratch."""
    if db_path.exists():
        db_path.unlink()
    if storage_dir.exists():
        shutil.rmtree(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        _schema(conn)
        vocab = _seed_vocab(conn)
        f = vocab["fields"]
        types = vocab["itemTypes"]
        creator_types = vocab["creatorTypes"]

        # ── Item 1: journalArticle with 2 PDFs ────────────────────────────
        i1 = _make_item(
            conn,
            item_type_id=types["journalArticle"],
            key="ITEMKEY01",
            date_modified="2025-01-15 12:00:00",
        )
        _put_field(conn, i1, f["title"], "Quantum Coherence in Photosynthesis")
        _put_field(conn, i1, f["date"], "2024-06-01")
        _put_field(conn, i1, f["DOI"], "10.1234/qcp.2024.001")
        _put_field(
            conn,
            i1,
            f["abstractNote"],
            "We demonstrate quantum coherence effects in chlorophyll antenna complexes.",
        )
        a1 = _ensure_creator(conn, "Alice", "Quanta")
        b1 = _ensure_creator(conn, "Bob", "Photon")
        author_t = creator_types["author"]
        _link_creator(conn, item_id=i1, creator_id=a1, creator_type_id=author_t, order_index=0)
        _link_creator(conn, item_id=i1, creator_id=b1, creator_type_id=author_t, order_index=1)
        _tag_item(conn, item_id=i1, tag="quantum")
        _tag_item(conn, item_id=i1, tag="physics")
        # Two PDF attachments.
        _attach(
            conn,
            parent_item_id=i1,
            key="ATTKEY01",
            content_type="application/pdf",
            relative_path="qcp-main.pdf",
        )
        _attach(
            conn,
            parent_item_id=i1,
            key="ATTKEY02",
            content_type="application/pdf",
            relative_path="qcp-supplement.pdf",
        )

        # ── Item 2: bookSection with 1 PDF ────────────────────────────────
        i2 = _make_item(
            conn,
            item_type_id=types["bookSection"],
            key="ITEMKEY02",
            date_modified="2025-02-01 09:00:00",
        )
        _put_field(conn, i2, f["title"], "Pancake Day Logistics")
        _put_field(conn, i2, f["date"], "2022")
        c2 = _ensure_creator(conn, "Charlie", "Editor")
        _link_creator(
            conn,
            item_id=i2,
            creator_id=c2,
            creator_type_id=creator_types["author"],
            order_index=0,
        )
        _tag_item(conn, item_id=i2, tag="cooking")
        _attach(
            conn,
            parent_item_id=i2,
            key="ATTKEY03",
            content_type="application/pdf",
            relative_path="pancake.pdf",
        )

        # ── Item 3: webpage with HTML snapshot (filtered) ─────────────────
        i3 = _make_item(
            conn,
            item_type_id=types["webpage"],
            key="ITEMKEY03",
            date_modified="2025-03-01 09:00:00",
        )
        _put_field(conn, i3, f["title"], "An HTML Snapshot of a Blog Post")
        _put_field(conn, i3, f["date"], "2023")
        _put_field(conn, i3, f["url"], "https://example.com/post")
        _attach(
            conn,
            parent_item_id=i3,
            key="ATTKEY04",
            content_type="text/html",
            relative_path="snapshot.html",
        )

        # ── Item 4: no attachments + abstract ─────────────────────────────
        i4 = _make_item(
            conn,
            item_type_id=types["journalArticle"],
            key="ITEMKEY04",
            date_modified="2025-04-01 09:00:00",
        )
        _put_field(conn, i4, f["title"], "Bare Abstract — No Attachments")
        _put_field(conn, i4, f["date"], "2021")
        _put_field(
            conn,
            i4,
            f["abstractNote"],
            "This item has no PDF, only an abstract describing why the result matters.",
        )

        # ── Item 5: sub-collection ────────────────────────────────────────
        i5 = _make_item(
            conn,
            item_type_id=types["journalArticle"],
            key="ITEMKEY05",
            date_modified="2025-05-01 09:00:00",
        )
        _put_field(conn, i5, f["title"], "Sub-Collection Item")
        _put_field(conn, i5, f["date"], "2025")
        # PDF reference whose on-disk file we DO NOT create — the source
        # should drop it silently (path absent).
        _attach(
            conn,
            parent_item_id=i5,
            key="ATTKEY05",
            content_type="application/pdf",
            relative_path="missing.pdf",
        )

        # Collections: Research/Quantum/, plus a top-level "Excluded" for tests.
        research_id = _add_collection(conn, name="Research", key="COLL_RES")
        quantum_id = _add_collection(
            conn,
            name="Quantum",
            key="COLL_QNT",
            parent_id=research_id,
        )
        excluded_id = _add_collection(conn, name="Excluded", key="COLL_EXC")
        _put_in_collection(conn, collection_id=quantum_id, item_id=i5)
        # Also place item 2 into Excluded so exclude-filter tests have a target.
        _put_in_collection(conn, collection_id=excluded_id, item_id=i2)

        conn.commit()
    finally:
        conn.close()

    # ── Lay down the storage tree (copy the existing PDFs) ────────────────
    src_pdfs = {
        "ATTKEY01/qcp-main.pdf": REPO_PDF_DIR / "digital-single-col.pdf",
        "ATTKEY02/qcp-supplement.pdf": REPO_PDF_DIR / "scanned-paper.pdf",
        "ATTKEY03/pancake.pdf": REPO_PDF_DIR / "digital-two-col-equations.pdf",
    }
    for rel, src in src_pdfs.items():
        dest = storage_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            raise FileNotFoundError(f"Source PDF missing: {src}")
        shutil.copyfile(src, dest)

    # ATTKEY04 (HTML) — write a stub the source filters out by MIME.
    (storage_dir / "ATTKEY04").mkdir(parents=True, exist_ok=True)
    (storage_dir / "ATTKEY04" / "snapshot.html").write_text(
        "<html><body>Snapshot</body></html>", encoding="utf-8"
    )
    # ATTKEY05 — DELIBERATELY MISSING on-disk file.


def smoke() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM items")
        n_items = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM itemAttachments")
        n_att = cur.fetchone()[0]
        cur.execute("SELECT value FROM settings WHERE setting='client' AND key='lastVersion'")
        client = cur.fetchone()
        cur.execute(
            "SELECT i.key, idv.value FROM items i "
            "JOIN itemData id ON id.itemID = i.itemID "
            "JOIN fields fd ON fd.fieldID = id.fieldID "
            "JOIN itemDataValues idv ON idv.valueID = id.valueID "
            "WHERE fd.fieldName='title' ORDER BY i.key"
        )
        titles = cur.fetchall()
        print(f"items={n_items} attachments={n_att} lastVersion={client}")
        print("titles:")
        for k, t in titles:
            print(f"  {k} -> {t}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    build()
    smoke()
    print(f"OK — wrote {DB_PATH}")
