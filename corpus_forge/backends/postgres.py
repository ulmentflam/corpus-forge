"""PostgreSQL storage backend implementation for corpus-forge."""

import atexit
import contextlib
import json
import logging
import os
import socket
import uuid
import weakref
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..chunkers.base import TextChunk
from ..identity import advisory_lock_key, chunk_content_hash
from .base import (
    IngestRunInProgressError,
    SharedConfigVersionConflict,
    StorageBackend,
    normalize_extensions_filter,
)

if TYPE_CHECKING:
    from corpus_forge.classifiers.base import ClassifiableDocument
    from corpus_forge.sources.base import RawConversation, RawDocument

    from ..retrieval.types import Hit


# Width of the legacy ``(heading, text)`` chunk shape accepted by
# :func:`_coerce_to_textchunk`. Pulled to a module constant so the
# ``isinstance ... len(item) == _LEGACY_CHUNK_TUPLE_LEN`` check stays
# readable without a ruff ``PLR2004`` magic-number flag.
_LEGACY_CHUNK_TUPLE_LEN = 2

#: Postgres protocol bind-parameter cap. PG limits the parameter
#: count per statement to a 16-bit unsigned integer (65,535).
#: Multi-row ``INSERT ... VALUES (?, ?), (?, ?), ...`` statements
#: can exceed this on pathological inputs (a single file with
#: 8k+ chunks for the 8-param chunk INSERT, 32k+ pairs for the
#: 2-param reuse-copy). We split the batch into sub-batches that
#: stay under the cap so the bulk-insert refactor doesn't blow up
#: on edge cases. Caller-side helpers
#: (``_insert_chunks_batch`` / ``_copy_reusable_embeddings_batch``)
#: divide their inputs by the per-row param count.
_PG_MAX_BIND_PARAMS = 65535


# pgvector index-building limits (8 KB Postgres page constraint).
# Source: pgvector README §"Vector Type" / §"Half Vector Type".
#
# - ``vector(N)`` HNSW: max N = 2000
# - ``halfvec(N)`` HNSW: max N = 4000
# - ``bit(N)`` HNSW: max N = 64000 (binary embeddings only)
#
# Storage is unconstrained up to 16000 dims for both ``vector`` and
# ``halfvec``; the limit is purely on what fits in an index tuple.
# Models like Qwen3-Embedding-8B (native 4096 dims, Matryoshka-trained)
# exceed BOTH index ceilings, so we keep the storage column at the
# user's full configured dimension and build the HNSW index over a
# half-precision projection of the first ``min(dim, 4000)`` dims. The
# search query uses the SAME projection expression so the planner can
# match the index — see ``_dense_index_strategy``.
_PGVECTOR_INDEX_LIMIT = 2000
_HALFVEC_INDEX_LIMIT = 4000


def _dense_index_strategy(dimension: int) -> tuple[str, str, str]:
    """Pick the HNSW index/search SQL pair for a given embedding width.

    Returns ``(index_expression, search_expression, ops_class)``.

    The two expressions are kept byte-identical (modulo the ``e.``
    table-alias prefix used in the search query) because Postgres
    will only choose an expression-based index when the query's
    ``ORDER BY`` matches the indexed expression exactly. Drifting
    them turns every dense search into a sequential scan.

    Branches:

    - ``dim <= 2000``: full ``vector_cosine_ops`` index — back-compat
      with every pre-existing ``embeddings_*`` table in the wild. No
      schema migration required for older deployments.
    - ``dim >  2000``: ``(subvector(embedding, 1, N)::halfvec(N))``
      indexed with ``halfvec_cosine_ops``, where ``N = min(dim,
      4000)``. Storage stays full ``vector(dim)`` so the float32
      vectors are preserved; the half-precision projection is
      search-side only.

      The ``subvector(v, 1, N)`` call is required even when ``dim ==
      N`` because pgvector's ``::halfvec(N)`` cast does NOT truncate
      — it asserts the source vector is already ``N``-dimensional and
      raises ``DataException: expected N dimensions, not <actual>``
      when the source is wider. ``subvector`` does the truncation
      (returning a ``vector(N)``), then the cast becomes a same-dim
      narrowing. For Matryoshka-trained models the leading-N prefix
      is the N-dim embedding, so the truncation is search-quality-
      coherent. For dim > 4000 the index expression always truncates
      to 4000 (pgvector's halfvec HNSW ceiling).
    """
    if dimension <= _PGVECTOR_INDEX_LIMIT:
        return ("embedding", "e.embedding", "vector_cosine_ops")
    index_dim = min(dimension, _HALFVEC_INDEX_LIMIT)
    return (
        f"(subvector(embedding, 1, {index_dim})::halfvec({index_dim}))",
        f"(subvector(e.embedding, 1, {index_dim})::halfvec({index_dim}))",
        "halfvec_cosine_ops",
    )


def _coerce_to_textchunk(item: Any) -> TextChunk:
    """Normalize a chunk input to a :class:`TextChunk`.

    Phase D housekeeping (HK-2): backends accept both the production
    ``TextChunk`` shape and the legacy ``(heading, text)`` 2-tuple shape
    used by older tests and ``tests/smoke``. Coercing at the backend
    boundary lets the storage path treat everything uniformly without
    forcing every caller to migrate at once.
    """
    if isinstance(item, TextChunk):
        return item
    # Legacy ``(heading, text)`` shape.
    if isinstance(item, tuple) and len(item) == _LEGACY_CHUNK_TUPLE_LEN:
        heading, text = item
        return TextChunk(text=text, heading=heading, metadata={})
    raise TypeError(
        f"upsert_document/upsert_conversation chunk inputs must be "
        f"TextChunk or (heading, text) tuples; got {type(item).__name__}"
    )


logger = logging.getLogger(__name__)

# Valid entity types for labels and feedback helpers (mirrors sqlite.py constants).
_LABEL_ENTITY_TYPES: tuple[str, ...] = ("chunk", "document", "conversation")
_FEEDBACK_ENTITY_TYPES: tuple[str, ...] = (*_LABEL_ENTITY_TYPES, "message")

_LABEL_TABLE_MAP: dict[str, tuple[str, str]] = {
    "chunk": ("corpus.chunk_labels", "chunk_id"),
    "document": ("corpus.document_labels", "document_id"),
    "conversation": ("corpus.conversation_labels", "conversation_id"),
}

_ENTITY_TABLE_MAP: dict[str, str] = {
    "chunk": "corpus.chunks",
    "document": "corpus.documents",
    "conversation": "corpus.conversations",
}

# Maximum number of recent feedback rows returned per entity by hydrate_hit_metadata.
_RECENT_FEEDBACK_LIMIT: int = 5


def _close_pool_at_exit(pool_ref: "weakref.ReferenceType[ConnectionPool]") -> None:
    """``atexit`` callback that closes a pool at interpreter shutdown.

    Holds a **weak** reference to the pool so the callback doesn't
    pin the pool alive for the lifetime of the process — if the
    owning backend is GC'd mid-process, the pool gets reclaimed
    normally and this becomes a no-op via the dead weakref.

    Critically: ``atexit`` fires **only at interpreter shutdown**,
    not during garbage collection. Earlier we tried
    :func:`weakref.finalize` (commit ec9632e) which fires on both
    GC *and* shutdown — that interacted badly with pytest's
    py3.12 GC timing and exhausted Postgres's connection pool in
    Integration CI ("FATAL: sorry, too many clients already" on
    ~6 alembic-migration tests). Reverted in 50913a3. ``atexit``
    side-steps the issue because no callback fires until the
    process is actually exiting.
    """

    pool = pool_ref()
    if pool is None:
        return  # backend was GC'd before exit — pool already gone
    with contextlib.suppress(Exception):
        # Interpreter-shutdown best-effort: the threading machinery
        # may already be torn down by the time atexit fires, and
        # ``pool.close()`` can raise from inside the worker threads.
        # Swallow so we replace psycopg-pool's noisy "couldn't stop
        # thread" stderr spam with a clean exit.
        pool.close()


class PostgresBackend(StorageBackend):
    """PostgreSQL storage backend with pgvector support."""

    def __init__(
        self,
        dsn: str,
        schema: str = "corpus",
        *,
        pool_min_size: int = 0,
        pool_max_size: int = 8,
    ):
        self.dsn = dsn
        self.schema = schema
        self._setup_connection()
        # Connection pool. Replaces the previous per-call ``psycopg.connect``
        # which cost ~41ms of TCP+TLS+auth handshake per backend op over
        # Tailscale (profiled 2026-05-27 against the maintainer's vault).
        #
        # ``configure`` runs once per fresh connection — sets the schema
        # search_path so unqualified table names in DDL still resolve.
        # (Session-scoped state; reusing a connection from the pool
        # preserves the search_path between checkouts.)
        self._pool = ConnectionPool(
            conninfo=os.path.expandvars(self.dsn),
            min_size=pool_min_size,
            max_size=pool_max_size,
            configure=self._configure_connection,
            # ``open=True`` is default in psycopg-pool 3.2+ but it pre-warms
            # ``min_size`` connections — saves the first-query latency hit.
            open=True,
        )
        # Register pool cleanup at interpreter shutdown via ``atexit``
        # (NOT ``weakref.finalize`` — see ``_close_pool_at_exit`` above
        # for why). Without this, psycopg-pool emits "couldn't stop
        # thread 'pool-1-worker-N' within 5.0 seconds" warnings at
        # exit; with it, every CLI invocation tears down cleanly.
        # Storing the weakref on ``self`` means it stays paired with
        # the backend instance and dies with the backend — but the
        # ``atexit`` registration holds its own copy of the weakref
        # so cleanup still runs if the backend escapes our reach.
        self._pool_ref = weakref.ref(self._pool)
        atexit.register(_close_pool_at_exit, self._pool_ref)
        # Process-lifetime caches for per-file hot paths. Profiled
        # 2026-05-27: ``register_embedder`` cost ~46ms per call (3
        # round-trips over Tailscale) and was called once per file
        # during ingest; ``_copy_reusable_embeddings`` issued a fresh
        # SELECT for embedder metadata per chunk despite identical
        # ``embedder_id`` arguments. Caching:
        # - ``_embedder_id_cache``: ``embedder.name`` → ``id``. Names
        #   are stable for the life of the process so the cache never
        #   needs to invalidate; on first call we still UPDATE the
        #   row in case ``embedder.config`` has changed since last
        #   process restart.
        # - ``_embedder_info_cache``: ``id`` → ``{"name", "table_name"}``
        #   used by the embedding-reuse path. Filled lazily on first
        #   register_embedder call.
        # - ``_tables_created``: set of embedder names whose
        #   ``embeddings_<name>`` table we've already CREATE TABLE IF
        #   NOT EXISTS'd this process — the DDL is idempotent on
        #   Postgres but still costs a round-trip we can skip after
        #   the first ingest_one call.
        self._embedder_id_cache: dict[str, int] = {}
        self._embedder_info_cache: dict[int, dict[str, str]] = {}
        self._tables_created: set[str] = set()

    def _configure_connection(self, conn: psycopg.Connection) -> None:
        """One-time per-connection setup. Called by the pool the first
        time a connection is created (and again if a connection is
        replaced after a failure). Must be idempotent and side-effect-
        free beyond setting session state.
        """
        conn.execute(
            pgsql.SQL("SET search_path = {schema}, public").format(
                schema=pgsql.Identifier(self.schema)
            )
        )
        conn.commit()

    def _setup_connection(self):
        """Setup connection parameters."""
        # Convert environment variables in DSN
        expanded_dsn = os.path.expandvars(self.dsn)
        self.conn_params = {"dbname": expanded_dsn}
        # In a real implementation, we'd parse the DSN properly
        # For now, we'll assume it's a valid connection string

    def close(self) -> None:
        """Close the connection pool. Idempotent — safe to call multiple
        times (subsequent calls are no-ops). Tests + the daemon both
        rely on being able to dispose of a backend cleanly without
        leaking the pool's TCP connections.
        """
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("PG pool close failed: %s", exc)

    @contextmanager
    def _get_connection(self):
        """Check out a connection from the pool for the duration of the
        ``with`` block. The connection is returned to the pool on exit
        — so subsequent calls reuse the warm connection instead of
        paying the TCP+TLS+auth handshake every time.
        """
        with self._pool.connection() as conn:
            yield conn

    def _execute(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return results as list of dicts."""
        with self._get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)  # pyrefly: ignore[bad-argument-type]  # query is internally constructed; psycopg's LiteralString stub is too strict for our usage
            rows = cur.fetchall() if cur.description else []
            conn.commit()
            return [dict(row) for row in rows]

    def migrate(self) -> None:
        """Apply schema migrations."""
        # In a real implementation, this would run numbered SQL files
        # For now, we'll create the basic schema
        migrate_sql = """
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE SCHEMA IF NOT EXISTS corpus;
        SET search_path = corpus, public;
        
        -- Datasets / sources --------------------------------------------------------
        CREATE TABLE IF NOT EXISTS datasets (
          id          BIGSERIAL PRIMARY KEY,
          name        TEXT NOT NULL UNIQUE,
          kind        TEXT NOT NULL,                 -- 'text' | 'chat'
          description TEXT,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS sources (
          id           BIGSERIAL PRIMARY KEY,
          dataset_id   BIGINT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
          plugin       TEXT NOT NULL,               -- 'markdown_vault' | 'claude_code' | 'opencode'
          identity     TEXT NOT NULL,                -- canonical id, e.g. vault root path
          host         TEXT NOT NULL,                -- writer hostname (multi-Mac coord)
          config       JSONB NOT NULL DEFAULT '{}'::jsonb,
          last_seen_at TIMESTAMPTZ,
          UNIQUE (dataset_id, plugin, identity, host)
        );
        
        -- Documents -----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS documents (
          id           BIGSERIAL PRIMARY KEY,
          dataset_id   BIGINT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
          source_uri   TEXT NOT NULL,                -- e.g. 'vault://Claude Wiki/foo.md'
          content_hash TEXT NOT NULL,                -- sha256 of raw bytes (idempotency key)
          title        TEXT,
          text         TEXT NOT NULL,                -- HF 'text' column
          modified_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
          UNIQUE (dataset_id, source_uri)
        );
        CREATE INDEX IF NOT EXISTS documents_hash_idx ON documents(content_hash);
        
        -- Conversations -------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS conversations (
          id            BIGSERIAL PRIMARY KEY,
          dataset_id    BIGINT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
          source_uri    TEXT NOT NULL,               -- e.g. 'claude-code://<projectId>/<sessionId>'
          external_id   TEXT,
          title         TEXT,
          started_at    TIMESTAMPTZ,
          ended_at      TIMESTAMPTZ,
          message_count INT NOT NULL DEFAULT 0,
          content_hash  TEXT NOT NULL,
          metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
          UNIQUE (dataset_id, source_uri)
        );
        CREATE INDEX IF NOT EXISTS conversations_hash_idx ON conversations(content_hash);
        
        CREATE TABLE IF NOT EXISTS messages (
          id              BIGSERIAL PRIMARY KEY,
          conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          external_uuid   TEXT,
          parent_uuid     TEXT,
          turn_index      INT NOT NULL,
          role            TEXT NOT NULL,             -- 'user'|'assistant'|'system'|'tool'
          content         TEXT NOT NULL,             -- flattened text (tool calls rendered)
          tool_calls      JSONB,
          tool_results    JSONB,
          ts              TIMESTAMPTZ,
          metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
          UNIQUE (conversation_id, turn_index)
        );
        CREATE INDEX IF NOT EXISTS messages_external_idx
          ON messages(conversation_id, external_uuid);
        
        -- Chunks (the embedded unit - XOR doc/conv) ---------------------------------
        CREATE TABLE IF NOT EXISTS chunks (
          id              BIGSERIAL PRIMARY KEY,
          document_id     BIGINT REFERENCES documents(id)     ON DELETE CASCADE,
          conversation_id BIGINT REFERENCES conversations(id) ON DELETE CASCADE,
          message_id      BIGINT REFERENCES messages(id)      ON DELETE CASCADE,
          chunk_index     INT NOT NULL,
          text            TEXT NOT NULL,             -- HF 'text' column
          heading         TEXT,
          role            TEXT,                      -- echoed for chat chunks
          token_count     INT,
          content_hash    TEXT,                      -- sha256 of chunk text (dedup key)
          metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
          CHECK ( (document_id IS NOT NULL)::int + (conversation_id IS NOT NULL)::int = 1 ),
          UNIQUE (document_id, chunk_index),
          UNIQUE (conversation_id, message_id, chunk_index)
        );
        CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks(content_hash);
        CREATE INDEX IF NOT EXISTS chunks_doc_idx  ON chunks(document_id);
        CREATE INDEX IF NOT EXISTS chunks_conv_idx ON chunks(conversation_id);
        
        -- Embedders registry --------------------------------------------------------
        CREATE TABLE IF NOT EXISTS embedders (
          id          BIGSERIAL PRIMARY KEY,
          name        TEXT NOT NULL UNIQUE,          -- 'qwen3_8b' | 'openai_3l'
          provider    TEXT NOT NULL,                 -- 'sentence_transformers' | 'openai'
          model_id    TEXT NOT NULL,                 -- 'Qwen/Qwen3-Embedding-8B'
          dimension   INT NOT NULL,
          normalized  BOOLEAN NOT NULL DEFAULT TRUE,
          distance    TEXT NOT NULL DEFAULT 'cosine',-- 'cosine'|'l2'|'ip'
          active      BOOLEAN NOT NULL DEFAULT TRUE,
          table_name  TEXT NOT NULL UNIQUE,          -- e.g. 'embeddings_qwen3_8b'
          config      JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        
        -- Labels (first-class — survives HF export) ---------------------------------
        CREATE TABLE IF NOT EXISTS labels (
          id        BIGSERIAL PRIMARY KEY,
          namespace TEXT NOT NULL,                   -- 'tag'|'topic'|'language'|'classifier:foo'
          value     TEXT NOT NULL,
          UNIQUE (namespace, value)
        );
        CREATE TABLE IF NOT EXISTS chunk_labels (
          chunk_id   BIGINT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
          label_id   BIGINT NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
          confidence REAL,
          source     TEXT NOT NULL,                  -- 'frontmatter'|'auto:embedder'|'user'
          PRIMARY KEY (chunk_id, label_id, source)
        );
        CREATE TABLE IF NOT EXISTS document_labels (
          document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          label_id    BIGINT NOT NULL REFERENCES labels(id)    ON DELETE CASCADE,
          source      TEXT NOT NULL,
          PRIMARY KEY (document_id, label_id, source)
        );
        CREATE TABLE IF NOT EXISTS conversation_labels (
          conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          label_id        BIGINT NOT NULL REFERENCES labels(id)        ON DELETE CASCADE,
          source          TEXT NOT NULL,
          PRIMARY KEY (conversation_id, label_id, source)
        );
        """

        # Split by semicolon and execute each statement
        statements = [stmt.strip() for stmt in migrate_sql.split(";") if stmt.strip()]
        for statement in statements:
            if statement:
                self._execute(statement)

        # Apply numbered SQL migration files (002_chunk_content_hash.sql,
        # 003_sync.sql, etc.) for columns/tables not covered by the inline DDL.
        from pathlib import Path as _Path  # noqa: PLC0415

        from corpus_forge.schema.migrate import apply_migrations  # noqa: PLC0415

        schema_dir = _Path(__file__).parent.parent / "schema"
        apply_migrations(self, schema_dir)

    def _ensure_embedder_caches(self) -> None:
        """Lazy-initialise the per-embedder caches.

        Production code initialises these in ``__init__``; unit tests
        that bypass ``__init__`` (via ``PostgresBackend.__new__`` +
        manual mock injection) miss that initialisation. Calling this
        helper at the top of every cache-using method keeps both code
        paths happy without forcing test rewrites for what should be
        an internal performance detail.
        """
        if not hasattr(self, "_embedder_id_cache"):
            self._embedder_id_cache = {}
        if not hasattr(self, "_embedder_info_cache"):
            self._embedder_info_cache = {}
        if not hasattr(self, "_tables_created"):
            self._tables_created = set()

    def register_embedder(self, embedder) -> int:
        """Register an embedder and create its table.

        Cached for process-lifetime — first call does the
        ``SELECT/UPDATE/INSERT`` + ``CREATE TABLE IF NOT EXISTS``
        round-trips (one-time cost per embedder), subsequent calls
        return the cached id and skip every round-trip. Names are
        stable for the life of the process so the cache never
        invalidates; if a caller mutates ``embedder.config``
        mid-process they need to call this with a fresh process or
        manually invoke the SELECT/UPDATE themselves.

        Saves ~46ms per call x N files in ingest_one (profiled
        2026-05-27 against the maintainer's Tailscale-PG: 3 round-
        trips per call = ~12ms each).
        """

        self._ensure_embedder_caches()

        # Fast path: cached.
        cached_id = self._embedder_id_cache.get(embedder.name)
        if cached_id is not None:
            return cached_id

        # Sanitize embedder name for use as a SQL identifier in table_name.
        safe_name = embedder.name.replace("-", "_")
        table_name_val = f"embeddings_{safe_name}"

        # Check if embedder already exists
        existing = self._execute(
            "SELECT id FROM corpus.embedders WHERE name = %s", (embedder.name,)
        )

        if existing:
            embedder_id = existing[0]["id"]
            # Update existing record
            self._execute(
                """
                UPDATE corpus.embedders
                SET provider = %s, model_id = %s, dimension = %s,
                    normalized = %s, distance = %s, active = %s,
                    table_name = %s, config = %s
                WHERE id = %s
                """,
                (
                    embedder.provider,
                    embedder.model_id,
                    embedder.dimension,
                    embedder.normalized,
                    embedder.distance,
                    getattr(embedder, "active", True),
                    table_name_val,
                    psycopg.types.json.Json(
                        {"provider": embedder.provider, "model_id": embedder.model_id}
                    ),
                    embedder_id,
                ),
            )
        else:
            # Insert new embedder
            result = self._execute(
                """
                INSERT INTO corpus.embedders
                (name, provider, model_id, dimension, normalized, distance,
                 active, table_name, config)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    embedder.name,
                    embedder.provider,
                    embedder.model_id,
                    embedder.dimension,
                    embedder.normalized,
                    embedder.distance,
                    True,  # active
                    table_name_val,
                    psycopg.types.json.Json(
                        {"provider": embedder.provider, "model_id": embedder.model_id}
                    ),
                ),
            )
            embedder_id = result[0]["id"]

        # Create the embedder-specific table on first call only —
        # subsequent calls hit the fast path above. The DDL itself
        # is idempotent (CREATE TABLE IF NOT EXISTS) but we'd still
        # pay the round-trip without this guard.
        if embedder.name not in self._tables_created:
            self._create_embedder_table(embedder)
            self._tables_created.add(embedder.name)

        # Populate caches AFTER all the DB work succeeds so a failure
        # mid-registration doesn't poison the cache with a non-existent id.
        self._embedder_id_cache[embedder.name] = embedder_id
        self._embedder_info_cache[embedder_id] = {
            "name": embedder.name,
            "table_name": table_name_val,
        }
        return embedder_id

    def _create_embedder_table(self, embedder) -> None:
        """Create the table for storing embeddings from this embedder."""
        # Sanitize the name so it forms a valid SQL identifier (replace hyphens with underscores).
        safe_name = embedder.name.replace("-", "_")
        table_name = f"embeddings_{safe_name}"
        index_expr, _, index_ops = _dense_index_strategy(embedder.dimension)
        self._execute(
            f"""
            CREATE TABLE IF NOT EXISTS corpus.{table_name} (
              chunk_id    BIGINT PRIMARY KEY REFERENCES corpus.chunks(id) ON DELETE CASCADE,
              embedder_id BIGINT NOT NULL REFERENCES corpus.embedders(id),
              embedding   vector({embedder.dimension}) NOT NULL,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS {table_name}_hnsw
              ON corpus.{table_name}
              USING hnsw ({index_expr} {index_ops});
            """
        )

    def upsert_document(
        self,
        dataset_id: int,
        doc: "RawDocument",
        chunks: "list[TextChunk] | list[tuple[str | None, str]]",
        embedder_ids: list[int] | None = None,
    ) -> int:
        """Insert or update a document and its chunks.

        BUG-3 fix: on re-ingest we UPDATE chunks in-place where the content_hash
        matches (preserving the chunk_id and therefore the embedding rows) rather
        than DELETE-then-INSERT all chunks.  Only genuinely removed chunks are
        deleted, and only truly new chunks are inserted.

        Phase D housekeeping (HK-2): ``chunks`` accepts either
        :class:`TextChunk` instances (production path — persists
        ``metadata``/``role``/``token_count``) or legacy
        ``(heading, text)`` 2-tuples (defaults metadata to ``{}``).
        """
        # Normalize at the boundary so the rest of this method can assume
        # TextChunk shape.
        norm_chunks: list[TextChunk] = [_coerce_to_textchunk(c) for c in chunks]

        # Check if document already exists
        existing = self._execute(
            "SELECT id FROM corpus.documents WHERE dataset_id = %s AND source_uri = %s",
            (dataset_id, doc.source_uri),
        )

        if existing:
            doc_id = existing[0]["id"]
            # Check if content has changed
            current_hash = self._execute(
                "SELECT content_hash FROM corpus.documents WHERE id = %s", (doc_id,)
            )[0]["content_hash"]

            if current_hash == doc.content_hash:
                # No change, return existing doc ID
                return doc_id

            # Update document metadata
            self._execute(
                """
                UPDATE corpus.documents
                SET content_hash = %s, title = %s, text = %s,
                    modified_at = NOW(), metadata = %s
                WHERE id = %s
                """,
                (
                    doc.content_hash,
                    doc.title,
                    doc.text,
                    psycopg.types.json.Json(doc.metadata),
                    doc_id,
                ),
            )

            # Load prior chunks keyed by content_hash (for embedding reuse)
            # and by chunk_index (for update-in-place matching).
            prior_rows = self._execute(
                "SELECT id, chunk_index, content_hash, heading"
                " FROM corpus.chunks WHERE document_id = %s ORDER BY chunk_index",
                (doc_id,),
            )
            # content_hash -> first surviving chunk_id (for reuse cache seeding)
            prior_by_hash: dict[str, int] = {}
            # chunk_index -> chunk_id (for update-in-place)
            prior_by_index: dict[int, dict] = {}
            for pr in prior_rows:
                prior_by_index[pr["chunk_index"]] = pr
                if pr["content_hash"]:
                    prior_by_hash.setdefault(pr["content_hash"], pr["id"])

            # Compute the set of new content_hashes to determine which prior
            # chunks to keep vs delete.
            new_chunk_hashes = {chunk_content_hash(c.text) for c in norm_chunks}

            # Build a "reuse by hash" map: for chunks whose content_hash appears
            # in the new set, match them to new chunk positions greedily.
            # We update these in-place (keeping chunk_id → keeping embedding rows).
            # For positions without a hash match, delete old + insert new.
            reusable: dict[str, int] = {}  # content_hash -> prior chunk_id
            for ph, pid in prior_by_hash.items():
                if ph in new_chunk_hashes:
                    reusable[ph] = pid

            # Delete prior chunks that will NOT be reused (content_hash gone from new set)
            for pr in prior_rows:
                if pr["content_hash"] not in new_chunk_hashes:
                    self._execute("DELETE FROM corpus.chunks WHERE id = %s", (pr["id"],))

            # Upsert new chunks: UPDATE if we have a reusable prior chunk_id for this
            # hash (preserves embedding rows); INSERT otherwise.
            used_prior_ids: set[int] = set()
            # Reuse cache for _copy_reusable_embeddings (cross-document or new chunks)
            cache: dict[tuple[str, int], int] = {}

            for i, chunk in enumerate(norm_chunks):
                chunk_hash = chunk_content_hash(chunk.text)
                meta = psycopg.types.json.Json(chunk.metadata or {})
                prior_id = reusable.get(chunk_hash)
                if prior_id is not None and prior_id not in used_prior_ids:
                    # Update-in-place: keep chunk_id (preserves embedding rows).
                    # HK-2: also refresh metadata/role/token_count so
                    # extractor-emitted labels propagate on re-ingest.
                    self._execute(
                        """
                        UPDATE corpus.chunks
                        SET chunk_index = %s, heading = %s, text = %s,
                            metadata = %s, role = %s, token_count = %s
                        WHERE id = %s
                        """,
                        (
                            i,
                            chunk.heading,
                            chunk.text,
                            meta,
                            chunk.role,
                            chunk.token_count,
                            prior_id,
                        ),
                    )
                    used_prior_ids.add(prior_id)
                    # Embedding already exists for prior_id; no need to copy/encode
                else:
                    row = self._execute(
                        """
                        INSERT INTO corpus.chunks
                        (document_id, chunk_index, heading, text, metadata,
                         role, token_count, content_hash)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            doc_id,
                            i,
                            chunk.heading,
                            chunk.text,
                            meta,
                            chunk.role,
                            chunk.token_count,
                            chunk_hash,
                        ),
                    )
                    if embedder_ids is not None:
                        self._copy_reusable_embeddings(
                            row[0]["id"], chunk_hash, embedder_ids, cache
                        )

            # Phase D / Wave 3 — persist extractor-emitted labels on the
            # document row. Idempotent: ``apply_label`` is a no-op when
            # the (namespace, value) pair is already attached.
            self._apply_document_labels(doc_id, doc)

            return doc_id
        else:
            # Insert new document
            result = self._execute(
                """
                INSERT INTO corpus.documents
                (dataset_id, source_uri, content_hash, title, text, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    dataset_id,
                    doc.source_uri,
                    doc.content_hash,
                    doc.title,
                    doc.text,
                    psycopg.types.json.Json(doc.metadata),
                ),
            )
            doc_id = result[0]["id"]

        # Add chunks for new document — batched.
        # Previously this loop did 2 round-trips per chunk (INSERT + a
        # follow-up reuse-embedding SELECT/INSERT pair inside
        # ``_copy_reusable_embeddings``). For an N-chunk file that's
        # 2N+ round-trips x ~4ms over Tailscale = the dominant cost
        # in the 2026-05-27 ingest profile. The batched path collapses
        # this into exactly 1 INSERT (regardless of N) plus 2
        # round-trips per embedder for the reuse-embeddings copy.
        if norm_chunks:
            new_chunk_id_and_hash = self._insert_chunks_batch(doc_id, norm_chunks)
            if embedder_ids:
                self._copy_reusable_embeddings_batch(new_chunk_id_and_hash, embedder_ids)

        # Phase D / Wave 3 — persist extractor-emitted labels on the
        # document row. Idempotent: ``apply_label`` is a no-op when
        # the (namespace, value) pair is already attached.
        self._apply_document_labels(doc_id, doc)

        return doc_id

    def _insert_chunks_batch(
        self,
        doc_id: int,
        chunks: "list[TextChunk]",
    ) -> list[tuple[int, str]]:
        """Insert all chunks for a document in one round-trip.

        Returns ``[(chunk_id, content_hash), ...]`` in the same order
        as the input ``chunks`` list — the caller relies on this order
        to map back to the chunk objects for follow-up embedding-reuse
        work.

        The multi-row ``VALUES`` form lets Postgres process every row
        in one statement; the ``RETURNING`` clause hands back all the
        generated ids in INSERT order. With the chunks table's unique
        index on ``(document_id, chunk_index)`` we can rely on PG to
        validate the chunk-index uniqueness as one batch instead of N
        per-row checks.
        """

        # Split into sub-batches that stay under the Postgres
        # bind-parameter cap (65,535 / 8 params per chunk = 8,191
        # chunks per statement). Files with more chunks than that are
        # rare but would otherwise fail with a confusing PG protocol
        # error. ``chunk_index`` keeps incrementing across sub-batches
        # via ``batch_start + i`` so the returned ordering matches
        # the caller's expectations.
        _PARAMS_PER_CHUNK = 8
        max_chunks_per_batch = _PG_MAX_BIND_PARAMS // _PARAMS_PER_CHUNK
        result: list[tuple[int, str]] = []
        for batch_start in range(0, len(chunks), max_chunks_per_batch):
            sub_chunks = chunks[batch_start : batch_start + max_chunks_per_batch]
            placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(sub_chunks))
            params: list[Any] = []
            for offset, chunk in enumerate(sub_chunks):
                chunk_hash = chunk_content_hash(chunk.text)
                meta = psycopg.types.json.Json(chunk.metadata or {})
                params.extend(
                    (
                        doc_id,
                        batch_start + offset,
                        chunk.heading,
                        chunk.text,
                        meta,
                        chunk.role,
                        chunk.token_count,
                        chunk_hash,
                    )
                )
            rows = self._execute(
                f"""
                INSERT INTO corpus.chunks
                (document_id, chunk_index, heading, text, metadata,
                 role, token_count, content_hash)
                VALUES {placeholders}
                RETURNING id, content_hash
                """,
                tuple(params),
            )
            result.extend((r["id"], r["content_hash"]) for r in rows)
        return result

    def _copy_reusable_embeddings_batch(
        self,
        new_chunks: list[tuple[int, str]],
        embedder_ids: list[int],
    ) -> None:
        """Bulk version of :meth:`_copy_reusable_embeddings`.

        For each ``embedder_id``, does TWO round-trips total
        regardless of ``len(new_chunks)``:

        1. ``SELECT DISTINCT ON (content_hash) ...`` to find a prior
           chunk with each unique content_hash that already has an
           embedding stored.
        2. ``INSERT INTO embeddings_<name> SELECT ... FROM VALUES
           JOIN embeddings_<name> ON prior_chunk_id`` to copy the
           reusable vectors against the new chunk_ids.

        Compared to the per-chunk :meth:`_copy_reusable_embeddings`
        path (3 round-trips per chunk per embedder), this is a
        ~N/2-ish speedup on multi-chunk files — a 25-chunk file on
        the maintainer's vault drops from ~75 round-trips per
        embedder to 2.
        """

        if not new_chunks or not embedder_ids:
            return

        self._ensure_embedder_caches()

        # Group new chunks by content_hash so we issue one prior-lookup
        # per unique hash, not one per chunk.
        hash_to_new_chunks: dict[str, list[int]] = defaultdict(list)
        for chunk_id, h in new_chunks:
            hash_to_new_chunks[h].append(chunk_id)
        unique_hashes = list(hash_to_new_chunks.keys())

        for embedder_id in embedder_ids:
            info = self._embedder_info_cache.get(embedder_id)
            if info is None:
                # Defensive: this branch is for callers that bypass
                # register_embedder (mostly tests). The DB SELECT
                # restores the cache so subsequent batches hit the
                # fast path.
                rows = self._execute(
                    "SELECT name, table_name FROM corpus.embedders WHERE id = %s",
                    (embedder_id,),
                )
                if not rows:
                    continue
                info = {"name": rows[0]["name"], "table_name": rows[0]["table_name"]}
                self._embedder_info_cache[embedder_id] = info

            embedder_table = f"corpus.{info['table_name']}"

            # 1 RTT: prior_chunk_id per unique content_hash (newest match).
            prior_rows = self._execute(
                f"""
                SELECT DISTINCT ON (c.content_hash)
                    c.content_hash, e.chunk_id AS prior_chunk_id
                FROM corpus.chunks c
                JOIN {embedder_table} e ON e.chunk_id = c.id
                WHERE c.content_hash = ANY(%s)
                ORDER BY c.content_hash, c.id DESC
                """,
                (unique_hashes,),
            )
            hash_to_prior: dict[str, int] = {
                r["content_hash"]: r["prior_chunk_id"] for r in prior_rows
            }

            # Build (new_chunk_id, prior_chunk_id) pairs to copy.
            # Skip self-copies (the new chunk IS its own prior — happens
            # when the same chunk gets re-ingested without
            # ``current_hash == doc.content_hash`` short-circuiting,
            # e.g. when only document metadata changed).
            copy_pairs: list[tuple[int, int]] = []
            for h, new_ids in hash_to_new_chunks.items():
                prior = hash_to_prior.get(h)
                if prior is None:
                    continue
                for new_id in new_ids:
                    if new_id != prior:
                        copy_pairs.append((new_id, prior))

            if not copy_pairs:
                continue

            # Bulk-copy embeddings via VALUES + JOIN, sub-batched to
            # stay under the Postgres bind-parameter cap (65,535 / 2
            # params per pair = 32,767 pairs per statement). Each
            # sub-batch is one round-trip; the loop only kicks in for
            # files with >32k unique chunks, which is rare in practice
            # but real on pathological inputs.
            _PARAMS_PER_PAIR = 2
            max_pairs_per_batch = _PG_MAX_BIND_PARAMS // _PARAMS_PER_PAIR
            for batch_start in range(0, len(copy_pairs), max_pairs_per_batch):
                sub_pairs = copy_pairs[batch_start : batch_start + max_pairs_per_batch]
                values_placeholders = ", ".join(["(%s, %s)"] * len(sub_pairs))
                flat_pairs = [v for pair in sub_pairs for v in pair]
                self._execute(
                    f"""
                    INSERT INTO {embedder_table} (chunk_id, embedder_id, embedding)
                    SELECT t.new_id, e.embedder_id, e.embedding
                    FROM (VALUES {values_placeholders}) AS t(new_id, prior_id)
                    JOIN {embedder_table} e ON e.chunk_id = t.prior_id
                    """,
                    tuple(flat_pairs),
                )

    def _apply_document_labels(self, doc_id: int, doc: "RawDocument") -> None:
        """Persist ``doc.labels`` against the ``corpus.document_labels`` junction.

        Extractors (Phase D) emit ``ExtractedDocument.labels`` such as
        ``[("format", "pdf")]``. ``FilesystemSource`` forwards those onto
        ``RawDocument.labels``. Without this hook the labels never reach
        the database and downstream filters (``backend.list_labels`` /
        retrieval-time label enrichment) can't see them.

        Tolerates a missing or empty labels list silently. Failures while
        attaching a single label are logged at DEBUG and skipped — the
        rest of the upsert remains atomic from the caller's point of view.
        """
        labels = getattr(doc, "labels", None)
        if not labels:
            return
        for entry in labels:
            try:
                namespace, value = entry
            except (TypeError, ValueError):
                continue
            try:
                self.apply_label("document", doc_id, namespace, value, source="extractor")
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "apply_label('document', %s, %r, %r) failed: %s",
                    doc_id,
                    namespace,
                    value,
                    exc,
                )

    def upsert_conversation(
        self,
        dataset_id: int,
        conv: "RawConversation",
        chunked_messages: "list[list[TextChunk]] | list[list[tuple[str | None, str]]]",
    ) -> int:
        """Insert or update a conversation and its messages/chunks.

        Phase D housekeeping (HK-2): ``chunked_messages`` accepts either
        :class:`TextChunk` lists (preferred) or legacy
        ``(heading, text)`` 2-tuple lists, normalized at the boundary.
        """
        # Check if conversation already exists
        existing = self._execute(
            "SELECT id FROM corpus.conversations WHERE dataset_id = %s AND source_uri = %s",
            (dataset_id, conv.source_uri),
        )

        if existing:
            conv_id = existing[0]["id"]
            # Check if content has changed
            current_hash = self._execute(
                "SELECT content_hash FROM corpus.conversations WHERE id = %s", (conv_id,)
            )[0]["content_hash"]

            if current_hash == conv.content_hash:
                # No change, return existing conv ID
                return conv_id

            # Update conversation
            started_at = (
                datetime.fromtimestamp(conv.started_at, tz=UTC)
                if conv.started_at is not None
                else None
            )
            ended_at = (
                datetime.fromtimestamp(conv.ended_at, tz=UTC) if conv.ended_at is not None else None
            )
            self._execute(
                """
                UPDATE corpus.conversations
                SET content_hash = %s, title = %s, started_at = %s,
                    ended_at = %s, message_count = %s, metadata = %s
                WHERE id = %s
                """,
                (
                    conv.content_hash,
                    conv.title,
                    started_at,
                    ended_at,
                    len(conv.messages),
                    psycopg.types.json.Json(conv.metadata),
                    conv_id,
                ),
            )

            # Delete existing messages and chunks
            self._execute("DELETE FROM corpus.messages WHERE conversation_id = %s", (conv_id,))
        else:
            # Insert new conversation
            started_at = (
                datetime.fromtimestamp(conv.started_at, tz=UTC)
                if conv.started_at is not None
                else None
            )
            ended_at = (
                datetime.fromtimestamp(conv.ended_at, tz=UTC) if conv.ended_at is not None else None
            )
            result = self._execute(
                """
                INSERT INTO corpus.conversations
                (dataset_id, source_uri, external_id, title, started_at,
                 ended_at, message_count, content_hash, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    dataset_id,
                    conv.source_uri,
                    conv.external_id,
                    conv.title,
                    started_at,
                    ended_at,
                    len(conv.messages),
                    conv.content_hash,
                    psycopg.types.json.Json(conv.metadata),
                ),
            )
            conv_id = result[0]["id"]

        # Add messages
        message_ids = []
        for i, message in enumerate(conv.messages):
            msg_ts = datetime.fromtimestamp(message.ts, tz=UTC) if message.ts is not None else None
            result = self._execute(
                """
                INSERT INTO corpus.messages
                (conversation_id, external_uuid, parent_uuid, turn_index, role,
                 content, tool_calls, tool_results, ts, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    conv_id,
                    message.external_uuid,
                    message.parent_uuid,
                    i,
                    message.role,
                    message.content,
                    psycopg.types.json.Json(message.tool_calls) if message.tool_calls else None,
                    psycopg.types.json.Json(message.tool_results) if message.tool_results else None,
                    msg_ts,
                    psycopg.types.json.Json(message.metadata),
                ),
            )
            message_ids.append(result[0]["id"])

        # Add chunks for each message
        for msg_idx, chunks_in_msg in enumerate(chunked_messages):
            message_id = message_ids[msg_idx]
            for chunk_idx, raw_chunk in enumerate(chunks_in_msg):
                chunk = _coerce_to_textchunk(raw_chunk)
                meta = psycopg.types.json.Json(chunk.metadata or {})
                self._execute(
                    """
                    INSERT INTO corpus.chunks
                    (conversation_id, message_id, chunk_index, heading, text,
                     metadata, role, token_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        conv_id,
                        message_id,
                        chunk_idx,
                        chunk.heading,
                        chunk.text,
                        meta,
                        conv.messages[msg_idx].role,
                        chunk.token_count,
                    ),
                )

        return conv_id

    def write_embeddings(self, embedder_id: int, pairs: list[tuple[int, np.ndarray]]) -> None:
        """Write embeddings for chunks."""
        if not pairs:
            return

        # Get embedder info to know table name and dimension
        embedder_info = self._execute(
            "SELECT name, dimension FROM corpus.embedders WHERE id = %s", (embedder_id,)
        )

        if not embedder_info:
            raise ValueError(f"Embedder with ID {embedder_id} not found")

        embedder_name = embedder_info[0]["name"]
        table_name = f"embeddings_{embedder_name.replace('-', '_')}"

        # Insert embeddings with ON CONFLICT DO NOTHING
        for chunk_id, embedding in pairs:
            # Convert numpy array to list for PostgreSQL
            embedding_list = embedding.tolist()
            self._execute(
                f"""
                INSERT INTO corpus.{table_name} (chunk_id, embedder_id, embedding)
                VALUES (%s, %s, %s)
                ON CONFLICT (chunk_id) DO NOTHING
                """,
                (chunk_id, embedder_id, embedding_list),
            )

    def chunks_missing_embedding(
        self,
        embedder_id: int,
        limit: int = 1024,
        *,
        extensions: list[str] | None = None,
        after_id: int | None = None,
    ) -> Iterator[tuple[int, str, str]]:
        """Yield ``(chunk_id, text, source_uri)`` for chunks missing an
        embedding under ``embedder_id``.

        PR #81: the third element ``source_uri`` is read from the parent
        ``documents`` row so the routing layer can pick the right
        specialist / catchall per chunk via
        :func:`corpus_forge.embedders.routing.claims`.

        Post-#81 bugfix: ``extensions`` pushes the specialist allow-list
        into SQL so a 1000-row page is dense with matches. Without this,
        the non-cursored ``ORDER BY c.id LIMIT 1000`` always returns the
        same first page; a specialist whose extensions matched zero rows
        in that first page would loop-break in the Python caller and
        skip the remaining millions of rows. See
        :func:`corpus_forge.backends.base.normalize_extensions_filter`
        for the normalisation contract.
        """
        built = self._build_missing_embedding_query(
            embedder_id, extensions=extensions, after_id=after_id
        )
        if built is None:
            return
        from_where, params = built

        query = f"""
        SELECT
            c.id,
            c.text,
            COALESCE(d.source_uri, cv.source_uri, '') AS source_uri
        {from_where}
        ORDER BY c.id
        LIMIT %s
        """

        results = self._execute(query, (*params, limit))
        for row in results:
            yield (row["id"], row["text"], row["source_uri"] or "")

    def _build_missing_embedding_query(
        self,
        embedder_id: int,
        *,
        extensions: list[str] | None = None,
        after_id: int | None = None,
    ) -> tuple[str, tuple] | None:
        """Build the shared ``FROM ... WHERE e.chunk_id IS NULL ...`` fragment.

        Single source of truth for the "chunk missing an embedding under
        ``embedder_id``" predicate, reused by
        :meth:`chunks_missing_embedding`,
        :meth:`count_chunks_missing_embedding`, and
        :meth:`claim_chunks_for_embedding` so the three paths cannot drift.

        Returns ``(from_where_sql, params)`` where ``from_where_sql`` begins
        with ``FROM corpus.chunks c`` and ends with the ``WHERE`` clause
        (no ORDER BY / LIMIT). ``params`` are the positional ``%s`` args for
        the ext-filter + cursor clauses, in order. Returns ``None`` when the
        embedder row doesn't exist (no per-embedder table to join).

        The JOINs onto documents/conversations give the route layer
        ``source_uri`` without a second round-trip; chunks XOR the two
        parents (schema CHECK), so COALESCE picks whichever side is set.
        """
        embedder_info = self._execute(
            "SELECT name FROM corpus.embedders WHERE id = %s", (embedder_id,)
        )
        if not embedder_info:
            return None
        embedder_name = embedder_info[0]["name"]
        table_name = f"embeddings_{embedder_name.replace('-', '_')}"

        norm_exts = normalize_extensions_filter(extensions)
        ext_clause = ""
        ext_params: tuple = ()
        if norm_exts:
            # One ``lower(COALESCE(...)) LIKE %s`` per extension, OR-joined.
            # The COALESCE expression must match the SELECT projection so
            # chat chunks (whose document_id is NULL) still consult
            # conversations.source_uri.
            like_clauses = " OR ".join(
                "lower(COALESCE(d.source_uri, cv.source_uri, '')) LIKE %s" for _ in norm_exts
            )
            ext_clause = f" AND ({like_clauses})"
            ext_params = tuple(f"%{e}" for e in norm_exts)

        # Forward-progress cursor — see docstring on
        # ``StorageBackend.chunks_missing_embedding`` for why this matters
        # for catchall backfills where ``extensions`` is unset and the
        # in-memory router may filter a whole page out.
        cursor_clause = ""
        cursor_params: tuple = ()
        if after_id is not None:
            cursor_clause = " AND c.id > %s"
            cursor_params = (after_id,)

        from_where = f"""FROM corpus.chunks c
        LEFT JOIN corpus.documents d ON d.id = c.document_id
        LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id
        LEFT JOIN corpus.{table_name} e ON e.chunk_id = c.id
        WHERE e.chunk_id IS NULL{ext_clause}{cursor_clause}"""
        return from_where, (*ext_params, *cursor_params)

    def count_chunks_missing_embedding(
        self,
        embedder_id: int,
        *,
        extensions: list[str] | None = None,
    ) -> int:
        """Total number of chunks missing an embedding for ``embedder_id``.

        Phase L Wave 4 — companion to :meth:`chunks_missing_embedding`
        (no limit, no row payload). Powers the embed-command progress
        bar's ``total`` argument so the user sees an ETA.

        Post-PR-#81 bugfix: accepts the same ``extensions=`` allow-list
        as :meth:`chunks_missing_embedding` so the count reported in the
        progress bar reflects the *filtered* work for a specialist
        embedder (not the unfiltered chunks total — which would say
        "1.88 M pending" for ``nomic-code`` even when only a few
        thousand .py chunks actually qualify).
        """
        # Reuses :meth:`_build_missing_embedding_query` so the count matches
        # exactly what :meth:`chunks_missing_embedding` would yield (same
        # JOINs, same COALESCE-based extension allow-list).
        built = self._build_missing_embedding_query(embedder_id, extensions=extensions)
        if built is None:
            return 0
        from_where, params = built
        query = f"SELECT COUNT(*) AS n {from_where}"
        rows = self._execute(query, params) if params else self._execute(query)
        return int(rows[0]["n"]) if rows else 0

    # ── Fleet 2 — distributed claim-based embedding backfill ──────────────

    def claim_chunks_for_embedding(
        self,
        embedder_id: int,
        host_id: str,
        batch: int = 1024,
        lease_ttl: int = 600,
        *,
        extensions: list[str] | None = None,
        after_id: int | None = None,
    ) -> list[tuple[int, str, str]]:
        """Atomically reserve up to ``batch`` not-yet-embedded chunks for ``host_id``.

        See :meth:`StorageBackend.claim_chunks_for_embedding` for the full
        contract. Concurrency safety rests on two Postgres primitives:

        * ``FOR UPDATE SKIP LOCKED`` on the candidate ``chunks`` rows — a
          second host claiming the same lane skips rows this transaction
          has locked instead of blocking on them.
        * ``ON CONFLICT (embedder_id, chunk_id) DO NOTHING`` on the claim
          insert — if two transactions select disjoint locked sets but a
          lease-expiry sweep raced in between, the unique constraint makes
          the duplicate insert a no-op; only ``RETURNING`` rows (claims that
          actually landed) are worked.

        The claim runs as a single CTE statement so the ``SKIP LOCKED`` row
        locks stay held across the insert: ``cand`` locks the candidate
        chunk rows, ``ins`` inserts the claims (``ON CONFLICT DO NOTHING``),
        and the final SELECT returns only the chunks whose claim landed.
        The stale-claim sweep runs first, in its own committed transaction,
        so rows freed by a dead worker are visible to ``cand``'s
        live-claim anti-join in this same call.
        """
        # 1. Self-heal first (own transaction) so freed rows are claimable now.
        self.expire_stale_claims(embedder_id)

        built = self._build_missing_embedding_query(
            embedder_id, extensions=extensions, after_id=after_id
        )
        if built is None:
            return []
        from_where, params = built

        now = datetime.now(tz=UTC)
        lease_until = now + timedelta(seconds=lease_ttl)

        # 2+3. Lock the candidate set (missing-embedding shared fragment,
        #      excluding live claims) and insert the claims atomically. The
        #      final SELECT joins the locked candidates to the rows that the
        #      insert actually landed (ON CONFLICT DO NOTHING filters races).
        query = f"""
        WITH cand AS (
            SELECT c.id, c.text,
                   COALESCE(d.source_uri, cv.source_uri, '') AS source_uri
            {from_where}
              AND NOT EXISTS (
                  SELECT 1 FROM corpus.embed_claims cc
                  WHERE cc.embedder_id = %s AND cc.chunk_id = c.id
              )
            ORDER BY c.id
            LIMIT %s
            FOR UPDATE OF c SKIP LOCKED
        ),
        ins AS (
            INSERT INTO corpus.embed_claims
                (embedder_id, chunk_id, host_id, claimed_at, lease_until)
            SELECT %s, cand.id, %s, %s, %s FROM cand
            ON CONFLICT (embedder_id, chunk_id) DO NOTHING
            RETURNING chunk_id
        )
        SELECT cand.id, cand.text, cand.source_uri
        FROM cand
        JOIN ins ON ins.chunk_id = cand.id
        ORDER BY cand.id
        """
        rows = self._execute(
            query,
            (*params, embedder_id, batch, embedder_id, host_id, now, lease_until),
        )
        return [(row["id"], row["text"], row["source_uri"] or "") for row in rows]

    def release_claims(
        self,
        embedder_id: int,
        host_id: str,
        chunk_ids: list[int],
    ) -> int:
        """Release this host's claims on ``chunk_ids`` for ``embedder_id``.

        See :meth:`StorageBackend.release_claims`. Scoped to ``host_id`` so a
        worker only ever releases its own reservations (a slow host whose
        lease expired and got re-claimed elsewhere won't clobber the new
        owner's claim). Returns the number of rows deleted.
        """
        if not chunk_ids:
            return 0
        rows = self._execute(
            "DELETE FROM corpus.embed_claims "
            "WHERE embedder_id = %s AND host_id = %s AND chunk_id = ANY(%s) "
            "RETURNING chunk_id",
            (embedder_id, host_id, list(chunk_ids)),
        )
        return len(rows)

    def expire_stale_claims(self, embedder_id: int | None = None) -> int:
        """Delete claims past ``lease_until``; return the count deleted.

        See :meth:`StorageBackend.expire_stale_claims`. ``embedder_id=None``
        sweeps every lane (used by the doctor stale-claim check); a concrete
        id scopes the sweep to one embedder.
        """
        now = datetime.now(tz=UTC)
        if embedder_id is None:
            rows = self._execute(
                "DELETE FROM corpus.embed_claims WHERE lease_until < %s RETURNING chunk_id",
                (now,),
            )
        else:
            rows = self._execute(
                "DELETE FROM corpus.embed_claims "
                "WHERE embedder_id = %s AND lease_until < %s RETURNING chunk_id",
                (embedder_id, now),
            )
        return len(rows)

    def count_live_claims(
        self,
        embedder_id: int,
        exclude_host_id: str | None = None,
    ) -> int:
        """Count unexpired claims on ``embedder_id`` (RFC fleet-2).

        See :meth:`StorageBackend.count_live_claims`. ``lease_until > now``
        defines "live"; when ``exclude_host_id`` is set this host's own
        claims are excluded so the embed progress total reflects only the
        chunks *other* hosts have reserved.
        """
        now = datetime.now(tz=UTC)
        if exclude_host_id is None:
            rows = self._execute(
                "SELECT COUNT(*) AS n FROM corpus.embed_claims "
                "WHERE embedder_id = %s AND lease_until > %s",
                (embedder_id, now),
            )
        else:
            rows = self._execute(
                "SELECT COUNT(*) AS n FROM corpus.embed_claims "
                "WHERE embedder_id = %s AND lease_until > %s AND host_id <> %s",
                (embedder_id, now, exclude_host_id),
            )
        return int(rows[0]["n"]) if rows else 0

    # ── Fleet 3 — federated config publish / pull ─────────────────────────

    def get_shared_config(self) -> tuple[int, dict] | None:
        """Return ``(version, body)`` for the corpus's shared config, or None.

        See :meth:`StorageBackend.get_shared_config`. ``body`` is a ``JSONB``
        column, so psycopg decodes it straight to a dict. Empty table → None.
        """
        rows = self._execute("SELECT version, body FROM corpus.shared_config WHERE corpus_id = 1")
        if not rows:
            return None
        row = rows[0]
        return int(row["version"]), row["body"]

    def put_shared_config(
        self,
        body: dict,
        expected_version: int,
        published_by: str,
    ) -> int:
        """Atomically publish ``body`` as the next shared-config version.

        See :meth:`StorageBackend.put_shared_config`. The write is a single
        conditional statement so the optimistic-concurrency check and the
        write are one atomic operation — two hosts racing on the same
        ``expected_version`` cannot both succeed (the PK on the first
        publish, the ``version = %s`` guard on updates). No ``RETURNING``
        row means the race was lost, raising
        :class:`SharedConfigVersionConflict`.
        """
        body_json = json.dumps(body)
        now = datetime.now(tz=UTC)
        new_version = expected_version + 1
        if expected_version == 0:
            # First publish: the PK on corpus_id is the conflict guard.
            rows = self._execute(
                "INSERT INTO corpus.shared_config "
                "(corpus_id, version, body, published_by, published_at) "
                "VALUES (1, %s, %s::jsonb, %s, %s) "
                "ON CONFLICT (corpus_id) DO NOTHING "
                "RETURNING version",
                (new_version, body_json, published_by, now),
            )
        else:
            # Update: the version guard is the conflict check.
            rows = self._execute(
                "UPDATE corpus.shared_config "
                "SET version = version + 1, body = %s::jsonb, "
                "    published_by = %s, published_at = %s "
                "WHERE corpus_id = 1 AND version = %s "
                "RETURNING version",
                (body_json, published_by, now, expected_version),
            )
        if not rows:
            raise SharedConfigVersionConflict(
                f"shared config has moved past version {expected_version}; "
                "pull the current config first, then re-publish on top."
            )
        return int(rows[0]["version"])

    # ── Phase L Wave 6 — embedder-fingerprint helpers ─────────────────────

    def find_embedder_row_by_name(self, name: str) -> dict | None:
        """Return the ``corpus.embedders`` row for ``name`` (or None).

        Phase L Wave 6 — backs the
        :func:`corpus_forge.embedders.fingerprint.compare_active` drift
        path so the Wave-5 module no longer needs its ``getattr`` /
        ``try/except AttributeError`` shim on real backends.

        The returned dict carries the columns defined in the alembic
        ``0001_core`` migration plus a parsed ``config`` dict — psycopg
        normally decodes JSONB to ``dict`` automatically, but defensive
        decoding of JSON-string payloads (rare; legacy paths) keeps the
        contract uniform.
        """

        rows = self._execute(
            """
            SELECT id, name, provider, model_id, dimension, normalized,
                   distance, active, table_name, config
              FROM corpus.embedders
             WHERE name = %s
            """,
            (name,),
        )
        if not rows:
            return None
        row = dict(rows[0])
        cfg = row.get("config")
        if isinstance(cfg, str):
            try:
                row["config"] = json.loads(cfg)
            except (json.JSONDecodeError, ValueError):
                row["config"] = {}
        elif cfg is None:
            row["config"] = {}
        return row

    def count_existing_embeddings(self, embedder: int | str) -> int:
        """Count embedding rows already written for ``embedder``.

        Resolves the per-embedder table via ``embedders.table_name``,
        then ``SELECT COUNT(*) FROM corpus.<table> WHERE embedder_id = ?``.
        Returns 0 when the embedder row is missing (never raises).
        """

        if isinstance(embedder, int):
            row_q = "SELECT id, table_name FROM corpus.embedders WHERE id = %s"
        else:
            row_q = "SELECT id, table_name FROM corpus.embedders WHERE name = %s"
        rows = self._execute(row_q, (embedder,))
        if not rows:
            return 0
        embedder_id = rows[0]["id"]
        table_name = rows[0]["table_name"]
        # ``table_name`` is synthesised from a sanitised embedder name in
        # :meth:`register_embedder` — safe to f-string.
        count_rows = self._execute(
            f"SELECT COUNT(*) AS n FROM corpus.{table_name} WHERE embedder_id = %s",
            (embedder_id,),
        )
        return int(count_rows[0]["n"]) if count_rows else 0

    def update_embedder_config_blob(self, embedder: int | str, config_blob: dict) -> None:
        """Update the ``embedders.config`` JSONB for ``embedder``.

        Phase L Wave 6 — called by
        :func:`corpus_forge.embedders.fingerprint.save_active_fingerprint`
        after a successful re-embed run.  ``embedder`` may be an integer
        row id (the common path; the fingerprint module looks the row up
        first) or a string name (ergonomic callers).
        """

        json_blob = psycopg.types.json.Json(config_blob)
        if isinstance(embedder, int):
            self._execute(
                "UPDATE corpus.embedders SET config = %s WHERE id = %s",
                (json_blob, embedder),
            )
        else:
            self._execute(
                "UPDATE corpus.embedders SET config = %s WHERE name = %s",
                (json_blob, embedder),
            )

    def pending_documents(
        self, *, dataset_id: int | None = None, limit: int = 5
    ) -> tuple[int, list[str]]:
        """Documents that have no chunks yet — count + sample source URIs.

        Phase L Wave 4 — drives the ``corpus-forge estimate`` "Pending
        files" section. "Not yet chunked" is defined as
        ``NOT EXISTS (SELECT 1 FROM chunks WHERE document_id = d.id)``;
        the corpus schema has no ``documents.state`` column, so absence
        of chunk rows is the canonical signal.
        """
        if dataset_id is None:
            count_rows = self._execute(
                "SELECT COUNT(*) AS n FROM corpus.documents d "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM corpus.chunks c WHERE c.document_id = d.id"
                ")"
            )
            sample_rows = self._execute(
                "SELECT d.source_uri FROM corpus.documents d "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM corpus.chunks c WHERE c.document_id = d.id"
                ") ORDER BY d.id LIMIT %s",
                (limit,),
            )
        else:
            count_rows = self._execute(
                "SELECT COUNT(*) AS n FROM corpus.documents d "
                "WHERE d.dataset_id = %s AND NOT EXISTS ("
                "  SELECT 1 FROM corpus.chunks c WHERE c.document_id = d.id"
                ")",
                (dataset_id,),
            )
            sample_rows = self._execute(
                "SELECT d.source_uri FROM corpus.documents d "
                "WHERE d.dataset_id = %s AND NOT EXISTS ("
                "  SELECT 1 FROM corpus.chunks c WHERE c.document_id = d.id"
                ") ORDER BY d.id LIMIT %s",
                (dataset_id, limit),
            )
        count = int(count_rows[0]["n"]) if count_rows else 0
        return count, [r["source_uri"] for r in sample_rows]

    # ── Phase G P1 — multi-modal embedding helpers ─────────────────────

    def register_multimodal_embedder(
        self,
        *,
        name: str,
        model_id: str,
        dimension: int,
    ) -> int:
        """Register a multi-modal embedder + provision its image table."""
        safe_name = name.replace("-", "_")
        table_name_val = f"image_embeddings_{safe_name}"

        existing = self._execute(
            "SELECT id FROM corpus.embedders WHERE name = %s",
            (name,),
        )
        if existing:
            embedder_id = existing[0]["id"]
            self._execute(
                """
                UPDATE corpus.embedders
                SET provider = %s, model_id = %s, dimension = %s,
                    normalized = %s, distance = %s, active = %s,
                    table_name = %s, image = TRUE
                WHERE id = %s
                """,
                (
                    "multimodal",
                    model_id,
                    dimension,
                    True,
                    "cosine",
                    True,
                    table_name_val,
                    embedder_id,
                ),
            )
        else:
            result = self._execute(
                """
                INSERT INTO corpus.embedders
                (name, provider, model_id, dimension, normalized, distance,
                 active, table_name, config, image)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                RETURNING id
                """,
                (
                    name,
                    "multimodal",
                    model_id,
                    dimension,
                    True,
                    "cosine",
                    True,
                    table_name_val,
                    psycopg.types.json.Json({"provider": "multimodal", "model_id": model_id}),
                ),
            )
            embedder_id = result[0]["id"]

        index_expr, _, index_ops = _dense_index_strategy(dimension)
        self._execute(
            f"""
            CREATE TABLE IF NOT EXISTS corpus.{table_name_val} (
              chunk_id    BIGINT PRIMARY KEY REFERENCES corpus.chunks(id) ON DELETE CASCADE,
              embedder_id BIGINT NOT NULL REFERENCES corpus.embedders(id),
              embedding   vector({dimension}) NOT NULL,
              model       TEXT,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS {table_name_val}_hnsw
              ON corpus.{table_name_val}
              USING hnsw ({index_expr} {index_ops});
            """
        )
        return embedder_id

    def write_image_embeddings(
        self,
        embedder_id: int,
        pairs: "list[tuple[int, list[float]]]",
    ) -> None:
        """Write image embeddings for chunks (Phase G P1)."""
        if not pairs:
            return
        embedder_info = self._execute(
            "SELECT name, model_id FROM corpus.embedders WHERE id = %s",
            (embedder_id,),
        )
        if not embedder_info:
            raise ValueError(f"Embedder with ID {embedder_id} not found")
        embedder_name = embedder_info[0]["name"]
        model_id = embedder_info[0]["model_id"]
        table_name = f"image_embeddings_{embedder_name.replace('-', '_')}"

        for chunk_id, embedding in pairs:
            self._execute(
                f"""
                INSERT INTO corpus.{table_name} (chunk_id, embedder_id, embedding, model)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model
                """,
                (chunk_id, embedder_id, list(embedding), model_id),
            )

    def image_chunks_missing_embedding(
        self, embedder_id: int, *, limit: int = 1024
    ) -> "Iterator[tuple[int, dict]]":
        """Yield image-labeled chunks missing an embedding for ``embedder_id``."""
        embedder_info = self._execute(
            "SELECT name FROM corpus.embedders WHERE id = %s",
            (embedder_id,),
        )
        if not embedder_info:
            return
        embedder_name = embedder_info[0]["name"]
        table_name = f"image_embeddings_{embedder_name.replace('-', '_')}"

        # Join chunks → documents → document_labels → labels to filter on
        # format=image. The metadata dict is built from chunks.metadata
        # (JSONB) so callers can read ``image_uri`` etc. directly.
        query = f"""
        SELECT c.id, c.text, c.metadata
        FROM corpus.chunks c
        JOIN corpus.documents d ON d.id = c.document_id
        JOIN corpus.document_labels dl ON dl.document_id = d.id
        JOIN corpus.labels l ON l.id = dl.label_id
        LEFT JOIN corpus.{table_name} e ON e.chunk_id = c.id
        WHERE e.chunk_id IS NULL
          AND l.namespace = 'format' AND l.value = 'image'
        ORDER BY c.id
        LIMIT %s
        """
        results = self._execute(query, (limit,))
        for row in results:
            meta = row.get("metadata") or {}
            # Augment with stored chunk text so callers can use it as a
            # fallback image source (some extractors stash the path in
            # text rather than metadata).
            if "text" not in meta:
                meta = {**meta, "text": row["text"]}
            yield (row["id"], meta)

    @contextmanager
    def lock_source(self, key: str, *, wait: bool = False):
        """Context manager for advisory lock on a source.

        Args:
            key:  Advisory-lock key string (hashed to a bigint).
            wait: If ``False`` (default), use ``pg_try_advisory_lock`` — fails
                  fast and raises ``IngestRunInProgressError`` if the lock is
                  already held.  If ``True``, use ``pg_advisory_lock`` which
                  blocks until the lock becomes available.
        """
        lock_key = advisory_lock_key(key)
        with self._get_connection() as conn, conn.cursor() as cur:
            if wait:
                # Blocking acquire — blocks until the lock is available.
                cur.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
                acquired = True
            else:
                # Non-blocking acquire — returns False if already held.
                cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
                row = cur.fetchone()
                acquired = row[0] if row is not None else False

            if not acquired:
                raise IngestRunInProgressError(
                    f"Could not acquire lock for source: {key}. "
                    "Another ingest run may be in progress on this host."
                )

            try:
                yield
            finally:
                # Release the lock
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))

    def delete_document(self, dataset_id: int, source_uri: str) -> None:
        """Delete a document and its chunks."""
        self._execute(
            """
            DELETE FROM corpus.documents
            WHERE dataset_id = %s AND source_uri = %s
            """,
            (dataset_id, source_uri),
        )

    def resolve_document(self, dataset_id: int, source_uri: str) -> dict | None:
        """Idempotently look up or CREATE a documents row by (dataset_id, source_uri).

        Returns the row as a dict with at least ``id`` and ``content_hash``.
        For new rows, inserts with empty text, empty content_hash, NULL title,
        and empty metadata.  Returns None only if source_uri is an empty string.

        Use this method when the caller must ensure a row exists (e.g. push
        handle_change).  For delete-side lookups that should NOT create stubs,
        use ``find_document`` instead.
        """
        if not source_uri:
            return None
        rows = self._execute(
            "SELECT id, content_hash FROM corpus.documents"
            " WHERE dataset_id = %s AND source_uri = %s",
            (dataset_id, source_uri),
        )
        if rows:
            return rows[0]
        result = self._execute(
            """
            INSERT INTO corpus.documents (dataset_id, source_uri, content_hash, text, metadata)
            VALUES (%s, %s, '', '', '{}'::jsonb)
            RETURNING id, content_hash
            """,
            (dataset_id, source_uri),
        )
        return result[0] if result else None

    def find_document(self, dataset_id: int, source_uri: str) -> dict | None:
        """Look up a documents row without creating one.

        Returns None if no row exists for (dataset_id, source_uri).
        """
        rows = self._execute(
            "SELECT id, content_hash FROM corpus.documents"
            " WHERE dataset_id = %s AND source_uri = %s",
            (dataset_id, source_uri),
        )
        return rows[0] if rows else None

    def resolve_self_source(self, dataset_id: int, host: str) -> int:
        """Upsert a sources row for this host's pull tracker and return its id."""
        rows = self._execute(
            "SELECT id FROM corpus.sources"
            " WHERE dataset_id = %s AND plugin = %s AND identity = %s AND host = %s",
            (dataset_id, "sync", "pull", host),
        )
        if rows:
            return int(rows[0]["id"])
        result = self._execute(
            """
            INSERT INTO corpus.sources (dataset_id, plugin, identity, host)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (dataset_id, "sync", "pull", host),
        )
        return int(result[0]["id"])

    def _copy_reusable_embeddings(
        self,
        new_chunk_id: int,
        content_hash: str,
        embedder_ids: list[int],
        cache: dict,
    ) -> set[int]:
        """Copy embeddings from prior chunks with the same content hash.

        For each embedder_id, looks up an existing chunk with the same content_hash
        that already has an embedding vector stored. If found, the vector row is
        copied (INSERT SELECT) for the new chunk. Cache entries skip the SELECT.

        Returns the set of embedder_ids whose embeddings were reused.
        """
        reused = set()

        embedder_info = {}
        for eid in embedder_ids:
            row = self._execute(
                "SELECT name, table_name FROM corpus.embedders WHERE id = %s",
                (eid,),
            )
            if row:
                embedder_info[eid] = row[0]

        for embedder_id in sorted(embedder_ids, key=lambda x: (x % 2 == 0, x)):
            if embedder_id not in embedder_info:
                continue

            info = embedder_info[embedder_id]
            embedder_table = f"corpus.{info['table_name']}"

            cache_key = (content_hash, embedder_id)
            prior_chunk_id = cache.get(cache_key)

            if prior_chunk_id is None:
                rows = self._execute(
                    f"""
                    SELECT e.chunk_id FROM corpus.chunks c
                    JOIN {embedder_table} e ON e.chunk_id = c.id
                    WHERE c.content_hash = %s AND c.id != %s
                    ORDER BY c.id DESC LIMIT 1
                    """,
                    (content_hash, new_chunk_id),
                )
                if not rows:
                    continue
                prior_chunk_id = rows[0]["chunk_id"]
                cache[cache_key] = prior_chunk_id

            self._execute(
                f"""
                INSERT INTO {embedder_table} (chunk_id, embedder_id, embedding)
                SELECT %s, embedder_id, embedding FROM {embedder_table}
                WHERE chunk_id = %s
                """,
                (new_chunk_id, prior_chunk_id),
            )
            reused.add(embedder_id)

        return reused

    def delete_conversation(self, dataset_id: int, source_uri: str) -> None:
        """Delete a conversation and its messages/chunks."""
        self._execute(
            """
            DELETE FROM corpus.conversations 
            WHERE dataset_id = %s AND source_uri = %s
            """,
            (dataset_id, source_uri),
        )

    # ── Revisions / Sync ─────────────────────────────────────────────────────

    def insert_revision(
        self,
        *,
        document_id: int,
        source_uri: str,  # noqa: ARG002 — part of public API (base.py); not stored in SQL directly
        content_hash: str,
        text: str,
        parent_revision_id: int | None,
        author_host: str,
        is_tombstone: bool,
        metadata: dict | None = None,
    ) -> dict:
        """Insert a new revision, returning id + revision_number.

        Callers are expected to already hold ``lock_source(source_uri)`` so that
        the ``MAX(revision_number)+1`` allocation is atomic.  The internal lock
        acquisition was removed to avoid double-lock across separate connections.
        """
        max_row = self._execute(
            "SELECT MAX(revision_number) AS max FROM corpus.document_revisions"
            " WHERE document_id = %s",
            (document_id,),
        )
        revision_number = (max_row[0]["max"] or 0) + 1
        result = self._execute(
            """
            INSERT INTO corpus.document_revisions
            (document_id, revision_number, parent_revision_id, content_hash,
             text, author_host, is_tombstone, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id, revision_number
            """,
            (
                document_id,
                revision_number,
                parent_revision_id,
                content_hash,
                text,
                author_host,
                is_tombstone,
                psycopg.types.json.Json(metadata) if metadata else psycopg.types.json.Json({}),
            ),
        )
        return {"id": result[0]["id"], "revision_number": result[0]["revision_number"]}

    def latest_revision(self, document_id: int) -> dict | None:
        """Return the highest revision_number row for a document, or None."""
        rows = self._execute(
            "SELECT * FROM corpus.document_revisions"
            " WHERE document_id = %s ORDER BY revision_number DESC LIMIT 1",
            (document_id,),
        )
        return rows[0] if rows else None

    def pending_remote_revisions(
        self,
        dataset_id: int,
        last_pulled_revision_id: int | None,
        self_host: str,
        *,
        limit: int = 1024,
    ) -> list[dict]:
        """Return revisions from other hosts not yet pulled.

        Each returned dict includes ``source_uri`` (from documents) and
        ``parent_content_hash`` (from the parent revision row) in addition to all
        columns of document_revisions.
        """
        last_id = last_pulled_revision_id if last_pulled_revision_id is not None else 0
        return self._execute(
            """
            SELECT r.*,
                   d.source_uri AS source_uri,
                   parent.content_hash AS parent_content_hash
            FROM corpus.document_revisions r
            JOIN corpus.documents d ON d.id = r.document_id
            LEFT JOIN corpus.document_revisions parent ON parent.id = r.parent_revision_id
            WHERE d.dataset_id = %s AND r.id > %s AND r.author_host <> %s
            ORDER BY r.id ASC LIMIT %s
            """,
            (dataset_id, last_id, self_host, limit),
        )

    def mark_revision_pulled(self, source_id: int, revision_id: int) -> None:
        """Advance last_pulled_revision_id for a source using GREATEST."""
        self._execute(
            "UPDATE corpus.sources"
            " SET last_pulled_revision_id = GREATEST(COALESCE(last_pulled_revision_id, 0), %s)"
            " WHERE id = %s",
            (revision_id, source_id),
        )

    def set_tombstone(self, document_id: int) -> None:
        """Mark a document as tombstoned."""
        self._execute(
            "UPDATE corpus.documents SET tombstoned_at = NOW() WHERE id = %s",
            (document_id,),
        )

    def clear_tombstone(self, document_id: int) -> None:
        """Remove tombstone from a document."""
        self._execute(
            "UPDATE corpus.documents SET tombstoned_at = NULL WHERE id = %s",
            (document_id,),
        )

    def get_or_create_dataset(self, name: str, kind: str, description: str) -> int:
        """Return the id of the named dataset, creating it if absent."""
        existing = self._execute("SELECT id FROM corpus.datasets WHERE name = %s", (name,))
        if existing:
            return existing[0]["id"]
        result = self._execute(
            """
            INSERT INTO corpus.datasets (name, kind, description)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (name, kind, description),
        )
        return result[0]["id"]

    def find_dataset_id_by_name(self, name: str) -> int | None:
        """Return the id of the named dataset, or None if it does not exist."""
        rows = self._execute("SELECT id FROM corpus.datasets WHERE name = %s", (name,))
        return rows[0]["id"] if rows else None

    def register_source(self, dataset_id: int, plugin: str, identity: str, host: str) -> int:
        """Upsert a sources row for a plugin/identity/host triple and return its id."""
        rows = self._execute(
            "SELECT id FROM corpus.sources"
            " WHERE dataset_id = %s AND plugin = %s AND identity = %s AND host = %s",
            (dataset_id, plugin, identity, host),
        )
        if rows:
            return int(rows[0]["id"])
        result = self._execute(
            """
            INSERT INTO corpus.sources (dataset_id, plugin, identity, host)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (dataset_id, plugin, identity, host),
        )
        return int(result[0]["id"])

    # ── Retrieval surface (Phase R1) ─────────────────────────────────────────

    def search_dense(
        self,
        embedder_id: int,
        query_vector: "np.ndarray",
        *,
        k: int,
        dataset_id: int | None = None,
        chunk_ids: "frozenset[int] | None" = None,
    ) -> "list":
        """pgvector cosine search via ``<=>`` against the per-embedder table.

        Returns ``Hit`` objects ordered by descending similarity
        (``score = 1.0 - cosine_distance``).  ``LEFT JOIN`` documents +
        conversations so message-only chunks are still attributable to a
        dataset.

        Phase N Wave 3 — ``chunk_ids`` filter:

        - ``None`` (default): pre-Wave-3 behaviour preserved.
        - empty ``frozenset()``: returns ``[]`` immediately ("filter to
          nothing" — distinct from "no filter").
        - non-empty: ``WHERE c.id = ANY(%s)`` restricts the search to
          the candidate pool surfaced by the fast tier (shortcut mode).
        """
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        # Phase N Wave 3 — empty filter short-circuits.
        if chunk_ids is not None and not chunk_ids:
            return []

        # Look up the per-embedder table (e.g. corpus.embeddings_qwen3_8b)
        # plus the dimension so we can pick the matching index strategy.
        rows = self._execute(
            "SELECT table_name, dimension FROM corpus.embedders WHERE id = %s",
            (embedder_id,),
        )
        if not rows:
            return []
        table_name = rows[0]["table_name"]
        dim = int(rows[0]["dimension"])

        # pgvector expects the literal vector(...) cast for psycopg's
        # parameter binding.  Serialise to "[v1,v2,...]" form.
        #
        # For the halfvec-projection strategy (dim > 2000), the indexed
        # column is ``subvector(embedding, 1, N)::halfvec(N)`` — so the
        # query vector must ALSO be exactly N dims wide before casting
        # to ``halfvec(N)``. The Matryoshka prefix is a quality-
        # preserving truncation. We slice Python-side instead of
        # wrapping in another ``subvector`` SQL call so the literal
        # passed to psycopg already has the right shape and pgvector
        # doesn't reject the cast with ``DataException: expected N
        # dimensions, not <dim>``.
        flat = np.asarray(query_vector).ravel()
        if dim > _PGVECTOR_INDEX_LIMIT:
            index_dim = min(dim, _HALFVEC_INDEX_LIMIT)
            flat = flat[:index_dim]
        vec_str = "[" + ",".join(repr(float(x)) for x in flat) + "]"

        params: list = [vec_str]
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = %s"
            params.append(dataset_id)
        chunk_filter = ""
        if chunk_ids is not None:
            chunk_filter = " AND c.id = ANY(%s)"
            params.append(list(chunk_ids))
        params.append(k)

        # Pick the search expression + query cast that MATCH the HNSW
        # index built in ``_create_embedder_table``. The expressions
        # must be byte-identical (modulo the ``e.`` alias) or Postgres
        # falls back to a sequential scan.
        _, search_expr, _ = _dense_index_strategy(dim)
        if dim <= _PGVECTOR_INDEX_LIMIT:
            query_cast = "%s::vector"
        else:
            index_dim = min(dim, _HALFVEC_INDEX_LIMIT)
            # Query vector is already truncated to index_dim above, so
            # the cast is a same-dim narrowing and matches the index
            # expression's ``halfvec(index_dim)`` exactly.
            query_cast = f"%s::halfvec({index_dim})"

        # NB: f-string interpolation of table_name is safe — register_embedder
        # synthesises it from a sanitised embedder name.
        result = self._execute(
            f"""
            SELECT c.id, c.text, c.document_id, c.conversation_id, c.metadata,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title,
                   ({search_expr} <=> {query_cast}) AS distance
            FROM corpus.{table_name} e
            JOIN corpus.chunks c ON c.id = e.chunk_id
            LEFT JOIN corpus.documents d ON d.id = c.document_id
            LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id
            WHERE TRUE{ds_filter}{chunk_filter}
            ORDER BY {search_expr} <=> {query_cast}
            LIMIT %s
            """,
            (vec_str, *params[1:-1], vec_str, params[-1]),
        )

        hits: list = []
        for r in result:
            dist = float(r["distance"])
            hits.append(
                Hit(
                    chunk_id=int(r["id"]),
                    score=1.0 - dist,
                    text=r["text"],
                    document_id=(int(r["document_id"]) if r["document_id"] is not None else None),
                    source_uri=r["source_uri"],
                    title=r["title"],
                    dataset_id=int(r["dataset_id"]) if r["dataset_id"] is not None else 0,
                    metadata=r["metadata"] if isinstance(r["metadata"], dict) else {},
                    source="dense",
                )
            )
        return hits

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
        chunk_ids: "frozenset[int] | None" = None,
    ) -> "list":
        """``text_tsv @@ websearch_to_tsquery('english', %s)`` ranked by ts_rank_cd.

        ``ts_rank_cd`` is already higher-is-better, so we use it directly as
        ``Hit.score`` (clipping into [0, 1] — typical ts_rank_cd values for
        short documents stay well inside that range but the contract says
        normalised scores so we cap).

        Phase N Wave 3 — ``chunk_ids`` filter (same semantics as
        :meth:`search_dense`): ``None`` = no filter, ``frozenset()`` =
        empty result, non-empty = ``WHERE c.id = ANY(%s)``.
        """
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        # Phase N Wave 3 — empty filter short-circuits.
        if chunk_ids is not None and not chunk_ids:
            return []

        params: list = [query, query]  # one for the rank, one for the WHERE
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = %s"
            params.append(dataset_id)
        chunk_filter = ""
        if chunk_ids is not None:
            chunk_filter = " AND c.id = ANY(%s)"
            params.append(list(chunk_ids))
        params.append(k)

        rows = self._execute(
            f"""
            SELECT c.id, c.text, c.document_id, c.conversation_id, c.metadata,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title,
                   ts_rank_cd(c.text_tsv, websearch_to_tsquery('english', %s)) AS rank
            FROM corpus.chunks c
            LEFT JOIN corpus.documents d ON d.id = c.document_id
            LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id
            WHERE c.text_tsv @@ websearch_to_tsquery('english', %s){ds_filter}{chunk_filter}
            ORDER BY rank DESC
            LIMIT %s
            """,
            tuple(params),
        )

        hits: list = []
        for r in rows:
            rank = float(r["rank"]) if r["rank"] is not None else 0.0
            # ts_rank_cd is unbounded above; clip the long tail at 1.0 to
            # satisfy the protocol's "normalised [0,1]" guidance.
            score = min(max(rank, 0.0), 1.0)
            hits.append(
                Hit(
                    chunk_id=int(r["id"]),
                    score=score,
                    text=r["text"],
                    document_id=(int(r["document_id"]) if r["document_id"] is not None else None),
                    source_uri=r["source_uri"],
                    title=r["title"],
                    dataset_id=int(r["dataset_id"]) if r["dataset_id"] is not None else 0,
                    metadata=r["metadata"] if isinstance(r["metadata"], dict) else {},
                    source="lexical",
                )
            )
        return hits

    def get_chunk(self, chunk_id: int) -> "dict | None":
        """Return chunk row joined to documents + conversations (LEFT JOIN).

        Additive (agent-chunk-explorer): also includes ``prev_chunk_id``
        and ``next_chunk_id`` (``int | None``) so callers can chain
        follow-up lookups without a second query.
        """
        rows = self._execute(
            """
            SELECT c.id, c.document_id, c.conversation_id, c.message_id,
                   c.chunk_index, c.text, c.heading, c.role, c.token_count,
                   c.metadata, c.content_hash,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title
            FROM corpus.chunks c
            LEFT JOIN corpus.documents d ON d.id = c.document_id
            LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id
            WHERE c.id = %s
            """,
            (chunk_id,),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["prev_chunk_id"], row["next_chunk_id"] = self._chunk_prev_next_ids(row)
        return row

    def _chunk_prev_next_ids(self, row: dict) -> tuple[int | None, int | None]:
        """Compute (prev_chunk_id, next_chunk_id) for a chunk row.

        Defensive: missing ``chunk_index`` (test mocks, partial rows)
        short-circuits to ``(None, None)`` rather than raising.
        """
        idx = row.get("chunk_index")
        if idx is None:
            return None, None
        if row.get("document_id") is not None:
            doc_id = row["document_id"]
            prev = self._execute(
                "SELECT id FROM corpus.chunks "
                "WHERE document_id = %s AND chunk_index < %s "
                "ORDER BY chunk_index DESC LIMIT 1",
                (doc_id, idx),
            )
            nxt = self._execute(
                "SELECT id FROM corpus.chunks "
                "WHERE document_id = %s AND chunk_index > %s "
                "ORDER BY chunk_index ASC LIMIT 1",
                (doc_id, idx),
            )
            return (
                int(prev[0]["id"]) if prev else None,
                int(nxt[0]["id"]) if nxt else None,
            )
        if row.get("conversation_id") is not None:
            convo_id = row["conversation_id"]
            msg_id = row.get("message_id")
            prev = self._execute(
                "SELECT id FROM corpus.chunks "
                "WHERE conversation_id = %s "
                "  AND (message_id < %s OR (message_id = %s AND chunk_index < %s)) "
                "ORDER BY message_id DESC, chunk_index DESC LIMIT 1",
                (convo_id, msg_id, msg_id, idx),
            )
            nxt = self._execute(
                "SELECT id FROM corpus.chunks "
                "WHERE conversation_id = %s "
                "  AND (message_id > %s OR (message_id = %s AND chunk_index > %s)) "
                "ORDER BY message_id ASC, chunk_index ASC LIMIT 1",
                (convo_id, msg_id, msg_id, idx),
            )
            return (
                int(prev[0]["id"]) if prev else None,
                int(nxt[0]["id"]) if nxt else None,
            )
        return None, None

    def get_chunk_neighbors(
        self,
        chunk_id: int,
        *,
        before: int = 1,
        after: int = 1,
    ) -> "list[dict]":
        """Return ``before`` preceding + ``after`` following neighbor chunks.

        Same row shape as :meth:`get_chunk` (minus prev/next ids — they
        only make sense for the anchor). Returns ``[]`` if the anchor
        chunk doesn't exist. ``before=0`` and/or ``after=0`` is valid.
        """
        if before < 0 or after < 0:
            raise ValueError("`before` and `after` must be >= 0")
        anchor_rows = self._execute(
            "SELECT id, document_id, conversation_id, message_id, chunk_index "
            "FROM corpus.chunks WHERE id = %s",
            (chunk_id,),
        )
        if not anchor_rows:
            return []
        anchor = anchor_rows[0]
        select_cols = (
            "c.id, c.document_id, c.conversation_id, c.message_id, "
            "c.chunk_index, c.text, c.heading, c.role, c.token_count, "
            "c.metadata, c.content_hash, "
            "COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id, "
            "d.source_uri, d.title"
        )
        joins = (
            "FROM corpus.chunks c "
            "LEFT JOIN corpus.documents d ON d.id = c.document_id "
            "LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id"
        )
        out: list[dict] = []
        if anchor["document_id"] is not None:
            doc_id = anchor["document_id"]
            idx = anchor["chunk_index"]
            if before > 0:
                prev_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.document_id = %s AND c.chunk_index < %s "
                    f"ORDER BY c.chunk_index DESC LIMIT %s",
                    (doc_id, idx, before),
                )
                out.extend(reversed([dict(r) for r in prev_rows]))
            if after > 0:
                next_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.document_id = %s AND c.chunk_index > %s "
                    f"ORDER BY c.chunk_index ASC LIMIT %s",
                    (doc_id, idx, after),
                )
                out.extend(dict(r) for r in next_rows)
        elif anchor["conversation_id"] is not None:
            convo_id = anchor["conversation_id"]
            msg_id = anchor["message_id"]
            idx = anchor["chunk_index"]
            if before > 0:
                prev_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.conversation_id = %s "
                    f"  AND (c.message_id < %s OR (c.message_id = %s AND c.chunk_index < %s)) "
                    f"ORDER BY c.message_id DESC, c.chunk_index DESC LIMIT %s",
                    (convo_id, msg_id, msg_id, idx, before),
                )
                out.extend(reversed([dict(r) for r in prev_rows]))
            if after > 0:
                next_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.conversation_id = %s "
                    f"  AND (c.message_id > %s OR (c.message_id = %s AND c.chunk_index > %s)) "
                    f"ORDER BY c.message_id ASC, c.chunk_index ASC LIMIT %s",
                    (convo_id, msg_id, msg_id, idx, after),
                )
                out.extend(dict(r) for r in next_rows)
        return out

    def get_document_chunks(self, document_id: int) -> "list[dict]":
        """Return every chunk of a document ordered by ``chunk_index``."""
        rows = self._execute(
            """
            SELECT c.id, c.document_id, c.conversation_id, c.message_id,
                   c.chunk_index, c.text, c.heading, c.role, c.token_count,
                   c.metadata, c.content_hash,
                   d.dataset_id, d.source_uri, d.title
            FROM corpus.chunks c
            LEFT JOIN corpus.documents d ON d.id = c.document_id
            WHERE c.document_id = %s
            ORDER BY c.chunk_index ASC
            """,
            (document_id,),
        )
        return [dict(r) for r in rows]

    def replace_document_chunks(
        self,
        document_id: int,
        chunks: "list[TextChunk]",
        embedder_ids: list[int] | None = None,
    ) -> int:
        """Replace the chunks of a document with the given list, content-hash-aware.

        Phase F (F-04): used by the ``rechunk`` CLI. Mirrors the
        ``content_hash`` chunk-reuse path inside :meth:`upsert_document`
        WITHOUT touching the document row (no text/title/metadata
        changes — only the chunk decomposition changes). Embedding
        rows for chunks whose ``content_hash`` survives the rechunk
        are preserved in-place (Phase C BUG-3).

        Returns the count of chunks now attached to the document.
        """
        norm_chunks: list[TextChunk] = [_coerce_to_textchunk(c) for c in chunks]

        prior_rows = self._execute(
            "SELECT id, chunk_index, content_hash, heading"
            " FROM corpus.chunks WHERE document_id = %s ORDER BY chunk_index",
            (document_id,),
        )
        prior_by_hash: dict[str, int] = {}
        for pr in prior_rows:
            if pr["content_hash"]:
                prior_by_hash.setdefault(pr["content_hash"], pr["id"])

        new_chunk_hashes = {chunk_content_hash(c.text) for c in norm_chunks}

        reusable: dict[str, int] = {}
        for ph, pid in prior_by_hash.items():
            if ph in new_chunk_hashes:
                reusable[ph] = pid

        # Delete prior chunks that will NOT be reused.
        for pr in prior_rows:
            if pr["content_hash"] not in new_chunk_hashes:
                self._execute("DELETE FROM corpus.chunks WHERE id = %s", (pr["id"],))

        used_prior_ids: set[int] = set()
        cache: dict[tuple[str, int], int] = {}

        for i, chunk in enumerate(norm_chunks):
            chunk_hash = chunk_content_hash(chunk.text)
            meta = psycopg.types.json.Json(chunk.metadata or {})
            prior_id = reusable.get(chunk_hash)
            if prior_id is not None and prior_id not in used_prior_ids:
                self._execute(
                    """
                    UPDATE corpus.chunks
                    SET chunk_index = %s, heading = %s, text = %s,
                        metadata = %s, role = %s, token_count = %s
                    WHERE id = %s
                    """,
                    (
                        i,
                        chunk.heading,
                        chunk.text,
                        meta,
                        chunk.role,
                        chunk.token_count,
                        prior_id,
                    ),
                )
                used_prior_ids.add(prior_id)
            else:
                row = self._execute(
                    """
                    INSERT INTO corpus.chunks
                    (document_id, chunk_index, heading, text, metadata,
                     role, token_count, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        document_id,
                        i,
                        chunk.heading,
                        chunk.text,
                        meta,
                        chunk.role,
                        chunk.token_count,
                        chunk_hash,
                    ),
                )
                if embedder_ids is not None:
                    self._copy_reusable_embeddings(row[0]["id"], chunk_hash, embedder_ids, cache)

        return len(norm_chunks)

    def get_document_chunk_texts(self, document_id: int) -> "list[str]":
        """Return the texts of all chunks attached to ``document_id`` in order.

        Phase F (F-04): the ``rechunk`` CLI uses this to compare the
        prospective new chunk-text list against the stored chunk-text list
        and skip the upsert when they match — the pragmatic idempotency
        check called out in the planning doc.

        Returns ``[]`` when the document has no chunks (or doesn't exist).
        """
        rows = self._execute(
            "SELECT text FROM corpus.chunks WHERE document_id = %s ORDER BY chunk_index",
            (document_id,),
        )
        return [r["text"] for r in rows]

    def get_document_chunk_metadatas(self, document_id: int) -> "list[dict]":
        """Return the metadata dicts of all chunks attached to ``document_id``.

        Phase F (F-04): the ``rechunk`` CLI uses this to detect when the
        document's existing chunks lack the expected chunker signature
        (e.g. ``cdc_fingerprint`` for prose classes), in which case the
        rechunk must run even if the chunk text happens to match.
        """
        rows = self._execute(
            "SELECT metadata FROM corpus.chunks WHERE document_id = %s ORDER BY chunk_index",
            (document_id,),
        )
        out: list[dict] = []
        for r in rows:
            md = r["metadata"]
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except (TypeError, ValueError):
                    md = {}
            out.append(md if isinstance(md, dict) else {})
        return out

    def get_chunk_by_content_hash(self, content_hash: str) -> "dict | None":
        """Return the chunk row with the given ``content_hash``, joined to its document.

        Phase R5-01: protocol-lifted from the ad-hoc SQL shim in
        ``corpus_forge/eval/runner.py``.  Mirrors the join surface of
        :meth:`get_chunk` so consumers receive the same dict shape.

        Tiebreak: when multiple chunks share the same content_hash the row
        with the LOWEST ``id`` is returned (``ORDER BY c.id ASC LIMIT 1``).
        This stability is part of the protocol contract — callers rely on it
        for reproducible drift resolution across runs.

        Non-string / unmatched inputs (e.g. ``""``, ``None``) return ``None``
        rather than raising; psycopg's parameter binding either rejects with
        a typed error (which we let propagate to the caller's own try/except)
        or simply fails to match.
        """
        rows = self._execute(
            """
            SELECT c.id, c.document_id, c.conversation_id, c.message_id,
                   c.chunk_index, c.text, c.heading, c.role, c.token_count,
                   c.metadata, c.content_hash,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title
            FROM corpus.chunks c
            LEFT JOIN corpus.documents d ON d.id = c.document_id
            LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id
            WHERE c.content_hash = %s
            ORDER BY c.id ASC
            LIMIT 1
            """,
            (content_hash,),
        )
        return rows[0] if rows else None

    def list_datasets(self) -> "list[dict]":
        """Return all datasets with document + chunk counts (text + chat)."""
        rows = self._execute(
            """
            SELECT d.name, d.kind, d.description,
                   COALESCE(doc_counts.n, 0) AS document_count,
                   COALESCE(doc_counts.c, 0) + COALESCE(conv_counts.c, 0) AS chunk_count
            FROM corpus.datasets d
            LEFT JOIN (
                SELECT doc.dataset_id AS dataset_id,
                       COUNT(DISTINCT doc.id) AS n,
                       COUNT(c.id) AS c
                FROM corpus.documents doc
                LEFT JOIN corpus.chunks c ON c.document_id = doc.id
                GROUP BY doc.dataset_id
            ) doc_counts ON doc_counts.dataset_id = d.id
            LEFT JOIN (
                SELECT cv.dataset_id AS dataset_id,
                       COUNT(c.id) AS c
                FROM corpus.conversations cv
                LEFT JOIN corpus.chunks c ON c.conversation_id = cv.id
                GROUP BY cv.dataset_id
            ) conv_counts ON conv_counts.dataset_id = d.id
            ORDER BY d.name
            """,
        )
        return rows

    def backfill_lexical_index(self) -> int:
        """No-op for Postgres.

        The 004 migration adds ``text_tsv`` as a ``GENERATED ALWAYS AS …
        STORED`` column, which is auto-populated for all existing rows on
        ``ADD COLUMN``.  Returns ``0`` to satisfy the protocol contract.
        """
        return 0

    # ── F-02 write helpers ────────────────────────────────────────────────────

    def get_entity_metadata(self, entity_type: str, entity_id: int) -> dict:
        """Return the current metadata dict for a document or conversation entity.

        Uses the backend's native execute path (%s placeholders) so psycopg
        does not raise a placeholder-mismatch error.  Returns {} when the
        entity is not found or has NULL metadata.
        """
        table = _ENTITY_TABLE_MAP[entity_type]
        rows = self._execute(f"SELECT metadata FROM {table} WHERE id = %s", (entity_id,))
        if not rows or rows[0]["metadata"] is None:
            return {}
        raw = rows[0]["metadata"]
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)

    def get_entity_description(self, entity_type: str, entity_id: int) -> "str | None":
        """Return the current description for a document or conversation entity.

        Uses the backend's native execute path (%s placeholders).  Returns
        None when the entity is not found or has NULL description.
        """
        table = _ENTITY_TABLE_MAP[entity_type]
        rows = self._execute(f"SELECT description FROM {table} WHERE id = %s", (entity_id,))
        if not rows:
            return None
        return rows[0]["description"]

    def count_messages(self, conversation_id: int) -> int:
        """Return the current message count for a conversation.

        Uses MAX(turn_index)+1 which equals the message count when turn
        indices are 0-based and contiguous.  Returns 0 for an empty
        conversation.
        """
        rows = self._execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS m FROM corpus.messages"
            " WHERE conversation_id = %s",
            (conversation_id,),
        )
        return int(rows[0]["m"]) + 1

    def apply_label(
        self,
        entity_type: str,
        entity_id: int,
        namespace: str,
        value: str,
        *,
        confidence: float | None = None,
        source: str = "user",
    ) -> tuple[int, bool]:
        """Upsert a label and attach it to an entity via the junction table.

        Returns ``(label_id, created)`` where ``created`` is ``True`` on the
        first application of this (namespace, value) pair to this entity.
        """
        if entity_type not in _LABEL_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {entity_type!r} is not valid for labels; "
                f"must be one of {_LABEL_ENTITY_TYPES}"
            )

        junction_table, fk_col = _LABEL_TABLE_MAP[entity_type]

        # Upsert the canonical label row.
        self._execute(
            "INSERT INTO corpus.labels (namespace, value) VALUES (%s, %s)"
            " ON CONFLICT (namespace, value) DO NOTHING",
            (namespace, value),
        )
        label_rows = self._execute(
            "SELECT id FROM corpus.labels WHERE namespace = %s AND value = %s",
            (namespace, value),
        )
        label_id: int = label_rows[0]["id"]

        # Check whether the junction row already exists.
        existing = self._execute(
            f"SELECT 1 FROM {junction_table} WHERE {fk_col} = %s AND label_id = %s AND source = %s",
            (entity_id, label_id, source),
        )
        created = len(existing) == 0

        if created:
            if entity_type in ("chunk", "document"):
                # Phase E (C-04 + C-06): ``document_labels`` gained a
                # nullable ``confidence`` column to mirror the existing
                # ``chunk_labels.confidence``. The same INSERT shape now
                # serves both entities; ``conversation_labels`` keeps
                # the no-confidence shape since classifier output is
                # document-scoped at P0.
                self._execute(
                    f"INSERT INTO {junction_table} ({fk_col}, label_id, confidence, source)"
                    f" VALUES (%s, %s, %s, %s)"
                    f" ON CONFLICT DO NOTHING",
                    (entity_id, label_id, confidence, source),
                )
            else:
                self._execute(
                    f"INSERT INTO {junction_table} ({fk_col}, label_id, source)"
                    f" VALUES (%s, %s, %s)"
                    f" ON CONFLICT DO NOTHING",
                    (entity_id, label_id, source),
                )

        return label_id, created

    def revoke_label(
        self,
        entity_type: str,
        entity_id: int,
        namespace: str,
        value: str,
    ) -> bool:
        """Remove all junction rows for this entity / label pair.

        Returns ``True`` if at least one row was deleted, ``False`` if the
        label was not applied (idempotent).
        """
        if entity_type not in _LABEL_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {entity_type!r} is not valid for labels; "
                f"must be one of {_LABEL_ENTITY_TYPES}"
            )

        junction_table, fk_col = _LABEL_TABLE_MAP[entity_type]

        label_rows = self._execute(
            "SELECT id FROM corpus.labels WHERE namespace = %s AND value = %s",
            (namespace, value),
        )
        if not label_rows:
            return False

        label_id = label_rows[0]["id"]
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {junction_table} WHERE {fk_col} = %s AND label_id = %s",  # pyrefly: ignore[bad-argument-type]  # f-string with whitelisted table/column names; no user input
                (entity_id, label_id),
            )
            deleted = (cur.rowcount or 0) > 0
            conn.commit()

        return deleted

    def patch_metadata(
        self,
        entity_type: str,
        entity_id: int,
        key: str,
        value: Any,
    ) -> tuple[dict, dict]:
        """Merge a single ``key: value`` pair into the entity's JSONB metadata.

        Returns ``(before, after)`` as Python dicts.  Uses PG JSONB merge
        operator (``metadata || jsonb_build_object(key, value::jsonb)``).
        PG semantics: NULL metadata stays NULL after merge; we default to {}
        to match SQLite semantics (SQLite always has '{}' as default).
        """
        if entity_type not in _LABEL_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {entity_type!r} not valid; must be one of {_LABEL_ENTITY_TYPES}"
            )

        table = _ENTITY_TABLE_MAP[entity_type]

        rows = self._execute(f"SELECT metadata FROM {table} WHERE id = %s", (entity_id,))
        before: dict = rows[0]["metadata"] if rows and rows[0]["metadata"] else {}

        value_json = json.dumps(value)
        self._execute(
            f"UPDATE {table}"
            f" SET metadata = COALESCE(metadata, '{{}}'::jsonb)"
            f"   || jsonb_build_object(%s::text, %s::jsonb)"
            f" WHERE id = %s",
            (key, value_json, entity_id),
        )

        after_rows = self._execute(f"SELECT metadata FROM {table} WHERE id = %s", (entity_id,))
        after: dict = after_rows[0]["metadata"] if after_rows and after_rows[0]["metadata"] else {}

        return before, after

    def set_description(
        self,
        entity_type: str,
        entity_id: int,
        text: str | None,
    ) -> tuple[str | None, str | None]:
        """Set (or clear) the ``description`` column on an entity row.

        Returns ``(before, after)`` where each is either a string or ``None``.
        """
        if entity_type not in _LABEL_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {entity_type!r} not valid; must be one of {_LABEL_ENTITY_TYPES}"
            )

        table = _ENTITY_TABLE_MAP[entity_type]

        rows = self._execute(f"SELECT description FROM {table} WHERE id = %s", (entity_id,))
        before: str | None = rows[0]["description"] if rows else None

        self._execute(f"UPDATE {table} SET description = %s WHERE id = %s", (text, entity_id))

        return before, text

    def append_conversation(
        self,
        dataset_id: int,
        title: str,
        started_at: "datetime | None",
        messages: list[dict],
        metadata: dict | None = None,
        labels: list[tuple[str, str]] | None = None,
    ) -> tuple[int, int]:
        """Insert a new conversation row with its messages and per-message chunks.

        Returns ``(conversation_id, message_count)``.  Chunks are inserted so
        that ``search_lexical`` (which queries the ``text_tsv`` GIN index on
        ``corpus.chunks``) can find appended content immediately.
        """
        from ..identity import chunk_content_hash as _chunk_hash  # noqa: PLC0415

        source_uri = f"append://{uuid.uuid4()}"
        content_hash = source_uri  # unique per call
        meta_json = psycopg.types.json.Json(metadata or {})

        result = self._execute(
            """
            INSERT INTO corpus.conversations
              (dataset_id, source_uri, content_hash, title, started_at,
               message_count, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                dataset_id,
                source_uri,
                content_hash,
                title,
                started_at,
                len(messages),
                meta_json,
            ),
        )
        conv_id: int = result[0]["id"]

        # Insert messages and collect their ids for chunk linkage.
        message_ids: list[int] = []
        for i, msg in enumerate(messages):
            tool_calls = msg.get("tool_calls")
            tool_results = msg.get("tool_results")
            ts_val = msg.get("ts")
            msg_meta = msg.get("metadata", {})
            msg_result = self._execute(
                """
                INSERT INTO corpus.messages
                  (conversation_id, turn_index, role, content,
                   tool_calls, tool_results, ts, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    conv_id,
                    i,
                    msg["role"],
                    msg["content"],
                    psycopg.types.json.Json(tool_calls) if tool_calls is not None else None,
                    psycopg.types.json.Json(tool_results) if tool_results is not None else None,
                    ts_val,
                    psycopg.types.json.Json(msg_meta),
                ),
            )
            message_ids.append(msg_result[0]["id"])

        # Insert one chunk per non-empty message (mirrors the per_message daemon path).
        for i, msg in enumerate(messages):
            text = msg.get("content", "")
            if not text.strip():
                continue
            role = msg.get("role", "")
            ch = _chunk_hash(text)
            self._execute(
                """
                INSERT INTO corpus.chunks
                  (conversation_id, message_id, chunk_index, text,
                   role, metadata, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    conv_id,
                    message_ids[i],
                    0,
                    text,
                    role,
                    psycopg.types.json.Json({}),
                    ch,
                ),
            )

        if labels:
            for ns, val in labels:
                self.apply_label("conversation", conv_id, ns, val)

        return conv_id, len(messages)

    def append_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        *,
        tool_calls: list | None = None,
        tool_results: list | None = None,
        ts: "datetime | None" = None,
        metadata: dict | None = None,
    ) -> tuple[int, int]:
        """Append a single message to an existing conversation.

        Uses ``SELECT MAX(turn_index) + 1 ... FOR UPDATE`` to serialise
        concurrent appends within Postgres.  Also inserts a chunk row so the
        message is immediately searchable via the ``text_tsv`` GIN index.

        Returns ``(message_id, turn_index)``.
        """
        from ..identity import chunk_content_hash as _chunk_hash  # noqa: PLC0415

        with self._get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            # Lock the conversation row to serialise concurrent appends, then
            # compute the next turn_index in a separate aggregate query.  PG
            # does not permit FOR UPDATE with aggregate functions.
            cur.execute(
                "SELECT id FROM corpus.conversations WHERE id = %s FOR UPDATE",
                (conversation_id,),
            )
            cur.execute(
                "SELECT COALESCE(MAX(turn_index), -1) AS m FROM corpus.messages"
                " WHERE conversation_id = %s",
                (conversation_id,),
            )
            max_row = cur.fetchone()
            turn_index: int = int(max_row["m"]) + 1  # type: ignore[index]

            cur.execute(
                """
                INSERT INTO corpus.messages
                  (conversation_id, turn_index, role, content,
                   tool_calls, tool_results, ts, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    conversation_id,
                    turn_index,
                    role,
                    content,
                    psycopg.types.json.Json(tool_calls) if tool_calls is not None else None,
                    psycopg.types.json.Json(tool_results) if tool_results is not None else None,
                    ts,
                    psycopg.types.json.Json(metadata or {}),
                ),
            )
            row = cur.fetchone()
            message_id: int = row["id"]  # type: ignore[index]

            # Insert a chunk so the message is indexed by search_lexical.
            if content.strip():
                ch = _chunk_hash(content)
                cur.execute(
                    """
                    INSERT INTO corpus.chunks
                      (conversation_id, message_id, chunk_index, text,
                       role, metadata, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        conversation_id,
                        message_id,
                        0,
                        content,
                        role,
                        psycopg.types.json.Json({}),
                        ch,
                    ),
                )

            conn.commit()

        return message_id, turn_index

    def add_feedback(
        self,
        entity_type: str,
        entity_id: int,
        kind: str,
        *,
        rating: int | None = None,
        text: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Insert a feedback row and return its ``id``."""
        if entity_type not in _FEEDBACK_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {entity_type!r} not valid; must be one of {_FEEDBACK_ENTITY_TYPES}"
            )

        host = socket.gethostname()
        result = self._execute(
            """
            INSERT INTO corpus.feedback
              (host, entity_type, entity_id, kind, rating, text, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                host,
                entity_type,
                entity_id,
                kind,
                rating,
                text,
                psycopg.types.json.Json(metadata if metadata is not None else {}),
            ),
        )
        return result[0]["id"]

    def audit_event(
        self,
        host: str,
        client: str | None,
        session_id: str | None,
        tool: str,
        entity_type: str,
        entity_id: int,
        before: Any,
        after: Any,
        dry_run: bool,
    ) -> int:
        """Insert an MCP audit log row and return its ``id``.

        ``before`` and ``after`` are stored as JSONB (NULL when None).
        """
        result = self._execute(
            """
            INSERT INTO corpus.mcp_audit
              (host, client, session_id, tool, entity_type, entity_id,
               before, after, dry_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                host,
                client,
                session_id,
                tool,
                entity_type,
                entity_id,
                psycopg.types.json.Json(before) if before is not None else None,
                psycopg.types.json.Json(after) if after is not None else None,
                dry_run,
            ),
        )
        return result[0]["id"]

    def list_labels(
        self,
        *,
        entity_type: str | None = None,
        namespace: str | None = None,
    ) -> dict:
        """Return applied labels with optional filters.

        Returns ``{"labels": [{"entity_type": str, "namespace": str,
        "value": str, "count": int}, ...]}``.

        When ``entity_type`` is given, only labels applied to that entity type
        are returned.  When ``namespace`` is given, only labels in that
        namespace are returned.  Both filters may be combined.
        """
        parts: list[str] = []
        params: list[object] = []

        for et, (jt, _fk) in _LABEL_TABLE_MAP.items():
            if entity_type is not None and et != entity_type:
                continue
            parts.append(
                f"SELECT '{et}' AS entity_type, l.namespace, l.value, COUNT(*) AS count"
                f" FROM {jt} j JOIN corpus.labels l ON l.id = j.label_id"
                + (" WHERE l.namespace = %s" if namespace is not None else "")
                + " GROUP BY l.namespace, l.value"
            )
            if namespace is not None:
                params.append(namespace)

        if not parts:
            return {"labels": []}

        union_sql = " UNION ALL ".join(parts)
        rows = self._execute(union_sql, tuple(params))
        labels = [
            {
                "entity_type": r["entity_type"],
                "namespace": r["namespace"],
                "value": r["value"],
                "count": r["count"],
            }
            for r in rows
        ]
        return {"labels": labels}

    def hydrate_hit_metadata(self, hits: "list[Hit]") -> list[dict]:
        """Bulk-load labels, description, and recent_feedback for a list of hits.

        Returns a new list of dicts that include all Hit fields plus three
        enrichment keys: ``labels``, ``description``, and ``recent_feedback``.
        ``Hit`` is frozen=True and has a pinned field set, so we return dicts
        rather than new Hit objects.  The F-02 contract explicitly allows either
        form; callers use ``hasattr``/``isinstance`` guards to handle both.

        No N+1: each field is fetched with a single query regardless of hit count.
        """
        import dataclasses  # noqa: PLC0415

        if not hits:
            return []

        chunk_ids = [h.chunk_id for h in hits]

        # --- bulk-fetch labels ---
        label_rows = self._execute(
            """
            SELECT cl.chunk_id, l.namespace, l.value
            FROM corpus.chunk_labels cl
            JOIN corpus.labels l ON l.id = cl.label_id
            WHERE cl.chunk_id = ANY(%s)
            """,
            (chunk_ids,),
        )
        labels_by_chunk: dict[int, list[tuple[str, str]]] = {cid: [] for cid in chunk_ids}
        for lr in label_rows:
            labels_by_chunk[lr["chunk_id"]].append((lr["namespace"], lr["value"]))

        # --- bulk-fetch descriptions ---
        desc_rows = self._execute(
            "SELECT id, description FROM corpus.chunks WHERE id = ANY(%s)",
            (chunk_ids,),
        )
        desc_by_chunk: dict[int, str | None] = dict.fromkeys(chunk_ids)
        for dr in desc_rows:
            desc_by_chunk[dr["id"]] = dr["description"]

        # --- bulk-fetch up to _RECENT_FEEDBACK_LIMIT most-recent feedback per chunk ---
        feedback_rows = self._execute(
            """
            SELECT entity_id, kind, rating, text, ts
            FROM corpus.feedback
            WHERE entity_type = 'chunk' AND entity_id = ANY(%s)
            ORDER BY entity_id, id DESC
            """,
            (chunk_ids,),
        )
        feedback_by_chunk: dict[int, list[dict]] = {cid: [] for cid in chunk_ids}
        for fr in feedback_rows:
            eid = fr["entity_id"]
            if len(feedback_by_chunk[eid]) < _RECENT_FEEDBACK_LIMIT:
                feedback_by_chunk[eid].append(
                    {
                        "kind": fr["kind"],
                        "rating": fr["rating"],
                        "text": fr["text"],
                        "ts": fr["ts"],
                    }
                )

        # Return dicts: Hit fields + 3 enrichment keys.
        result: list[dict] = []
        for hit in hits:
            hit_dict = dataclasses.asdict(hit)
            hit_dict["labels"] = labels_by_chunk.get(hit.chunk_id, [])
            hit_dict["description"] = desc_by_chunk.get(hit.chunk_id)
            hit_dict["recent_feedback"] = feedback_by_chunk.get(hit.chunk_id, [])
            result.append(hit_dict)

        return result

    # ── G-02 chat-template helpers ────────────────────────────────────────────

    def register_chat_template(
        self,
        name: str,
        source: str,
        *,
        jinja: str | None = None,
        model_id: str | None = None,
        description: str | None = None,
        host: str,
    ) -> tuple[int, bool]:
        """Upsert a chat_templates row and return (template_id, created).

        Uses ON CONFLICT(name) DO NOTHING.  Returns ``created=True`` when a
        new row was inserted, ``False`` when the name already existed.
        """
        existing = self._execute("SELECT id FROM corpus.chat_templates WHERE name = %s", (name,))
        if existing:
            return int(existing[0]["id"]), False

        result = self._execute(
            """
            INSERT INTO corpus.chat_templates
                (name, source, jinja, model_id, description, host)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(name) DO NOTHING
            RETURNING id
            """,
            (name, source, jinja, model_id, description, host),
        )
        if result:
            return int(result[0]["id"]), True

        # Race condition: another writer inserted between our SELECT and INSERT.
        row = self._execute("SELECT id FROM corpus.chat_templates WHERE name = %s", (name,))
        return int(row[0]["id"]), False

    def list_chat_templates(self) -> list[dict]:
        """Return all rows from corpus.chat_templates as a list of dicts."""
        return self._execute("SELECT * FROM corpus.chat_templates ORDER BY id")

    def get_chat_template_by_name(self, name: str) -> dict | None:
        """Return the corpus.chat_templates row for *name*, or None if absent."""
        rows = self._execute("SELECT * FROM corpus.chat_templates WHERE name = %s LIMIT 1", (name,))
        return rows[0] if rows else None

    # ── G-03 conversation helpers ─────────────────────────────────────────────

    def get_conversation(self, conversation_id: int) -> "dict | None":
        """Return the corpus.conversations row for *conversation_id*, or None."""
        rows = self._execute(
            "SELECT * FROM corpus.conversations WHERE id = %s LIMIT 1",
            (conversation_id,),
        )
        return rows[0] if rows else None

    def list_conversations_for_dataset(self, dataset_id: int) -> "list[dict]":
        """Return all conversations for *dataset_id* as a list of dicts."""
        return self._execute(
            "SELECT * FROM corpus.conversations WHERE dataset_id = %s ORDER BY id",
            (dataset_id,),
        )

    def list_conversation_messages(self, conversation_id: int) -> "list[dict]":
        """Return all messages for *conversation_id* ordered by turn_index.

        Each dict has at minimum: id, conversation_id, turn_index, role, content.
        """
        return self._execute(
            "SELECT * FROM corpus.messages WHERE conversation_id = %s ORDER BY turn_index",
            (conversation_id,),
        )

    # ── H-02 feedback-session helpers ─────────────────────────────────────────

    def upsert_feedback_session(
        self,
        client: str,
        session_id: str,
        host: str,
        started_at: "datetime | str",
    ) -> int:
        """Insert a feedback_sessions row if (client, session_id) is new.

        Uses ON CONFLICT(client, session_id) DO NOTHING so a duplicate key is
        silently skipped.  Returns the id of the existing or newly-created row.
        """
        started_at_str = str(started_at)
        self._execute(
            """
            INSERT INTO corpus.feedback_sessions
              (client, session_id, host, started_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (client, session_id) DO NOTHING
            """,
            (client, session_id, host, started_at_str),
        )
        rows = self._execute(
            "SELECT id FROM corpus.feedback_sessions WHERE client = %s AND session_id = %s",
            (client, session_id),
        )
        return int(rows[0]["id"])

    def append_feedback_event(
        self,
        feedback_session_id: int,
        *,
        audit_id: "int | None" = None,
        feedback_id: "int | None" = None,
        entity_type: str,
        entity_id: int,
    ) -> int:
        """Insert a feedback_events row and return its id.

        At least one of audit_id or feedback_id must be non-None.
        """
        if audit_id is None and feedback_id is None:
            raise ValueError(
                "append_feedback_event requires at least one of audit_id or feedback_id to be set"
            )
        result = self._execute(
            """
            INSERT INTO corpus.feedback_events
              (feedback_session_id, audit_id, feedback_id, entity_type, entity_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (feedback_session_id, audit_id, feedback_id, entity_type, entity_id),
        )
        return int(result[0]["id"])

    def end_feedback_session(self, client: str, session_id: str) -> bool:
        """Set ended_at on the matching open session row.

        Returns True if a row was updated, False if no open row was found.
        """
        ended_at = datetime.now(UTC)
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE corpus.feedback_sessions
                SET ended_at = %s
                WHERE client = %s AND session_id = %s AND ended_at IS NULL
                """,  # pyrefly: ignore[bad-argument-type]
                (ended_at, client, session_id),
            )
            updated = (cur.rowcount or 0) > 0
            conn.commit()
        return updated

    def link_feedback_session_to_conversation(
        self, client: str, session_id: str, conversation_id: int
    ) -> bool:
        """Set feedback_sessions.conversation_id if currently NULL.

        Returns True if a row was updated, False if no matching row or already linked.
        """
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE corpus.feedback_sessions
                SET conversation_id = %s
                WHERE client = %s AND session_id = %s AND conversation_id IS NULL
                """,
                (conversation_id, client, session_id),
            )
            updated = (cur.rowcount or 0) > 0
            conn.commit()
        return updated

    def get_feedback_session_by_key(self, client: str, session_id: str) -> "dict | None":
        """Return the corpus.feedback_sessions row for (client, session_id), or None."""
        rows = self._execute(
            "SELECT * FROM corpus.feedback_sessions WHERE client = %s AND session_id = %s LIMIT 1",
            (client, session_id),
        )
        return rows[0] if rows else None

    # ── H-04 feedback-export helpers ──────────────────────────────────────────

    def list_feedback_events_for_dataset(self, dataset_id: int) -> "list[dict]":
        """Return feedback_events joined to linked feedback_sessions for *dataset_id*.

        Only events whose session has conversation_id IS NOT NULL and whose
        conversation belongs to *dataset_id* are included.  Events from
        unlinked sessions are silently skipped.

        Each row contains all feedback_events columns plus the feedback_session
        fields: client, session_id, host, and conversation_id.
        """
        return self._execute(
            """
            SELECT
                fe.id,
                fe.feedback_session_id,
                fe.audit_id,
                fe.feedback_id,
                fe.entity_type,
                fe.entity_id,
                fe.ts,
                fs.client,
                fs.session_id,
                fs.host,
                fs.conversation_id
            FROM corpus.feedback_events fe
            JOIN corpus.feedback_sessions fs ON fs.id = fe.feedback_session_id
            JOIN corpus.conversations c ON c.id = fs.conversation_id
            WHERE c.dataset_id = %s
              AND fs.conversation_id IS NOT NULL
            ORDER BY fe.id
            """,
            (dataset_id,),
        )

    def list_sdft_demonstrations(
        self,
        dataset_id: int,
        include_sources: "list[str] | None" = None,
    ) -> "list[dict]":
        """Return sdft_demonstrations rows for *dataset_id*.

        Postgres JSONB columns (student_messages, teacher_messages) are
        returned as native Python objects by psycopg2, so no extra
        deserialization is needed.

        Args:
            dataset_id:      Dataset to filter on.
            include_sources: When non-None and non-empty, only rows whose
                             ``source`` value is in this list are returned.
                             ``None`` returns all rows.  An empty list
                             returns no rows.
        """
        if include_sources is not None and len(include_sources) == 0:
            return []

        if include_sources is not None:
            placeholders = ",".join(["%s"] * len(include_sources))
            return self._execute(
                f"SELECT id, query, student_messages, teacher_messages, target, source,"
                f" dataset_id, trace_id, content_hash"
                f" FROM corpus.sdft_demonstrations"
                f" WHERE dataset_id = %s AND source IN ({placeholders})"
                f" ORDER BY id",
                (dataset_id, *include_sources),
            )
        return self._execute(
            "SELECT id, query, student_messages, teacher_messages, target, source,"
            " dataset_id, trace_id, content_hash"
            " FROM corpus.sdft_demonstrations"
            " WHERE dataset_id = %s"
            " ORDER BY id",
            (dataset_id,),
        )

    def get_audit_event(self, audit_id: int) -> "dict | None":
        """Return the corpus.mcp_audit row for *audit_id*, or None on miss."""
        rows = self._execute(
            "SELECT * FROM corpus.mcp_audit WHERE id = %s LIMIT 1",
            (audit_id,),
        )
        return rows[0] if rows else None

    def get_feedback(self, feedback_id: int) -> "dict | None":
        """Return the corpus.feedback row for *feedback_id*, or None on miss."""
        rows = self._execute(
            "SELECT * FROM corpus.feedback WHERE id = %s LIMIT 1",
            (feedback_id,),
        )
        return rows[0] if rows else None

    def get_conversation_messages_up_to_ts(
        self, conversation_id: int, ts: "str | None"
    ) -> "list[dict]":
        """Return messages for *conversation_id* with message.ts <= *ts*.

        Messages are ordered by turn_index.  If *ts* is None or no messages
        fall within the window, all messages for the conversation are returned.
        """
        if ts is not None:
            rows = self._execute(
                """
                SELECT * FROM corpus.messages
                WHERE conversation_id = %s AND ts <= %s
                ORDER BY turn_index
                """,
                (conversation_id, ts),
            )
            if rows:
                return rows
        return self._execute(
            "SELECT * FROM corpus.messages WHERE conversation_id = %s ORDER BY turn_index",
            (conversation_id,),
        )

    # ── Classification surface (Phase E / C-06) ───────────────────────────

    def iter_documents_for_classification(
        self,
        dataset_id: "int | None" = None,
        *,
        include_classified: bool = False,
    ) -> "Iterator[ClassifiableDocument]":
        """Yield :class:`ClassifiableDocument` rows for the classifier chain.

        See :meth:`StorageBackend.iter_documents_for_classification` for
        the contract. Implementation joins ``documents`` to
        ``document_labels`` + ``labels`` so the classifier sees the
        already-attached structural labels.

        The classifier-skip filter uses ``NOT EXISTS`` against a
        subquery selecting any ``class``-namespace label with
        ``source LIKE 'classifier:%'``.
        """
        from corpus_forge.classifiers.base import (  # noqa: PLC0415
            ClassifiableDocument,
        )

        params: tuple[Any, ...]
        where_clauses: list[str] = []
        if dataset_id is not None:
            where_clauses.append("d.dataset_id = %s")
            params = (dataset_id,)
        else:
            params = ()

        if not include_classified:
            where_clauses.append(
                "NOT EXISTS ("
                "  SELECT 1 FROM corpus.document_labels dl2"
                "  JOIN corpus.labels l2 ON l2.id = dl2.label_id"
                "  WHERE dl2.document_id = d.id"
                "    AND l2.namespace = 'class'"
                "    AND dl2.source LIKE 'classifier:%%'"
                ")"
            )

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        doc_rows = self._execute(
            f"""
            SELECT d.id AS document_id, d.source_uri, d.title, d.text, d.metadata
            FROM corpus.documents d
            {where_sql}
            ORDER BY d.id
            """,
            params,
        )
        if not doc_rows:
            return

        # Bulk-fetch labels in a single query — N+1 guard.
        ids = [int(r["document_id"]) for r in doc_rows]
        placeholders = ",".join(["%s"] * len(ids))
        label_rows = self._execute(
            f"""
            SELECT dl.document_id, l.namespace, l.value
            FROM corpus.document_labels dl
            JOIN corpus.labels l ON l.id = dl.label_id
            WHERE dl.document_id IN ({placeholders})
            """,
            tuple(ids),
        )
        labels_by_doc: dict[int, list[tuple[str, str]]] = {i: [] for i in ids}
        for lr in label_rows:
            labels_by_doc[int(lr["document_id"])].append((lr["namespace"], lr["value"]))

        for row in doc_rows:
            doc_id = int(row["document_id"])
            md = row["metadata"] or {}
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except (TypeError, ValueError):
                    md = {}
            yield ClassifiableDocument(
                document_id=doc_id,
                source_uri=row["source_uri"],
                title=row.get("title"),
                text=row.get("text") or "",
                format_labels=labels_by_doc.get(doc_id, []),
                metadata=md if isinstance(md, dict) else {},
            )

    # ── Code-enrichment surface (Phase H) ─────────────────────────────────

    def iter_code_chunks_for_enrichment(
        self,
        model_tag: str,
        dataset_id: "int | None" = None,
    ) -> "Iterator[tuple[int, TextChunk, str]]":
        """Yield ``(chunk_id, TextChunk, language)`` for code chunks to enrich.

        Joins ``chunks`` → ``documents`` → ``document_labels`` →
        ``labels`` to filter on ``namespace='class' AND value='code'``.
        Idempotency is enforced in Python rather than SQL so the
        comparison reads ``model_tag`` from JSON without relying on
        Postgres-specific JSON-path syntax (keeps the SQLite parity
        simple).

        ``language`` resolution falls back from
        ``chunks.metadata.language`` to a ``namespace='language'``
        document-label to the literal ``"unknown"``.
        """
        params: tuple[Any, ...]
        ds_clause = ""
        if dataset_id is not None:
            ds_clause = "AND d.dataset_id = %s"
            params = (dataset_id,)
        else:
            params = ()

        # Pull every code-class chunk with its metadata + the parent doc's
        # language label (if any).
        rows = self._execute(
            f"""
            SELECT c.id AS chunk_id,
                   c.text,
                   c.heading,
                   c.role,
                   c.token_count,
                   c.metadata AS chunk_metadata,
                   (
                       SELECT l2.value
                       FROM corpus.document_labels dl2
                       JOIN corpus.labels l2 ON l2.id = dl2.label_id
                       WHERE dl2.document_id = d.id AND l2.namespace = 'language'
                       LIMIT 1
                   ) AS doc_language
            FROM corpus.chunks c
            JOIN corpus.documents d ON d.id = c.document_id
            JOIN corpus.document_labels dl ON dl.document_id = d.id
            JOIN corpus.labels l ON l.id = dl.label_id
            WHERE l.namespace = 'class' AND l.value = 'code'
              {ds_clause}
            ORDER BY c.id
            """,
            params,
        )

        for row in rows:
            md = row["chunk_metadata"] or {}
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except (TypeError, ValueError):
                    md = {}
            if not isinstance(md, dict):
                md = {}
            existing = md.get("enrichment") or {}
            if isinstance(existing, dict) and existing.get("model") == model_tag:
                # Idempotency: skip chunks already enriched with this model.
                continue

            chunk_language = md.get("language") or row.get("doc_language") or "unknown"
            chunk = TextChunk(
                text=row["text"] or "",
                heading=row.get("heading"),
                role=row.get("role"),
                token_count=row.get("token_count"),
                metadata=md,
            )
            yield int(row["chunk_id"]), chunk, str(chunk_language)

    def update_chunk_enrichment(
        self,
        chunk_id: int,
        enrichment: Any,
    ) -> None:
        """Merge ``enrichment.to_metadata()`` into ``chunks.metadata.enrichment``.

        Uses Postgres ``jsonb_set`` (full-path replace) so existing
        sibling keys (``kind``, ``name``, ``byte_range``, ``language``,
        ``cdc_fingerprint``) survive. ``COALESCE(metadata, '{}'::jsonb)``
        keeps the merge safe when ``chunks.metadata`` is NULL.
        """
        payload = (
            enrichment.to_metadata() if hasattr(enrichment, "to_metadata") else dict(enrichment)
        )
        self._execute(
            """
            UPDATE corpus.chunks
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{enrichment}',
                %s::jsonb,
                true
            )
            WHERE id = %s
            """,
            (json.dumps(payload), chunk_id),
        )

    # --- Ingest-run state (SR-G2) -------------------------------------------

    def start_ingest_run(
        self,
        *,
        run_id: str,
        host: str,
        pid: int,
        config_digest: str,
    ) -> None:
        """Insert a new ingest-run row with status='running'.

        On conflict (same run_id — resume path), flips status back to
        'running', clears ended_at, and bumps last_progress_at so the
        row is recycled rather than duplicated.
        """
        now = datetime.now(tz=UTC)
        self._execute(
            """
            INSERT INTO corpus.ingest_runs
                (run_id, host, pid, config_digest, status, started_at, last_progress_at)
            VALUES (%s, %s, %s, %s, 'running', %s, %s)
            ON CONFLICT (run_id) DO UPDATE
                SET status           = 'running',
                    ended_at         = NULL,
                    last_progress_at = EXCLUDED.last_progress_at,
                    host             = EXCLUDED.host,
                    pid              = EXCLUDED.pid,
                    config_digest    = EXCLUDED.config_digest
            """,
            (run_id, host, pid, config_digest, now, now),
        )

    def update_ingest_run(
        self,
        run_id: str,
        *,
        last_op: str | None = None,
        last_done: int | None = None,
        last_total: int | None = None,
    ) -> None:
        """Best-effort heartbeat update; swallows psycopg.OperationalError at DEBUG."""
        # Build a dynamic SET clause that only touches provided fields.
        now = datetime.now(tz=UTC)
        set_parts = ["last_progress_at = %s"]
        params: list[Any] = [now]
        if last_op is not None:
            set_parts.append("last_op = %s")
            params.append(last_op)
        if last_done is not None:
            set_parts.append("last_done = %s")
            params.append(last_done)
        if last_total is not None:
            set_parts.append("last_total = %s")
            params.append(last_total)
        params.append(run_id)
        try:
            self._execute(
                f"UPDATE corpus.ingest_runs SET {', '.join(set_parts)} WHERE run_id = %s",
                tuple(params),
            )
        except psycopg.OperationalError as exc:
            logger.debug("update_ingest_run swallowed OperationalError: %r", exc)

    def finish_ingest_run(
        self,
        run_id: str,
        *,
        status: "Literal['completed', 'interrupted', 'failed']",
        error: str | None = None,
    ) -> None:
        """Set ended_at, status, and optional error on the ingest-run row."""
        now = datetime.now(tz=UTC)
        self._execute(
            """
            UPDATE corpus.ingest_runs
            SET status   = %s,
                ended_at = %s,
                error    = %s
            WHERE run_id = %s
            """,
            (status, now, error, run_id),
        )

    def latest_ingest_run(self) -> dict | None:
        """Returns the row with the most-recent started_at (any status)."""
        rows = self._execute(
            """
            SELECT run_id, status, host, pid, config_digest,
                   started_at, ended_at, last_progress_at,
                   last_op, last_done, last_total, error
            FROM corpus.ingest_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        return dict(rows[0]) if rows else None

    def latest_unfinished_ingest_run(self, host: str | None = None) -> dict | None:
        """Returns the most-recent row with status IN ('running','interrupted'); None otherwise.

        host=None (default) returns any unfinished row regardless of host (back-compat).
        host='X' adds AND host = 'X' to the WHERE clause.
        """
        rows = self._execute(
            """
            SELECT run_id, status, host, pid, config_digest,
                   started_at, ended_at, last_progress_at,
                   last_op, last_done, last_total, error
            FROM corpus.ingest_runs
            WHERE status IN ('running', 'interrupted')
              AND (%s::text IS NULL OR host = %s)
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (host, host),
        )
        return dict(rows[0]) if rows else None

    def upsert_ingest_run_source(
        self,
        *,
        run_id: str,
        source_uri_prefix: str,
        dataset_id: int,
        last_scanned_at: datetime | None = None,
        docs_seen_delta: int = 0,
        docs_skipped_delta: int = 0,
        docs_failed_delta: int = 0,
        finished: bool = False,
    ) -> None:
        """UPSERT on (run_id, source_uri_prefix). Deltas ADD to existing counters.
        When finished=True, finished_at is set to NOW().
        """
        now = datetime.now(tz=UTC)
        finished_at_val = now if finished else None
        self._execute(
            """
            INSERT INTO corpus.ingest_run_sources AS t
                (run_id, source_uri_prefix, dataset_id,
                 last_scanned_at, docs_seen, docs_skipped, docs_failed, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, source_uri_prefix) DO UPDATE
                SET docs_seen       = t.docs_seen    + EXCLUDED.docs_seen,
                    docs_skipped    = t.docs_skipped + EXCLUDED.docs_skipped,
                    docs_failed     = t.docs_failed  + EXCLUDED.docs_failed,
                    last_scanned_at = COALESCE(EXCLUDED.last_scanned_at, t.last_scanned_at),
                    finished_at     = CASE WHEN EXCLUDED.finished_at IS NOT NULL
                                          THEN EXCLUDED.finished_at
                                          ELSE t.finished_at
                                     END
            """,
            (
                run_id,
                source_uri_prefix,
                dataset_id,
                last_scanned_at,
                docs_seen_delta,
                docs_skipped_delta,
                docs_failed_delta,
                finished_at_val,
            ),
        )

    def find_source_last_scanned_at(self, source_uri_prefix: str) -> datetime | None:
        """Latest finished_at across any completed/interrupted run for this source_uri_prefix.

        Joins to ingest_runs to filter by run status ('completed' or 'interrupted')
        so that still-running runs are excluded from the max calculation.
        Also excludes rows where finished_at IS NULL (source still in-progress within a run).
        Returns a UTC-aware datetime, or None if the source has never been scanned.

        Matches SQLite backend semantics per the binding contract in tasks.md §5.
        """
        rows = self._execute(
            """
            SELECT MAX(irs.finished_at) AS last_scanned_at
            FROM corpus.ingest_run_sources irs
            JOIN corpus.ingest_runs ir ON ir.run_id = irs.run_id
            WHERE irs.source_uri_prefix = %s
              AND ir.status IN ('completed', 'interrupted')
              AND irs.finished_at IS NOT NULL
            """,
            (source_uri_prefix,),
        )
        if not rows:
            return None
        ts = rows[0]["last_scanned_at"]
        if ts is None:
            return None
        # Ensure UTC-aware
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts

    def mark_stale_runs(
        self,
        threshold_seconds: float,
        *,
        host: str | None = None,
    ) -> int:
        """Transition stale 'running' rows to 'failed'.

        Short-circuits immediately (returns 0) when threshold_seconds <= 0.
        Wraps psycopg.OperationalError and returns 0 (best-effort idiom).
        """
        if threshold_seconds <= 0:
            return 0
        now = datetime.now(tz=UTC)
        try:
            rows = self._execute(
                """
                UPDATE corpus.ingest_runs
                SET status   = 'failed',
                    ended_at = %s,
                    error    = 'stale heartbeat: last progress > '
                               || ROUND(%s)::text
                               || 's ago; host '
                               || host
                               || '/pid '
                               || pid::text
                               || ' presumed dead'
                WHERE status = 'running'
                  AND last_progress_at < %s - make_interval(secs => %s)
                  AND (%s::text IS NULL OR host = %s)
                RETURNING run_id
                """,
                (now, threshold_seconds, now, threshold_seconds, host, host),
            )
            return len(rows)
        except psycopg.OperationalError as exc:
            logger.debug("mark_stale_runs swallowed OperationalError: %r", exc)
            return 0

    # -------------------------------------------------------------------------
    # Fleet telemetry registry (rfc-fleet-1)
    # -------------------------------------------------------------------------

    def upsert_host(
        self,
        *,
        host_id: str,
        hostname: str,
        os: str,
        accelerator: dict | None,
        tailscale_name: str | None = None,
    ) -> None:
        """UPSERT this host's row, bumping ``last_seen`` to now (rfc-fleet-1)."""
        now = datetime.now(tz=UTC)
        accelerator_json = json.dumps(accelerator) if accelerator is not None else None
        self._execute(
            """
            INSERT INTO corpus.hosts
                (host_id, hostname, os, accelerator, tailscale_name, last_seen)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (host_id) DO UPDATE
                SET hostname       = EXCLUDED.hostname,
                    os             = EXCLUDED.os,
                    accelerator    = EXCLUDED.accelerator,
                    tailscale_name = COALESCE(
                        EXCLUDED.tailscale_name, corpus.hosts.tailscale_name
                    ),
                    last_seen      = EXCLUDED.last_seen
            """,
            (host_id, hostname, os, accelerator_json, tailscale_name, now),
        )

    def upsert_models(self, rows: list[dict]) -> None:
        """Insert ``models`` rows, preserving ``first_seen`` (rfc-fleet-1)."""
        if not rows:
            return
        now = datetime.now(tz=UTC)
        for row in rows:
            self._execute(
                """
                INSERT INTO corpus.models
                    (model_key, kind, provider, model_id, dimension, first_seen)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_key) DO NOTHING
                """,
                (
                    row["model_key"],
                    row.get("kind"),
                    row.get("provider"),
                    row.get("model_id"),
                    row.get("dimension"),
                    now,
                ),
            )

    def insert_model_benchmark(
        self,
        *,
        host_id: str,
        model_key: str,
        source: str,
        transport: str,
        device: str,
        batch_size: int | None,
        sample_chunks: int | None,
        chunks_per_s: float | None,
        tokens_per_s: float | None = None,
        latency_p50_ms: float | None = None,
        latency_p95_ms: float | None = None,
    ) -> None:
        """Insert one ``model_benchmarks`` row, stamping ``measured_at`` (rfc-fleet-1)."""
        now = datetime.now(tz=UTC)
        self._execute(
            """
            INSERT INTO corpus.model_benchmarks
                (host_id, model_key, source, transport, device, batch_size,
                 sample_chunks, chunks_per_s, tokens_per_s, latency_p50_ms,
                 latency_p95_ms, measured_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                host_id,
                model_key,
                source,
                transport,
                device,
                batch_size,
                sample_chunks,
                chunks_per_s,
                tokens_per_s,
                latency_p50_ms,
                latency_p95_ms,
                now,
            ),
        )

    def list_models_with_latest_benchmark(self) -> list[dict]:
        """``models`` ⨝ latest benchmark per ``(host_id, model_key)`` (rfc-fleet-1).

        A ``ROW_NUMBER()`` window over the benchmarks picks the freshest
        row per ``(host_id, model_key)`` pair (the 0018 index on
        ``(host_id, model_key, measured_at DESC)`` serves the ORDER BY); a
        LEFT JOIN from ``models`` keeps never-benchmarked models in the
        output with NULL host/metric columns.
        """
        return self._execute(
            """
            WITH latest AS (
                SELECT
                    host_id, model_key, chunks_per_s, transport, device,
                    source, measured_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY host_id, model_key
                        ORDER BY measured_at DESC
                    ) AS rn
                FROM corpus.model_benchmarks
            )
            SELECT
                m.model_key, m.kind, m.provider, m.model_id, m.dimension,
                l.host_id, l.chunks_per_s, l.transport, l.device,
                l.source, l.measured_at
            FROM corpus.models m
            LEFT JOIN latest l
                ON l.model_key = m.model_key AND l.rn = 1
            ORDER BY m.model_key ASC, l.host_id ASC NULLS FIRST
            """
        )

    def list_hosts_with_latest_rate(self) -> list[dict]:
        """``hosts`` + each host's freshest aggregate ``chunks_per_s`` (rfc-fleet-1).

        ``latest`` ranks every host's benchmark rows by ``measured_at`` so
        ``rn = 1`` is the single freshest sample; ``counts`` tallies the
        distinct benchmarked models per host.  A LEFT JOIN from ``hosts``
        keeps hosts with no benchmarks (NULL aggregates).
        """
        return self._execute(
            """
            WITH latest AS (
                SELECT
                    host_id, chunks_per_s, measured_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY host_id ORDER BY measured_at DESC
                    ) AS rn
                FROM corpus.model_benchmarks
            ),
            counts AS (
                SELECT host_id, COUNT(DISTINCT model_key) AS models
                FROM corpus.model_benchmarks
                GROUP BY host_id
            )
            SELECT
                h.host_id, h.hostname, h.os, h.accelerator, h.last_seen,
                COALESCE(c.models, 0) AS models,
                l.chunks_per_s AS latest_chunks_per_s,
                l.measured_at AS latest_measured_at
            FROM corpus.hosts h
            LEFT JOIN latest l ON l.host_id = h.host_id AND l.rn = 1
            LEFT JOIN counts c ON c.host_id = h.host_id
            ORDER BY h.last_seen DESC NULLS LAST
            """
        )

    def model_benchmark_stats(self) -> dict:
        """Total ``model_benchmarks`` count + freshest ``measured_at`` (rfc-fleet-1)."""
        rows = self._execute(
            """
            SELECT COUNT(*) AS count, MAX(measured_at) AS freshest
            FROM corpus.model_benchmarks
            """
        )
        if not rows:
            return {"count": 0, "freshest": None}
        return {"count": int(rows[0].get("count") or 0), "freshest": rows[0].get("freshest")}
