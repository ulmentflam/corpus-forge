"""Phase M Wave 4 — ``ZoteroSource`` plugin.

Glue between the two Zotero readers (local SQLite + Web API) and the
``RawDocument`` ingestion pipeline. Per the master plan:

- One ``RawDocument`` per PDF attachment with the parent item's metadata
  duplicated onto every attachment.
- Items with no PDF attachments emit a text-only ``RawDocument``
  carrying ``abstractNote`` IFF non-empty; otherwise skipped (DEBUG log).
- ``mode = "both"`` reconciles on ``zotero_item_key`` — local wins
  unless the web's ``dateModified`` is strictly newer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from corpus_forge.sources.base import RawDocument, WatchedSource
from corpus_forge.zotero import (
    ZoteroAttachment,
    ZoteroItem,
    ZoteroLocalReader,
    ZoteroReconciled,
    ZoteroWebClient,
)

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.vlm.base import VLMBackend
    from corpus_forge.whisper.base import WhisperBackend

logger = logging.getLogger(__name__)


def _passes_collection_filters(
    collection_path: str,
    *,
    include: list[str],
    exclude: list[str],
) -> bool:
    """Mirror :class:`ZoteroLocalReader`'s collection filtering semantics.

    - ``include`` empty → all collections accepted (subject to ``exclude``).
    - ``include`` non-empty → at least one prefix in ``include`` must
      match ``collection_path`` (root-anchored prefix match).
    - ``exclude`` always applied last; any prefix match excludes the item.
    - Web items often carry an empty ``collection_path`` (the Zotero web
      API doesn't expose collection paths on item entries — see
      :func:`corpus_forge.zotero.web_client._parse_item`). Empty path
      passes ``include`` only when ``include`` is empty.
    """
    if include and (
        not collection_path
        or not any(collection_path == p or collection_path.startswith(p + "/") for p in include)
    ):
        return False
    return not (
        exclude
        and collection_path
        and any(collection_path == p or collection_path.startswith(p + "/") for p in exclude)
    )


def _parse_zotero_dt(s: str) -> datetime | None:
    """Best-effort parse of a Zotero ``dateModified`` string to a UTC
    ``datetime``.

    Zotero emits two shapes in practice: ``YYYY-MM-DDTHH:MM:SSZ`` (Web
    API, ISO-8601) and ``YYYY-MM-DD HH:MM:SS`` (local SQLite). Both are
    naturally accepted by ``datetime.fromisoformat`` on Python 3.11+
    after normalising the trailing ``Z`` and the space-vs-T separator.
    Returns ``None`` on parse failure so callers can fall back to a
    deterministic tie-breaker.
    """
    import datetime as _dt  # noqa: PLC0415

    if not s:
        return None
    raw = s.replace(" ", "T").rstrip("Z")
    try:
        d = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.UTC)
    return d


def _is_newer(web_dt: str, local_dt: str) -> bool:
    """True iff parsed ``web_dt`` is strictly after parsed ``local_dt``.

    Both parse failures, or web unparseable → False (local wins).
    Only local unparseable → True (assume web is canonical).
    """
    w = _parse_zotero_dt(web_dt)
    if w is None:
        return False
    loc = _parse_zotero_dt(local_dt)
    if loc is None:
        return True
    return w > loc


# ── reconciliation ────────────────────────────────────────────────────


def reconcile_items(
    local_items: Iterable[ZoteroItem],
    web_items: Iterable[ZoteroItem],
) -> ZoteroReconciled:
    """Merge two iterables of items on ``item_key``.

    Rules:

    - Item present in both: local wins UNLESS ``web.date_modified``
      parses to a strictly later instant than ``local.date_modified``.
      Zotero emits both ``YYYY-MM-DDTHH:MM:SSZ`` and ``YYYY-MM-DD
      HH:MM:SS``; lexical compare is unsafe across the two because the
      ``T``/space difference is at position 10, before the time fields.
      We parse to ``datetime`` and compare; on parse failure local wins
      (deterministic tie-breaker).
    - Item present in only one source: passes through.
    - Conflicts (both sides present) are recorded in
      :attr:`ZoteroReconciled.conflicts` for auditing.

    Attachments are not merged here — the orchestrator emits them
    from the winning side's iter_attachments.
    """
    by_key_local: dict[str, ZoteroItem] = {it.item_key: it for it in local_items}
    by_key_web: dict[str, ZoteroItem] = {it.item_key: it for it in web_items}

    merged: dict[str, ZoteroItem] = {}
    conflicts: list[str] = []
    for key in by_key_local.keys() | by_key_web.keys():
        local = by_key_local.get(key)
        web = by_key_web.get(key)
        if local is not None and web is not None:
            conflicts.append(key)
            merged[key] = web if _is_newer(web.date_modified, local.date_modified) else local
        elif local is not None:
            merged[key] = local
        else:
            assert web is not None  # guaranteed by set union membership
            merged[key] = web
    items = list(merged.values())
    return ZoteroReconciled(items=items, attachments=[], conflicts=conflicts)


# ── source ────────────────────────────────────────────────────────────


class ZoteroSource(WatchedSource):
    """``WatchedSource`` over a Zotero library (local + web + reconciled)."""

    name = "zotero"
    dataset_kind = "text"

    def __init__(
        self,
        *,
        mode: Literal["local", "web", "both"] = "local",
        library_path: Path | str | None = None,
        user_id: str | None = None,
        api_key_env: str = "ZOTERO_API_KEY",
        library_type: Literal["user", "group"] = "user",
        group_id: str | None = None,
        base_url: str = "https://api.zotero.org",
        include_attachments: list[str] | None = None,
        include_collections: list[str] | None = None,
        exclude_collections: list[str] | None = None,
        cache_dir: Path | str | None = None,
        debounce: float = 2.0,
        vlm: VLMBackend | None = None,
        whisper: WhisperBackend | None = None,
    ) -> None:
        self.mode = mode
        self.library_path = Path(library_path) if library_path else None
        self.user_id = user_id
        self.api_key_env = api_key_env
        self.library_type = library_type
        self.group_id = group_id
        self.base_url = base_url
        self.include_attachments = list(include_attachments or ["application/pdf"])
        self.include_collections = list(include_collections or [])
        self.exclude_collections = list(exclude_collections or [])
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self.debounce = debounce
        self.vlm = vlm
        self.whisper = whisper

        # Skip the WatchedSource init that wants a single root Path — the
        # Zotero source's "root" is the library, which is a SQLite file
        # (local) or remote endpoint (web). identity() is overridden.
        self.root = self.library_path if self.library_path else Path("zotero://")

        # Build readers + caches.
        self._local: ZoteroLocalReader | None = None
        self._web: ZoteroWebClient | None = None
        self._library_id: str = "local"
        self._items_by_key: dict[str, ZoteroItem] = {}
        self._attachments_by_path: dict[Path, ZoteroAttachment] = {}
        self._extractor = None
        self._prime()

    # ── construction helpers ──────────────────────────────────────────

    def _prime(self) -> None:
        """Eagerly walk both readers once so ``discover()`` is O(cache)."""
        import os  # noqa: PLC0415

        local_items: list[ZoteroItem] = []
        local_attachments: list[ZoteroAttachment] = []
        web_items: list[ZoteroItem] = []

        if self.mode in ("local", "both"):
            if self.library_path is None:
                if self.mode == "local":
                    raise ValueError("ZoteroSource(mode='local') requires library_path")
            else:
                self._local = ZoteroLocalReader(self.library_path, library_id="local")
                local_items = list(
                    self._local.iter_items(
                        include_collections=self.include_collections,
                        exclude_collections=self.exclude_collections,
                    )
                )
                local_attachments = list(
                    self._local.iter_attachments(
                        include_attachments=self.include_attachments,
                        include_collections=self.include_collections,
                        exclude_collections=self.exclude_collections,
                    )
                )

        if self.mode in ("web", "both"):
            api_key = os.environ.get(self.api_key_env)
            self._web = ZoteroWebClient(
                user_id=self.user_id,
                api_key=api_key,
                library_type=self.library_type,
                group_id=self.group_id,
                base_url=self.base_url,
                cache_dir=self.cache_dir,
            )
            try:
                web_items = [
                    it
                    for it in self._web.iter_items()
                    if _passes_collection_filters(
                        it.collection_path,
                        include=self.include_collections,
                        exclude=self.exclude_collections,
                    )
                ]
            except Exception as exc:
                if self.mode == "web":
                    # Web-only source — failure must surface so the user
                    # doesn't see "0 documents" mean "everything is fine".
                    logger.error("Zotero web client failed in mode='web': %s", exc)
                    raise
                # In mode='both' a transient web failure is recoverable —
                # local data still gets ingested. Log at WARNING.
                logger.warning(
                    "Zotero web client failed during prime: %s — degrading to local-only",
                    exc,
                )
                web_items = []

        if self.mode == "both":
            merged = reconcile_items(local_items, web_items)
            self._items_by_key = {it.item_key: it for it in merged.items}
        elif self.mode == "web":
            self._items_by_key = {it.item_key: it for it in web_items}
        else:
            self._items_by_key = {it.item_key: it for it in local_items}

        for att in local_attachments:
            # Drop attachments whose on-disk file is missing — the source
            # silently skips them (the canonical case is "removed from
            # disk via Finder, still referenced in the DB").
            if not att.on_disk_path.exists():
                logger.debug(
                    "Zotero attachment file missing: %s — skipping",
                    att.on_disk_path,
                )
                continue
            # Patch the parent metadata from the reconciled items dict so
            # both-mode picks up the winning side's fields.
            parent = self._items_by_key.get(att.item_key, att.parent_item_metadata)
            self._attachments_by_path[att.on_disk_path] = ZoteroAttachment(
                attachment_key=att.attachment_key,
                item_key=att.item_key,
                parent_item_metadata=parent,
                on_disk_path=att.on_disk_path,
                mime=att.mime,
                library_id=att.library_id,
            )

        if self.mode == "local":
            self._library_id = "local"
        elif self.library_type == "group":
            # Different groups must not alias each other — using user_id
            # here would collapse every group-backed source under the
            # same source.identity() and zotero://<library_id>/ prefix.
            self._library_id = self.group_id or "unknown-group"
        else:
            self._library_id = self.user_id or "unknown-user"

    def _get_extractor(self):
        """Lazy-build the PDF digital extractor.

        Mirrors the ``FilesystemSource`` pattern — the extractor is
        constructed lazily so plain "list items" callers don't pay for
        the pymupdf import.
        """
        if self._extractor is None:
            from corpus_forge.extractors.pdf import PdfDigitalExtractor  # noqa: PLC0415

            self._extractor = PdfDigitalExtractor(vlm=self.vlm)
        return self._extractor

    # ── WatchedSource interface ───────────────────────────────────────

    def identity(self) -> str:
        """Canonical identity for the sources table."""
        if self.mode == "web":
            return f"zotero-web://{self._library_id}"
        if self.mode == "both":
            return f"zotero-both://{self._library_id}"
        return f"zotero-local://{self.library_path}"

    def discover(self) -> Iterator[Path]:
        """Yield the on-disk PDF attachment paths.

        Items with no attachments but a non-empty abstract are NOT
        yielded here — their ``RawDocument`` is built directly by
        :meth:`scan` so the chunker dispatcher sees a synthetic source
        URI rather than a non-existent file path.
        """
        yield from sorted(self._attachments_by_path.keys())

    def scan(self) -> Iterator[RawDocument]:
        """Override to surface the abstract-only docs alongside attachments."""
        # 1) Attachment-backed docs.
        for path in self.discover():
            doc = self.parse(path)
            if doc is not None:
                yield doc
        # 2) Abstract-only docs for items with no attachments but a
        #    non-empty abstractNote.
        attached_item_keys = {att.item_key for att in self._attachments_by_path.values()}
        for item in self._items_by_key.values():
            if item.item_key in attached_item_keys:
                continue
            abstract = (item.abstract or "").strip()
            if not abstract:
                logger.debug(
                    "Zotero item %s has no attachments and no abstract — skipping",
                    item.item_key,
                )
                continue
            yield _build_abstract_rawdoc(item, library_id=self._library_id)

    def parse(self, path: Path) -> RawDocument | None:
        """Look up ``path`` in the cache, run the PDF extractor, build RawDocument."""
        att = self._attachments_by_path.get(path)
        if att is None:
            logger.debug("Zotero source asked to parse unknown path %s — skipping", path)
            return None
        item = att.parent_item_metadata

        if att.mime != "application/pdf":
            # Non-PDF MIME types are not yet wired through here; they
            # are filtered out at the reader level. Belt-and-braces.
            logger.debug(
                "ZoteroSource skipping non-PDF attachment %s (mime=%s)",
                att.attachment_key,
                att.mime,
            )
            return None

        try:
            extracted = self._get_extractor().extract(path)
        except Exception as exc:
            logger.warning("PDF extraction failed for Zotero attachment %s: %s", path, exc)
            return None

        # Compose metadata: extractor's bag + reserved keys + Zotero fields.
        metadata: dict = dict(extracted.metadata)
        metadata["chunker_hint"] = extracted.chunker_hint
        if extracted.language is not None:
            metadata["language"] = extracted.language
        metadata.update(_zotero_metadata_for(item, att))

        labels: list[tuple[str, str]] = list(extracted.labels)
        labels.extend(_zotero_labels_for(item))

        try:
            modified_at = path.stat().st_mtime
        except OSError:
            modified_at = 0.0

        source_uri = f"zotero://{self._library_id}/{item.item_key}/{att.attachment_key}"
        return RawDocument(
            source_uri=source_uri,
            content_hash=self.file_content_hash(path),
            text=extracted.text,
            title=item.title or path.stem,
            modified_at=modified_at,
            metadata=metadata,
            labels=labels,
        )


# ── builders ──────────────────────────────────────────────────────────


def _zotero_metadata_for(item: ZoteroItem, att: ZoteroAttachment) -> dict:
    return {
        "zotero_item_key": item.item_key,
        "zotero_authors": list(item.authors),
        "zotero_year": item.year,
        "zotero_doi": item.doi,
        "zotero_collection": item.collection_path,
        "zotero_abstract": item.abstract or None,
        "zotero_attachment_key": att.attachment_key,
        "zotero_mime": att.mime,
        "itemType": item.item_type,
    }


def _zotero_labels_for(item: ZoteroItem) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = [("zotero_tag", tag) for tag in item.tags]
    if item.collection_path:
        labels.append(("zotero_collection", item.collection_path))
    return labels


def _build_abstract_rawdoc(item: ZoteroItem, *, library_id: str) -> RawDocument:
    """Build a RawDocument for an item with no attachments but a non-empty abstract."""
    metadata = {
        "zotero_item_key": item.item_key,
        "zotero_authors": list(item.authors),
        "zotero_year": item.year,
        "zotero_doi": item.doi,
        "zotero_collection": item.collection_path,
        "zotero_abstract": item.abstract or None,
        "zotero_attachment_key": None,
        "zotero_mime": None,
        "itemType": item.item_type,
        "chunker_hint": "markdown",
    }
    labels = _zotero_labels_for(item)
    source_uri = f"zotero://{library_id}/{item.item_key}/abstract"
    # No on-disk file → use the abstract text as the canonical content
    # hash input. ``identity.file_content_hash`` would need a path, so
    # we hash the abstract directly.
    import hashlib  # noqa: PLC0415

    content_hash = hashlib.sha256(item.abstract.encode("utf-8")).hexdigest()
    return RawDocument(
        source_uri=source_uri,
        content_hash=content_hash,
        text=item.abstract,
        title=item.title or item.item_key,
        modified_at=0.0,
        metadata=metadata,
        labels=labels,
    )


__all__ = ["ZoteroSource", "reconcile_items"]
