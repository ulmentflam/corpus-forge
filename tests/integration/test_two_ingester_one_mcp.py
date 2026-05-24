"""E-02 — Two-ingester / one-MCP cross-host smoke test.

Topology:
- ONE testcontainers Postgres instance.
- TWO PostgresBackend instances pointing at the same DSN:
    backend_a  (writes as host "mac-a")
    backend_b  (writes as host "mac-b")
- Migrations applied once via backend_a.migrate().
- Each backend ingests a disjoint set of 3 markdown chunks via direct SQL
  (bypasses the markdown chunker; exercises STORAGE + retrieval, not the
  source plugin).
- An MCP server is stood up IN-PROCESS via build_server() with a
  LexicalOnlyRetriever (no ML embedder; exercises search_lexical and
  list_datasets which are what the MCP tools actually call).

Three assertions (each as an independent test function sharing a
module-level fixture):
1. test_list_datasets_sees_both_hosts — list_datasets returns the shared
   dataset; both hosts' source rows are registered in corpus.sources.
2. test_search_hits_mac_a_chunks — searching for mac-a-exclusive text
   returns hits whose source_uri belongs to mac-a docs.
3. test_search_hits_mac_b_chunks — searching for mac-b-exclusive text
   returns hits whose source_uri belongs to mac-b docs.

The tests should PASS at first run (load-bearing regression pin).  If they
fail the inline note explains the exact plumbing bug surfaced.

pytestmark: pytest.mark.integration — skipped by conftest when Docker is
unavailable (CI_NO_DOCKER=1) or testcontainers cannot be imported.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.mcp.server import build_server
from corpus_forge.retrieval.types import Hit, SearchOptions
from corpus_forge.schema.migrate import apply_migrations

if TYPE_CHECKING:
    from mcp.server import Server
    from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration

# ── constants ─────────────────────────────────────────────────────────────────

_HOST_A = "mac-a"
_HOST_B = "mac-b"
_DATASET_NAME = "cross-host-smoke"

# Distinct phrases guaranteed to appear only in one host's content.
_MAC_A_PHRASE = "quasar tachyon eigenvalue"
_MAC_B_PHRASE = "meander fjord archipelago"

# Three chunks per host (disjoint content).
_MAC_A_CHUNKS = [
    "Alpha document chunk one: quasar tachyon eigenvalue resonance.",
    "Alpha document chunk two: photon lattice structure.",
    "Alpha document chunk three: quantum entanglement bridge.",
]
_MAC_B_CHUNKS = [
    "Beta document chunk one: meander fjord archipelago coastline.",
    "Beta document chunk two: tidal estuarine wetland.",
    "Beta document chunk three: mangrove delta sediment.",
]


# ── helpers ───────────────────────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_and_migrate_backend(pg_dsn: str) -> PostgresBackend:
    """Construct a PostgresBackend and apply all migrations."""
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()
    schema_dir = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
    apply_migrations(backend, schema_dir)
    return backend


def _ensure_dataset(pg_dsn: str, name: str) -> int:
    """Insert the shared dataset row; return its id."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (name, kind, description)"
            " VALUES (%s, 'text', 'E-02 cross-host smoke')"
            " ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind"
            " RETURNING id",
            (name,),
        )
        conn.commit()
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _ingest_chunks(
    backend: PostgresBackend,
    dataset_id: int,
    host: str,
    source_prefix: str,
    chunks: list[str],
) -> None:
    """Insert one document per host with N chunks directly via SQL.

    source_prefix is something like 'vault://mac-a/doc.md'.
    The sources.host column records which host wrote the row.
    """
    source_uri = f"vault://{source_prefix}/doc.md"
    full_text = "\n\n".join(chunks)
    doc_hash = _content_hash(full_text)

    # Register the source (sets sources.host = host).
    backend.register_source(
        dataset_id=dataset_id,
        plugin="markdown_vault",
        identity=source_uri,
        host=host,
    )

    # Upsert the document row.
    doc_rows = backend._execute(
        "SELECT id FROM corpus.documents WHERE dataset_id = %s AND source_uri = %s",
        (dataset_id, source_uri),
    )
    if doc_rows:
        doc_id = int(doc_rows[0]["id"])
    else:
        result = backend._execute(
            "INSERT INTO corpus.documents"
            " (dataset_id, source_uri, content_hash, title, text)"
            " VALUES (%s, %s, %s, %s, %s)"
            " RETURNING id",
            (dataset_id, source_uri, doc_hash, f"Doc from {host}", full_text),
        )
        doc_id = int(result[0]["id"])

    # Insert chunks.
    for idx, chunk_text in enumerate(chunks):
        chunk_hash = _content_hash(chunk_text)
        backend._execute(
            "INSERT INTO corpus.chunks"
            " (document_id, chunk_index, text, content_hash)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (document_id, chunk_index) DO NOTHING",
            (doc_id, idx, chunk_text, chunk_hash),
        )


# ── Lexical-only retriever stub for the MCP server ───────────────────────────


class _LexicalRetriever:
    """Minimal retriever that delegates to backend.search_lexical.

    No embedder, no dense search, no ML model load.  Sufficient for the
    MCP smoke test which verifies content reachability, not ranking quality.
    """

    def __init__(self, backend: PostgresBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: SearchOptions) -> list[Hit]:
        dataset_id: int | None = None
        if options.dataset is not None:
            dataset_id = self.backend.find_dataset_id_by_name(options.dataset)
        return self.backend.search_lexical(query, k=options.k, dataset_id=dataset_id)


# ── module-level shared state (set up once per test session via fixture) ──────


@pytest.fixture(scope="module")
def cross_host_setup(postgres_container: PostgresContainer):  # type: ignore[return]
    """Stand up the two-backend topology + MCP server once for the module.

    Yields a dict with keys:
        backend_a, backend_b, dataset_id, pg_dsn, server
    """
    c = postgres_container
    pg_dsn = (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )

    # Drop + recreate schema for a clean slate.
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Two backends — same DSN, no host arg in __init__ (host is passed per-operation).
    backend_a = _make_and_migrate_backend(pg_dsn)
    backend_b = PostgresBackend(dsn=pg_dsn, schema="corpus")  # share already-migrated schema

    dataset_id = _ensure_dataset(pg_dsn, _DATASET_NAME)

    # Each backend ingests a disjoint vault.
    _ingest_chunks(backend_a, dataset_id, _HOST_A, _HOST_A, _MAC_A_CHUNKS)
    _ingest_chunks(backend_b, dataset_id, _HOST_B, _HOST_B, _MAC_B_CHUNKS)

    # Build the MCP server in-process (lexical retriever, no ML).
    retriever = _LexicalRetriever(backend_a)

    def _retriever_builder() -> _LexicalRetriever:
        return retriever

    server = build_server(retriever_builder=_retriever_builder)

    return {
        "backend_a": backend_a,
        "backend_b": backend_b,
        "dataset_id": dataset_id,
        "pg_dsn": pg_dsn,
        "server": server,
        "retriever": retriever,
    }


# ── helper: drive an MCP tool call synchronously ─────────────────────────────


def _call_tool_sync(server: Server[object], name: str, arguments: dict) -> dict:
    """Invoke an MCP tool and return its structured result dict.

    Drives the request handler registered by build_server() directly,
    without spinning up an stdio transport.  Returns the ``structuredContent``
    dict from the handler's CallToolResult (e.g. ``{"datasets": [...]}`` or
    ``{"hits": [...]}``) and raises on ``isError=True``.
    """

    async def _run() -> dict:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers.get(CallToolRequest)
        assert handler is not None, "No CallToolRequest handler registered on server"

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        # wrapper.root is a CallToolResult
        root = wrapper.root
        if getattr(root, "isError", False):
            content_text = ""
            for block in getattr(root, "content", []):
                content_text += getattr(block, "text", "")
            raise AssertionError(f"MCP tool {name!r} returned isError=True: {content_text}")
        # structuredContent is the decoded dict; fall back to parsing content[0].text
        structured = getattr(root, "structuredContent", None)
        if structured is not None:
            return dict(structured)
        import json

        text_blocks = [getattr(b, "text", "") for b in getattr(root, "content", [])]
        return json.loads("".join(text_blocks))

    return asyncio.run(_run())


# ── tests ─────────────────────────────────────────────────────────────────────


def test_list_datasets_sees_both_hosts(cross_host_setup: dict) -> None:
    """list_datasets returns the shared dataset; both hosts have sources rows.

    list_datasets() on the backend returns one row per dataset (not per host),
    so we verify multi-host presence via a SQL query on corpus.sources.
    The MCP list_datasets tool must at minimum return the shared dataset.
    """
    server = cross_host_setup["server"]
    pg_dsn = cross_host_setup["pg_dsn"]
    dataset_id = cross_host_setup["dataset_id"]

    # ── MCP call ──
    result = _call_tool_sync(server, "list_datasets", {})
    datasets_list: list[dict] = result.get("datasets", [])

    dataset_names = [d["name"] for d in datasets_list]
    assert _DATASET_NAME in dataset_names, (
        f"Expected dataset {_DATASET_NAME!r} in list_datasets result; got: {dataset_names}"
    )

    # ── Both hosts' sources rows exist ──
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT host FROM corpus.sources"
            " WHERE dataset_id = %s AND plugin = 'markdown_vault'"
            " ORDER BY host",
            (dataset_id,),
        )
        rows = cur.fetchall()

    registered_hosts = {r[0] for r in rows}
    assert _HOST_A in registered_hosts, (
        f"Expected host {_HOST_A!r} in corpus.sources; found: {registered_hosts}"
    )
    assert _HOST_B in registered_hosts, (
        f"Expected host {_HOST_B!r} in corpus.sources; found: {registered_hosts}"
    )


def test_search_hits_mac_a_chunks(cross_host_setup: dict) -> None:
    """search('<mac-a phrase>') returns hits whose source_uri belongs to mac-a docs.

    Uses lexical search (FTS) so no embedding model is required.
    Verifies that mac-a content is reachable via the MCP search tool.
    """
    server = cross_host_setup["server"]
    pg_dsn = cross_host_setup["pg_dsn"]

    result = _call_tool_sync(server, "search", {"query": _MAC_A_PHRASE, "k": 5})
    hits: list[dict] = result.get("hits", [])

    assert len(hits) > 0, (
        f"Expected at least 1 search hit for mac-a phrase {_MAC_A_PHRASE!r}; got 0.\n"
        "This means mac-a content was not indexed or FTS is not working."
    )

    # All returned hits must come from mac-a's document (source_uri contains HOST_A).
    mac_a_source_prefix = f"vault://{_HOST_A}/"
    for hit in hits:
        source_uri = hit.get("source_uri", "")
        assert source_uri.startswith(mac_a_source_prefix), (
            f"Hit source_uri {source_uri!r} does not start with {mac_a_source_prefix!r}.\n"
            "A mac-a exclusive query returned a hit from another host — cross-host leakage."
        )

    # Verify via SQL that the chunk text contains the phrase.
    chunk_ids = [h["chunk_id"] for h in hits]
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(  # pyrefly: ignore[no-matching-overload]
            "SELECT text FROM corpus.chunks WHERE id = ANY(%s)",
            (chunk_ids,),
        )
        texts = [r[0] for r in cur.fetchall()]

    assert any(_MAC_A_PHRASE in t for t in texts), (
        f"None of the returned chunk texts contain {_MAC_A_PHRASE!r}.\nChunk texts: {texts}"
    )


def test_search_hits_mac_b_chunks(cross_host_setup: dict) -> None:
    """search('<mac-b phrase>') returns hits whose source_uri belongs to mac-b docs.

    Mirror of test_search_hits_mac_a_chunks for the mac-b side.
    Verifies that mac-b content is independently reachable via the same MCP
    server (both hosts' chunks visible through one backend connection).
    """
    server = cross_host_setup["server"]
    pg_dsn = cross_host_setup["pg_dsn"]

    result = _call_tool_sync(server, "search", {"query": _MAC_B_PHRASE, "k": 5})
    hits: list[dict] = result.get("hits", [])

    assert len(hits) > 0, (
        f"Expected at least 1 search hit for mac-b phrase {_MAC_B_PHRASE!r}; got 0.\n"
        "This means mac-b content was not indexed or FTS is not working."
    )

    mac_b_source_prefix = f"vault://{_HOST_B}/"
    for hit in hits:
        source_uri = hit.get("source_uri", "")
        assert source_uri.startswith(mac_b_source_prefix), (
            f"Hit source_uri {source_uri!r} does not start with {mac_b_source_prefix!r}.\n"
            "A mac-b exclusive query returned a hit from another host — cross-host leakage."
        )

    chunk_ids = [h["chunk_id"] for h in hits]
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(  # pyrefly: ignore[no-matching-overload]
            "SELECT text FROM corpus.chunks WHERE id = ANY(%s)",
            (chunk_ids,),
        )
        texts = [r[0] for r in cur.fetchall()]

    assert any(_MAC_B_PHRASE in t for t in texts), (
        f"None of the returned chunk texts contain {_MAC_B_PHRASE!r}.\nChunk texts: {texts}"
    )
