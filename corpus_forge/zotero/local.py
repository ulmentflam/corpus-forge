"""Phase M Wave 4 — local Zotero SQLite reader.

Reads a Zotero library's ``zotero.sqlite`` in **read-only** mode using
``sqlite3.connect("file:...?mode=ro&immutable=1", uri=True)`` — required
when Zotero is running because Zotero owns the WAL. ``immutable=1`` tells
SQLite "no other process is writing to this DB right now", which lets us
skip the lock-checking dance and ignore the sibling ``.wal`` / ``.shm``
files entirely. Trade-off: the snapshot we read is whatever was last
checkpointed, so any edits Zotero made since its last checkpoint won't
be visible. This is documented in ``docs/sources/zotero.md`` as the
"ingest-lag tradeoff".

Reference: https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

from corpus_forge.zotero.types import ZoteroAttachment, ZoteroItem

logger = logging.getLogger(__name__)


class ZoteroSchemaUnsupported(Exception):
    """Raised when the SQLite at ``library_path`` doesn't look like Zotero.

    Surfaces as a clear error message rather than producing wrong joins
    against an unknown schema. The reader probes the ``settings`` table
    for any row with ``setting = 'client'`` — Zotero writes
    ``client.lastVersion`` / ``client.lastCompatibleVersion`` on every
    startup (and historically wrote ``client.lastclient`` on older
    versions); when **no** ``client`` row is present we refuse rather
    than risk emitting garbage.
    """


# Author-equivalent creator types. The fixture / schema reference list
# 1=author, 2=editor, 3=translator. We treat author and editor as
# author-equivalent for the purposes of ``authors=[...]`` propagation.
# Translator is intentionally excluded — it almost always carries a
# different name than the original author.
_AUTHOR_CREATOR_TYPES = ("author", "editor")


def default_library_path() -> Path | None:
    """Best-effort resolution of the default Zotero library location.

    Returns ``None`` when no default exists on the current platform (or
    when the directory isn't there). Order:

    - macOS:  ``~/Zotero/zotero.sqlite``
    - Linux:  ``~/Zotero/zotero.sqlite`` then ``~/.zotero/zotero.sqlite``
    - Windows: ``%USERPROFILE%\\Zotero\\zotero.sqlite``
    """
    home = Path.home()
    candidates: list[Path] = []
    if sys.platform == "darwin" or sys.platform == "win32":
        candidates.append(home / "Zotero" / "zotero.sqlite")
    else:
        candidates.append(home / "Zotero" / "zotero.sqlite")
        candidates.append(home / ".zotero" / "zotero.sqlite")
    for p in candidates:
        if p.exists():
            return p
    return None


def _open_readonly(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only via the URI form so Zotero's lock is bypassed."""
    # ``immutable=1`` is the magic: SQLite treats the file as a frozen
    # snapshot and ignores WAL / SHM siblings. ``mode=ro`` is belt-and-
    # braces; ``immutable=1`` already implies it but we set both so the
    # intent is obvious.
    uri = f"file:{os.fspath(path)}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


class ZoteroLocalReader:
    """Read-only iterator over a local ``zotero.sqlite``.

    Args:
        library_path: Path to ``zotero.sqlite`` (NOT the parent
            directory).
        library_id: Tag used in :attr:`ZoteroItem.library_id`. Defaults
            to ``"local"``; callers wiring up ``mode = "both"`` use the
            numeric user / group id from the web client so the
            reconciler can tell rows apart.

    The reader does NOT open the DB at construction — callers can build
    a reader, decide they don't want the data, and never pay the open
    cost. Each iter method opens, reads, closes.
    """

    def __init__(self, library_path: Path | str, library_id: str = "local") -> None:
        self.library_path = Path(library_path)
        self.library_id = library_id

    # ── schema probe ──────────────────────────────────────────────────

    def _validate_schema_compatibility(self, conn: sqlite3.Connection) -> None:
        """Refuse to proceed against an alien schema.

        Probes for the ``settings`` row Zotero writes on startup. The
        specific ``key`` Zotero writes has changed across versions:

        - Modern Zotero (5.x / 6.x / 7.x): ``setting='client'`` rows
          with ``key='lastVersion'`` and ``key='lastCompatibleVersion'``.
        - Older / synthetic fixtures: ``setting='client'`` with
          ``key='lastclient'``.

        We accept **any** ``setting='client'`` row as a positive
        identification rather than pinning a specific ``key`` name —
        the presence of *any* ``client`` row in ``settings`` is what
        actually distinguishes a real Zotero DB from an unrelated
        SQLite file. When no ``client`` row is present we raise
        :class:`ZoteroSchemaUnsupported`. This was the cause of a
        2026-05-22 false-negative against a real Zotero 7 library
        whose settings table had ``lastVersion`` / ``lastCompatibleVersion``
        but no ``lastclient`` row.
        """
        try:
            cur = conn.execute(
                "SELECT 1 FROM settings WHERE setting = 'client' LIMIT 1"
            )
            row = cur.fetchone()
        except sqlite3.OperationalError as exc:
            raise ZoteroSchemaUnsupported(
                f"Cannot read settings table from {self.library_path}: {exc}. "
                "This does not look like a Zotero library — refusing to proceed."
            ) from exc
        if row is None:
            raise ZoteroSchemaUnsupported(
                f"No settings row with setting='client' in {self.library_path}; "
                "this does not look like a Zotero library — refusing to proceed."
            )

    # ── public iterators ──────────────────────────────────────────────

    def iter_items(
        self,
        *,
        include_collections: list[str] | None = None,
        exclude_collections: list[str] | None = None,
    ) -> Iterator[ZoteroItem]:
        """Yield every parent (non-attachment) item.

        Args:
            include_collections: When non-empty, only emit items whose
                collection path starts with one of these prefixes (the
                ``"Research/Quantum"`` style strings).
            exclude_collections: Drop items whose collection path matches
                any of these prefixes. Applied AFTER include.
        """
        conn = _open_readonly(self.library_path)
        try:
            self._validate_schema_compatibility(conn)
            yield from self._iter_items(
                conn,
                include_collections=include_collections or [],
                exclude_collections=exclude_collections or [],
            )
        finally:
            conn.close()

    def iter_attachments(
        self,
        *,
        include_attachments: list[str] | None = None,
        include_collections: list[str] | None = None,
        exclude_collections: list[str] | None = None,
    ) -> Iterator[ZoteroAttachment]:
        """Yield every attachment whose MIME passes ``include_attachments``.

        Args:
            include_attachments: MIME allowlist. Default
                ``["application/pdf"]``.
            include_collections / exclude_collections: same as
                :meth:`iter_items`, applied to the parent item's collection.
        """
        mime_allow = include_attachments or ["application/pdf"]
        conn = _open_readonly(self.library_path)
        try:
            self._validate_schema_compatibility(conn)
            # Lift items first so we can attach parent metadata to each
            # attachment without re-querying. The item-id keyed dict is
            # small even for huge libraries.
            items_by_id = {
                row[0]: item
                for row, item in self._iter_items_with_id(
                    conn,
                    include_collections=include_collections or [],
                    exclude_collections=exclude_collections or [],
                )
            }
            yield from self._iter_attachments(
                conn,
                items_by_id=items_by_id,
                mime_allow=mime_allow,
            )
        finally:
            conn.close()

    # ── private helpers ───────────────────────────────────────────────

    def _collection_paths(self, conn: sqlite3.Connection) -> dict[int, str]:
        """Return ``{collection_id: "Parent/Child"}``.

        Built by joining ``collections`` against itself on
        ``parentCollectionID`` once; deep trees fall through the
        cache via iterative ancestor walking.
        """
        cur = conn.execute(
            "SELECT collectionID, collectionName, parentCollectionID FROM collections"
        )
        rows = cur.fetchall()
        names = {cid: name for cid, name, _parent in rows}
        parents = {cid: parent for cid, _name, parent in rows}
        paths: dict[int, str] = {}
        for cid in names:
            parts: list[str] = []
            cursor: int | None = cid
            seen: set[int] = set()
            while cursor is not None and cursor not in seen:
                seen.add(cursor)
                parts.append(names[cursor])
                cursor = parents.get(cursor)
            paths[cid] = "/".join(reversed(parts))
        return paths

    def _item_collections(self, conn: sqlite3.Connection) -> dict[int, list[str]]:
        """Return ``{item_id: ["Parent/Child", ...]}``."""
        paths = self._collection_paths(conn)
        cur = conn.execute("SELECT collectionID, itemID FROM collectionItems")
        out: dict[int, list[str]] = {}
        for cid, iid in cur.fetchall():
            out.setdefault(iid, []).append(paths.get(cid, ""))
        return out

    def _item_field(self, conn: sqlite3.Connection) -> dict[int, dict[str, str]]:
        """Return ``{item_id: {field_name: value}}`` for every item.

        Single query joining ``itemData`` + ``fields`` + ``itemDataValues``.
        """
        cur = conn.execute(
            """
            SELECT id.itemID, fd.fieldName, idv.value
            FROM itemData id
            JOIN fields fd ON fd.fieldID = id.fieldID
            JOIN itemDataValues idv ON idv.valueID = id.valueID
            """
        )
        out: dict[int, dict[str, str]] = {}
        for iid, fname, val in cur.fetchall():
            out.setdefault(iid, {})[fname] = val
        return out

    def _item_authors(self, conn: sqlite3.Connection) -> dict[int, list[str]]:
        """Return ``{item_id: ["Alice Quanta", ...]}`` ordered by orderIndex."""
        cur = conn.execute(
            """
            SELECT ic.itemID, ic.orderIndex, c.firstName, c.lastName, ct.creatorType
            FROM itemCreators ic
            JOIN creators c ON c.creatorID = ic.creatorID
            JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
            ORDER BY ic.itemID, ic.orderIndex
            """
        )
        out: dict[int, list[tuple[int, str, str]]] = {}
        for iid, order, first, last, ctype in cur.fetchall():
            if ctype not in _AUTHOR_CREATOR_TYPES:
                continue
            name = " ".join(p for p in (first or "", last or "") if p).strip()
            if name:
                out.setdefault(iid, []).append((order, ctype, name))
        return {
            iid: [name for _o, _c, name in sorted(entries, key=lambda t: t[0])]
            for iid, entries in out.items()
        }

    def _item_tags(self, conn: sqlite3.Connection) -> dict[int, list[str]]:
        cur = conn.execute(
            """
            SELECT it.itemID, t.name
            FROM itemTags it
            JOIN tags t ON t.tagID = it.tagID
            ORDER BY it.itemID, t.name
            """
        )
        out: dict[int, list[str]] = {}
        for iid, name in cur.fetchall():
            out.setdefault(iid, []).append(name)
        return out

    def _iter_items_with_id(
        self,
        conn: sqlite3.Connection,
        *,
        include_collections: list[str],
        exclude_collections: list[str],
    ) -> Iterator[tuple[tuple[int], ZoteroItem]]:
        """Yield ``((item_id,), ZoteroItem)`` so callers can keep a row -> item map."""
        item_collections = self._item_collections(conn)
        fields = self._item_field(conn)
        authors = self._item_authors(conn)
        tags = self._item_tags(conn)

        cur = conn.execute(
            """
            SELECT i.itemID, it.typeName, i.key, i.dateModified
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            WHERE it.typeName != 'attachment'
            ORDER BY i.itemID
            """
        )
        for iid, type_name, item_key, modified in cur.fetchall():
            collections = item_collections.get(iid, [])
            collection_path = collections[0] if collections else ""

            if include_collections and not any(
                any(c.startswith(prefix) for c in collections) for prefix in include_collections
            ):
                continue
            if exclude_collections and any(
                any(c.startswith(prefix) for c in collections) for prefix in exclude_collections
            ):
                continue

            f = fields.get(iid, {})
            year = _parse_year(f.get("date", ""))
            item = ZoteroItem(
                item_key=item_key,
                item_type=type_name,
                title=f.get("title", ""),
                authors=list(authors.get(iid, [])),
                year=year,
                doi=f.get("DOI") or None,
                abstract=f.get("abstractNote", "") or "",
                tags=list(tags.get(iid, [])),
                collection_path=collection_path,
                date_modified=str(modified),
                library_id=self.library_id,
            )
            yield (iid,), item

    def _iter_items(
        self,
        conn: sqlite3.Connection,
        *,
        include_collections: list[str],
        exclude_collections: list[str],
    ) -> Iterator[ZoteroItem]:
        for _row, item in self._iter_items_with_id(
            conn,
            include_collections=include_collections,
            exclude_collections=exclude_collections,
        ):
            yield item

    def _iter_attachments(
        self,
        conn: sqlite3.Connection,
        *,
        items_by_id: dict[int, ZoteroItem],
        mime_allow: list[str],
    ) -> Iterator[ZoteroAttachment]:
        storage_root = self.library_path.parent / "storage"
        cur = conn.execute(
            """
            SELECT ia.parentItemID, child.key, ia.contentType, ia.path
            FROM itemAttachments ia
            JOIN items child ON child.itemID = ia.itemID
            WHERE ia.parentItemID IS NOT NULL
            ORDER BY ia.parentItemID, ia.itemID
            """
        )
        for parent_iid, att_key, content_type, raw_path in cur.fetchall():
            if content_type not in mime_allow:
                continue
            parent_item = items_by_id.get(parent_iid)
            if parent_item is None:
                # Parent filtered out by include/exclude collections.
                continue
            filename = _strip_storage_prefix(raw_path or "")
            if not filename:
                continue
            on_disk_path = storage_root / att_key / filename
            yield ZoteroAttachment(
                attachment_key=att_key,
                item_key=parent_item.item_key,
                parent_item_metadata=parent_item,
                on_disk_path=on_disk_path,
                mime=content_type,
                library_id=self.library_id,
            )


def _parse_year(date_str: str) -> int | None:
    """Pull a 4-digit year out of a Zotero ``date`` field.

    Zotero stores dates in many shapes (``"2024"``, ``"2024-06-01"``,
    ``"June 2024"``, ``"2024/2025"``, ...). We grab the first 4-digit
    sequence between 1000 and 2999 — good enough for the metadata-tagging
    use case.
    """
    if not date_str:
        return None
    import re  # noqa: PLC0415

    m = re.search(r"\b(1[0-9]{3}|2[0-9]{3})\b", date_str)
    if not m:
        return None
    return int(m.group(1))


def _strip_storage_prefix(path: str) -> str:
    """Zotero stores attachment paths as ``storage:filename.ext``."""
    if path.startswith("storage:"):
        return path[len("storage:") :]
    return path


__all__ = [
    "ZoteroLocalReader",
    "ZoteroSchemaUnsupported",
    "default_library_path",
]
