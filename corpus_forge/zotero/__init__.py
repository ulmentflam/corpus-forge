"""Phase M Wave 4 — Zotero library connector.

Two readers, one orchestrator:

- :class:`ZoteroLocalReader` reads ``zotero.sqlite`` in read-only mode
  (URI form ``mode=ro&immutable=1``) so it co-exists with a running
  Zotero instance.
- :class:`ZoteroWebClient` talks to ``api.zotero.org`` (sync ``httpx``)
  with pagination, ``If-Modified-Since``, ``Retry-After``, and
  bounded 5xx backoff.
- :class:`corpus_forge.sources.zotero.ZoteroSource` (separate module to
  avoid the heavy ``sources`` package import on the read paths) glues
  both readers together and emits :class:`RawDocument` for downstream
  chunking + embedding.
"""

from __future__ import annotations

from corpus_forge.zotero.local import (
    ZoteroLocalReader,
    ZoteroSchemaUnsupported,
    default_library_path,
)
from corpus_forge.zotero.types import (
    ZoteroAttachment,
    ZoteroItem,
    ZoteroReconciled,
)
from corpus_forge.zotero.web_client import ZoteroWebClient

__all__ = [
    "ZoteroAttachment",
    "ZoteroItem",
    "ZoteroLocalReader",
    "ZoteroReconciled",
    "ZoteroSchemaUnsupported",
    "ZoteroWebClient",
    "default_library_path",
]
