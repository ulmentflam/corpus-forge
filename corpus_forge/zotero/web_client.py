"""Phase M Wave 4 — Zotero Web API client (sync httpx).

Speaks the Zotero v3 REST API at ``https://api.zotero.org``. Sync only —
the ingest loop is single-threaded so we don't need anyio/asyncio.

HTTP semantics:

- 200 OK with ``Total-Results`` header drives pagination via
  ``?start=<N>&limit=100``.
- 304 Not Modified (sent when ``If-Modified-Since`` matches) yields an
  empty iterator.
- 429 Too Many Requests honors ``Retry-After`` for ONE retry, then
  raises.
- 5xx triggers bounded exponential backoff (3 tries total).

Attachment binaries are cached under ``cache_dir or ~/.cache/corpus-forge/
zotero/<library_id>/<attachment_key>``. Cache hit ⇒ no network call.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import httpx

from corpus_forge.zotero.types import ZoteroAttachment, ZoteroItem

logger = logging.getLogger(__name__)


_DEFAULT_PAGE_SIZE = 100
_MAX_RETRIES_5XX = 3
_BACKOFF_BASE = 0.5  # seconds; doubled per attempt
_HTTP_NOT_MODIFIED = 304
_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERR_LO = 500
_HTTP_SERVER_ERR_HI = 600


class ZoteroWebClient:
    """Sync httpx-backed Zotero Web API client."""

    def __init__(
        self,
        *,
        user_id: str | None,
        api_key: str | None,
        library_type: Literal["user", "group"] = "user",
        group_id: str | None = None,
        base_url: str = "https://api.zotero.org",
        cache_dir: Path | str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.user_id = user_id
        self.api_key = api_key
        self.library_type = library_type
        self.group_id = group_id
        self.base_url = base_url.rstrip("/")
        self.cache_dir = (
            Path(cache_dir).expanduser()
            if cache_dir is not None
            else Path.home() / ".cache" / "corpus-forge" / "zotero"
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    # ── URL shape ─────────────────────────────────────────────────────

    @property
    def library_id(self) -> str:
        """Tag used to namespace cached attachments + emitted ``library_id``."""
        if self.library_type == "group":
            return self.group_id or "group-unknown"
        return self.user_id or "user-unknown"

    def _items_url(self) -> str:
        if self.library_type == "group":
            return f"{self.base_url}/groups/{self.group_id}/items"
        return f"{self.base_url}/users/{self.user_id}/items"

    def _attachment_file_url(self, attachment_key: str) -> str:
        if self.library_type == "group":
            return f"{self.base_url}/groups/{self.group_id}/items/{attachment_key}/file"
        return f"{self.base_url}/users/{self.user_id}/items/{attachment_key}/file"

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Zotero-API-Version": "3"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ── public API ────────────────────────────────────────────────────

    def iter_items(
        self,
        *,
        if_modified_since: str | None = None,
    ) -> Iterator[ZoteroItem]:
        """Paginate all parent items in the library.

        Args:
            if_modified_since: RFC-1123 timestamp. The server returns 304
                when nothing has changed; we yield empty in that case.
        """
        headers = self._auth_headers()
        if if_modified_since is not None:
            headers["If-Modified-Since"] = if_modified_since

        start = 0
        url = self._items_url()
        while True:
            resp = self._get_with_retries(
                url,
                params={"start": str(start), "limit": str(_DEFAULT_PAGE_SIZE)},
                headers=headers,
            )
            if resp.status_code == _HTTP_NOT_MODIFIED:
                return
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list):
                return
            for entry in payload:
                item = _parse_item(entry, library_id=self.library_id)
                if item is not None:
                    yield item
            total = int(resp.headers.get("Total-Results", len(payload)))
            start += len(payload)
            if not payload or start >= total:
                return

    def iter_attachments(self) -> Iterator[ZoteroAttachment]:
        """Paginate the attachment view of the library.

        Note: parent metadata is NOT joined server-side. The orchestrating
        ``ZoteroSource`` is expected to call ``iter_items`` first and
        then enrich attachments by ``parentItem`` lookup. To keep the
        client simple we yield attachments with a synthetic empty
        ``parent_item_metadata`` and a placeholder ``on_disk_path`` —
        the caller resolves them via ``fetch_attachment``.
        """
        headers = self._auth_headers()
        start = 0
        url = self._items_url()
        while True:
            resp = self._get_with_retries(
                url,
                params={
                    "start": str(start),
                    "limit": str(_DEFAULT_PAGE_SIZE),
                    "itemType": "attachment",
                },
                headers=headers,
            )
            if resp.status_code == _HTTP_NOT_MODIFIED:
                return
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list):
                return
            for entry in payload:
                att = _parse_attachment(entry, library_id=self.library_id)
                if att is not None:
                    yield att
            total = int(resp.headers.get("Total-Results", len(payload)))
            start += len(payload)
            if not payload or start >= total:
                return

    def fetch_attachment(self, attachment_key: str) -> Path:
        """Download and cache an attachment's binary; return the on-disk path."""
        cache_path = self.cache_dir / self.library_id / attachment_key
        # Cache hit — short-circuit, no network call. Pick the first file
        # in the directory; we don't know the filename without metadata.
        if cache_path.exists() and any(cache_path.iterdir()):
            return next(cache_path.iterdir())
        cache_path.mkdir(parents=True, exist_ok=True)
        url = self._attachment_file_url(attachment_key)
        resp = self._get_with_retries(url, params=None, headers=self._auth_headers())
        resp.raise_for_status()
        # Filename: prefer Content-Disposition, else the attachment_key.pdf.
        filename = _filename_from_response(resp) or f"{attachment_key}.bin"
        path = cache_path / filename
        path.write_bytes(resp.content)
        return path

    def close(self) -> None:
        import contextlib  # noqa: PLC0415

        with contextlib.suppress(Exception):  # pragma: no cover — non-fatal
            self._client.close()

    # ── HTTP plumbing ─────────────────────────────────────────────────

    def _get_with_retries(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """GET with the rate-limit + 5xx-backoff semantics described above."""
        attempts = 0
        retry_after_used = False
        while True:
            attempts += 1
            resp = self._client.get(url, params=params, headers=headers)
            if resp.status_code == _HTTP_TOO_MANY and not retry_after_used:
                retry_after_used = True
                wait = _parse_retry_after(resp.headers.get("Retry-After"))
                logger.debug("Zotero 429 — sleeping %.2fs then retrying once", wait)
                time.sleep(wait)
                continue
            if _HTTP_SERVER_ERR_LO <= resp.status_code < _HTTP_SERVER_ERR_HI:
                if attempts >= _MAX_RETRIES_5XX:
                    resp.raise_for_status()
                wait = _BACKOFF_BASE * (2 ** (attempts - 1))
                logger.debug(
                    "Zotero %d — sleeping %.2fs (attempt %d/%d)",
                    resp.status_code,
                    wait,
                    attempts,
                    _MAX_RETRIES_5XX,
                )
                time.sleep(wait)
                continue
            return resp


# ── parsers ───────────────────────────────────────────────────────────


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return 1.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 1.0


def _parse_item(entry: dict, *, library_id: str) -> ZoteroItem | None:
    data = entry.get("data") or {}
    if data.get("itemType") == "attachment":
        return None
    title = data.get("title", "") or ""
    creators = data.get("creators") or []
    authors: list[str] = []
    for c in creators:
        if c.get("creatorType") not in ("author", "editor"):
            continue
        name = (
            c.get("name")
            or " ".join(p for p in (c.get("firstName") or "", c.get("lastName") or "") if p).strip()
        )
        if name:
            authors.append(name)
    tags = [t.get("tag", "") for t in (data.get("tags") or []) if t.get("tag")]
    year = _year_from_date(str(data.get("date", "")))
    return ZoteroItem(
        item_key=data.get("key") or entry.get("key", ""),
        item_type=data.get("itemType", ""),
        title=title,
        authors=authors,
        year=year,
        doi=data.get("DOI") or None,
        abstract=data.get("abstractNote", "") or "",
        tags=tags,
        collection_path="",  # web API doesn't return collection paths on items
        date_modified=str(data.get("dateModified", "")),
        library_id=library_id,
    )


def _parse_attachment(entry: dict, *, library_id: str) -> ZoteroAttachment | None:
    data = entry.get("data") or {}
    if data.get("itemType") != "attachment":
        return None
    parent = data.get("parentItem", "") or ""
    if not parent:
        return None
    # Placeholder parent metadata — orchestrator enriches by lookup.
    placeholder = ZoteroItem(
        item_key=parent,
        item_type="",
        title="",
        authors=[],
        year=None,
        doi=None,
        abstract="",
        tags=[],
        collection_path="",
        date_modified=str(data.get("dateModified", "")),
        library_id=library_id,
    )
    return ZoteroAttachment(
        attachment_key=data.get("key") or entry.get("key", ""),
        item_key=parent,
        parent_item_metadata=placeholder,
        on_disk_path=Path(),  # web client doesn't materialise until fetch
        mime=data.get("contentType", "") or "",
        library_id=library_id,
    )


def _year_from_date(date_str: str) -> int | None:
    if not date_str:
        return None
    import re  # noqa: PLC0415

    m = re.search(r"\b(1[0-9]{3}|2[0-9]{3})\b", date_str)
    return int(m.group(1)) if m else None


def _filename_from_response(resp: httpx.Response) -> str | None:
    cd = resp.headers.get("Content-Disposition", "")
    if not cd:
        return None
    # Naive parse: ``attachment; filename="foo.pdf"``.
    parts = [p.strip() for p in cd.split(";")]
    for p in parts:
        if p.lower().startswith("filename="):
            return p.split("=", 1)[1].strip().strip('"')
    return None


__all__ = ["ZoteroWebClient"]
