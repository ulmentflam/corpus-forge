"""Phase M Wave 4 — Zotero connector value types.

Frozen dataclasses shared by ``corpus_forge.zotero.local`` (SQLite reader),
``corpus_forge.zotero.web_client`` (HTTP client), and the orchestrating
``corpus_forge.sources.zotero.ZoteroSource``.

The shape of these types is the public contract for both readers — when
both are active in ``mode = "both"``, they MUST emit the same dataclass
instances so the reconciler can compare on ``item_key`` / ``attachment_key``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ZoteroItem:
    """Bibliographic item (no on-disk attachment payload).

    Args:
        item_key: Zotero's stable per-item identifier (e.g. ``"ABCD1234"``).
            For the local reader this is the ``items.key`` column; for the
            web client it's the JSON ``key`` field.
        item_type: ``journalArticle`` / ``bookSection`` / ``webpage`` / etc.
        title: Item title (may be empty for ill-formed entries).
        authors: ``["Alice Quanta", "Bob Photon"]`` ordered. Editors are
            included when no authors are present.
        year: 4-digit year parsed from the ``date`` field, or ``None``.
        doi: DOI string, or ``None``.
        abstract: Abstract / note text. Empty string when absent.
        tags: Flat list of tag names.
        collection_path: ``"Research/Quantum"`` style; empty when the item
            lives outside any collection.
        date_modified: ISO 8601-ish string. The reconciler compares lexically
            (Zotero emits ``YYYY-MM-DDTHH:MM:SSZ`` or ``YYYY-MM-DD HH:MM:SS``
            both of which sort correctly).
        library_id: ``"local"`` for the local reader; the numeric user / group
            id for the web client.
    """

    item_key: str
    item_type: str
    title: str
    authors: list[str]
    year: int | None
    doi: str | None
    abstract: str
    tags: list[str]
    collection_path: str
    date_modified: str
    library_id: str


@dataclass(frozen=True)
class ZoteroAttachment:
    """On-disk attachment + back-reference to its parent item's metadata.

    Args:
        attachment_key: Zotero attachment item key (the ``KEY`` part of the
            ``storage/<KEY>/<filename>`` path).
        item_key: Parent item's ``item_key``.
        parent_item_metadata: Full :class:`ZoteroItem` for the parent so
            the source can lift author / year / DOI / tags / collection
            onto every attachment-derived ``RawDocument`` without joining
            again.
        on_disk_path: Absolute path to the attachment file. The source
            drops attachments whose ``on_disk_path`` does not exist (e.g.
            removed-from-disk Zotero entries).
        mime: Content type (``application/pdf`` / ``text/html`` / ...).
        library_id: Matches ``parent_item_metadata.library_id``.
    """

    attachment_key: str
    item_key: str
    parent_item_metadata: ZoteroItem
    on_disk_path: Path
    mime: str
    library_id: str


@dataclass(frozen=True)
class ZoteroReconciled:
    """Result of merging local + web in ``mode = "both"``.

    Args:
        items: Deduped list of items (one per ``item_key``).
        attachments: Deduped list of attachments (one per ``attachment_key``).
        conflicts: Item keys where local and web disagreed. The reconciler
            records the choice — local wins unless web is strictly newer.
    """

    items: list[ZoteroItem]
    attachments: list[ZoteroAttachment]
    conflicts: list[str] = field(default_factory=list)


__all__ = ["ZoteroAttachment", "ZoteroItem", "ZoteroReconciled"]
