"""Phase M Wave 4 — ``ZoteroWebClient`` against a mocked ``api.zotero.org``.

Uses ``respx`` (httpx mock router) so HTTP semantics — pagination,
``If-Modified-Since`` 304, ``Retry-After``, exponential 5xx backoff,
attachment caching — are exercised without touching the real API.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from corpus_forge.zotero import ZoteroWebClient

# ── helpers ───────────────────────────────────────────────────────────────


def _item_json(key: str, *, title: str = "T", item_type: str = "journalArticle") -> dict:
    """Shape mimicking the Zotero v3 API item envelope."""
    return {
        "key": key,
        "version": 1,
        "library": {"type": "user", "id": 1, "name": "Test"},
        "data": {
            "key": key,
            "itemType": item_type,
            "title": title,
            "date": "2024",
            "DOI": "",
            "abstractNote": "",
            "tags": [],
            "dateModified": "2025-01-01T00:00:00Z",
        },
    }


def _attachment_json(key: str, *, parent_item: str, content_type: str = "application/pdf") -> dict:
    return {
        "key": key,
        "version": 1,
        "library": {"type": "user", "id": 1, "name": "Test"},
        "data": {
            "key": key,
            "itemType": "attachment",
            "parentItem": parent_item,
            "contentType": content_type,
            "filename": f"{key}.pdf",
            "linkMode": "imported_file",
            "dateModified": "2025-01-01T00:00:00Z",
        },
    }


# ── tests ─────────────────────────────────────────────────────────────────


class TestPagination:
    @respx.mock
    def test_iter_items_paginates(self, tmp_path: Path) -> None:
        items_page1 = [_item_json(f"KEY{i:03d}") for i in range(100)]
        items_page2 = [_item_json(f"KEY{i:03d}") for i in range(100, 175)]
        url = "https://api.zotero.org/users/1/items"

        # Page 1: 100 items + Total-Results header.
        respx.get(url, params={"start": "0", "limit": "100"}).mock(
            return_value=httpx.Response(
                200,
                json=items_page1,
                headers={"Total-Results": "175"},
            )
        )
        # Page 2: 75 items.
        respx.get(url, params={"start": "100", "limit": "100"}).mock(
            return_value=httpx.Response(
                200,
                json=items_page2,
                headers={"Total-Results": "175"},
            )
        )

        client = ZoteroWebClient(
            user_id="1",
            api_key="fake",
            cache_dir=tmp_path,
        )
        items = list(client.iter_items())
        assert len(items) == 175


class TestIfModifiedSince:
    @respx.mock
    def test_304_returns_empty_iterator(self, tmp_path: Path) -> None:
        url = "https://api.zotero.org/users/1/items"
        respx.get(url).mock(return_value=httpx.Response(304))

        client = ZoteroWebClient(user_id="1", api_key="fake", cache_dir=tmp_path)
        items = list(client.iter_items(if_modified_since="Mon, 01 Jan 2025 00:00:00 GMT"))
        assert items == []


class TestAttachmentCaching:
    @respx.mock
    def test_second_fetch_uses_cache(self, tmp_path: Path) -> None:
        # Attachment binary download endpoint.
        file_url = "https://api.zotero.org/users/1/items/ATT001/file"

        respx.get(file_url).mock(return_value=httpx.Response(200, content=b"%PDF-1.4\nFAKE\n"))

        client = ZoteroWebClient(user_id="1", api_key="fake", cache_dir=tmp_path)
        # First fetch — network call.
        path1 = client.fetch_attachment("ATT001")
        assert path1.exists()
        # Second fetch — should NOT hit the network. We assert by tracking
        # how many times respx matched the route.
        n_calls_after_first = respx.get(file_url).call_count
        path2 = client.fetch_attachment("ATT001")
        assert path2 == path1
        assert respx.get(file_url).call_count == n_calls_after_first


class TestLibraryShape:
    @respx.mock
    def test_group_url_shape(self, tmp_path: Path) -> None:
        url = "https://api.zotero.org/groups/42/items"
        respx.get(url, params={"start": "0", "limit": "100"}).mock(
            return_value=httpx.Response(200, json=[], headers={"Total-Results": "0"})
        )
        client = ZoteroWebClient(
            user_id="1",
            api_key="fake",
            library_type="group",
            group_id="42",
            cache_dir=tmp_path,
        )
        list(client.iter_items())


class TestRateLimitAndBackoff:
    @respx.mock
    def test_429_with_retry_after_retries_once(self, tmp_path: Path) -> None:
        url = "https://api.zotero.org/users/1/items"
        # First call: 429 with Retry-After: 0 (fast-path the sleep).
        # Second call: 200.
        responses = [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=[], headers={"Total-Results": "0"}),
        ]
        respx.get(url).mock(side_effect=responses)

        client = ZoteroWebClient(user_id="1", api_key="fake", cache_dir=tmp_path)
        # Should return without raising — the second response is empty.
        list(client.iter_items())
        assert respx.get(url).call_count == 2

    @respx.mock
    def test_5xx_exponential_backoff_then_raise(self, tmp_path: Path, monkeypatch) -> None:
        url = "https://api.zotero.org/users/1/items"
        respx.get(url).mock(return_value=httpx.Response(503))
        # Patch sleep so the test doesn't actually wait.
        import time

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", sleeps.append)

        client = ZoteroWebClient(user_id="1", api_key="fake", cache_dir=tmp_path)
        with pytest.raises(httpx.HTTPStatusError):
            list(client.iter_items())
        # Three tries total → two sleeps between them.
        assert respx.get(url).call_count == 3
