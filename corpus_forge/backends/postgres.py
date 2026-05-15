"""PostgreSQL storage backend implementation for corpus-forge."""

import json
import logging
import os
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row

from ..chunkers.base import TextChunk
from ..identity import advisory_lock_key, chunk_content_hash
from .base import StorageBackend

if TYPE_CHECKING:
    from corpus_forge.sources.base import RawConversation, RawDocument

    from ..retrieval.types import Hit


# Width of the legacy ``(heading, text)`` chunk shape accepted by
# :func:`_coerce_to_textchunk`. Pulled to a module constant so the
# ``isinstance ... len(item) == _LEGACY_CHUNK_TUPLE_LEN`` check stays
# readable without a ruff ``PLR2004`` magic-number flag.
_LEGACY_CHUNK_TUPLE_LEN = 2


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


class PostgresBackend(StorageBackend):
    """PostgreSQL storage backend with pgvector support."""

    def __init__(self, dsn: str, schema: str = "corpus"):
        self.dsn = dsn
        self.schema = schema
        self._setup_connection()

    def _setup_connection(self):
        """Setup connection parameters."""
        # Convert environment variables in DSN
        expanded_dsn = os.path.expandvars(self.dsn)
        self.conn_params = {"dbname": expanded_dsn}
        # In a real implementation, we'd parse the DSN properly
        # For now, we'll assume it's a valid connection string

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        # In a real implementation, we'd use a connection pool
        # For now, we'll create a new connection each time
        conn = psycopg.connect(self.dsn)
        try:
            # Ensure the schema is visible for unqualified table names in DDL.
            # This must be set per-connection because SET search_path is session-scoped.
            conn.execute(
                pgsql.SQL("SET search_path = {schema}, public").format(
                    schema=pgsql.Identifier(self.schema)
                )
            )
            conn.commit()
            yield conn
        finally:
            conn.close()

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

    def register_embedder(self, embedder) -> int:
        """Register an embedder and create its table."""
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

        # Create the embedder-specific table
        self._create_embedder_table(embedder)

        return embedder_id

    def _create_embedder_table(self, embedder) -> None:
        """Create the table for storing embeddings from this embedder."""
        # Sanitize the name so it forms a valid SQL identifier (replace hyphens with underscores).
        safe_name = embedder.name.replace("-", "_")
        table_name = f"embeddings_{safe_name}"
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
              USING hnsw (embedding vector_cosine_ops);
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

        # Add chunks for new document
        cache: dict[tuple[str, int], int] = {}
        for i, chunk in enumerate(norm_chunks):
            chunk_hash = chunk_content_hash(chunk.text)
            meta = psycopg.types.json.Json(chunk.metadata or {})
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
                self._copy_reusable_embeddings(row[0]["id"], chunk_hash, embedder_ids, cache)

        # Phase D / Wave 3 — persist extractor-emitted labels on the
        # document row. Idempotent: ``apply_label`` is a no-op when
        # the (namespace, value) pair is already attached.
        self._apply_document_labels(doc_id, doc)

        return doc_id

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
        self, embedder_id: int, limit: int = 1024
    ) -> Iterator[tuple[int, str]]:
        """Get chunks that are missing embeddings for the given embedder."""
        # Get embedder info
        embedder_info = self._execute(
            "SELECT name FROM corpus.embedders WHERE id = %s", (embedder_id,)
        )

        if not embedder_info:
            return

        embedder_name = embedder_info[0]["name"]
        table_name = f"embeddings_{embedder_name.replace('-', '_')}"

        # Query for chunks missing this embedder's embedding
        query = f"""
        SELECT c.id, c.text
        FROM corpus.chunks c
        LEFT JOIN corpus.{table_name} e ON e.chunk_id = c.id
        WHERE e.chunk_id IS NULL
        ORDER BY c.id
        LIMIT %s
        """

        results = self._execute(query, (limit,))
        for row in results:
            yield (row["id"], row["text"])

    @contextmanager
    def lock_source(self, key: str):
        """Context manager for advisory lock on a source."""
        lock_key = advisory_lock_key(key)
        with self._get_connection() as conn, conn.cursor() as cur:
            # Try to acquire advisory lock
            cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
            row = cur.fetchone()
            acquired = row[0] if row is not None else False

            if not acquired:
                raise RuntimeError(f"Could not acquire lock for source: {key}")

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
    ) -> "list":
        """pgvector cosine search via ``<=>`` against the per-embedder table.

        Returns ``Hit`` objects ordered by descending similarity
        (``score = 1.0 - cosine_distance``).  ``LEFT JOIN`` documents +
        conversations so message-only chunks are still attributable to a
        dataset.
        """
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        # Look up the per-embedder table (e.g. corpus.embeddings_qwen3_8b).
        rows = self._execute(
            "SELECT table_name FROM corpus.embedders WHERE id = %s", (embedder_id,)
        )
        if not rows:
            return []
        table_name = rows[0]["table_name"]

        # pgvector expects the literal vector(...) cast for psycopg's
        # parameter binding.  Serialise to "[v1,v2,...]" form.
        vec_str = "[" + ",".join(repr(float(x)) for x in np.asarray(query_vector).ravel()) + "]"

        params: list = [vec_str]
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = %s"
            params.append(dataset_id)
        params.append(k)

        # NB: f-string interpolation of table_name is safe — register_embedder
        # synthesises it from a sanitised embedder name.
        result = self._execute(
            f"""
            SELECT c.id, c.text, c.document_id, c.conversation_id, c.metadata,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title,
                   (e.embedding <=> %s::vector) AS distance
            FROM corpus.{table_name} e
            JOIN corpus.chunks c ON c.id = e.chunk_id
            LEFT JOIN corpus.documents d ON d.id = c.document_id
            LEFT JOIN corpus.conversations cv ON cv.id = c.conversation_id
            WHERE TRUE{ds_filter}
            ORDER BY e.embedding <=> %s::vector
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
    ) -> "list":
        """``text_tsv @@ websearch_to_tsquery('english', %s)`` ranked by ts_rank_cd.

        ``ts_rank_cd`` is already higher-is-better, so we use it directly as
        ``Hit.score`` (clipping into [0, 1] — typical ts_rank_cd values for
        short documents stay well inside that range but the contract says
        normalised scores so we cap).
        """
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        params: list = [query, query]  # one for the rank, one for the WHERE
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = %s"
            params.append(dataset_id)
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
            WHERE c.text_tsv @@ websearch_to_tsquery('english', %s){ds_filter}
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
        """Return chunk row joined to documents + conversations (LEFT JOIN)."""
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
        return rows[0] if rows else None

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
            if entity_type == "chunk":
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
