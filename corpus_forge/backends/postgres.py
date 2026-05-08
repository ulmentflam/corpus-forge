"""PostgreSQL storage backend implementation for corpus-forge."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import numpy as np
import psycopg
from psycopg.rows import dict_row

from ..identity import advisory_lock_key, chunk_content_hash
from .base import StorageBackend

if TYPE_CHECKING:
    from corpus_forge.sources.base import RawConversation, RawDocument


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
            yield conn
        finally:
            conn.close()

    def _execute(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return results as list of dicts."""
        with self._get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            if cur.description:  # SELECT query
                return [dict(row) for row in cur.fetchall()]
            else:  # INSERT/UPDATE/DELETE
                conn.commit()
                return []

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
          plugin       TEXT NOT NULL,                -- 'markdown_vault' |
              'claude_code' | 'opencode'
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
        
        -- Chunks (the embedded unit; XOR doc/conv) ----------------------------------
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
          metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
          CHECK ( (document_id IS NOT NULL)::int + (conversation_id IS NOT NULL)::int = 1 ),
          UNIQUE (document_id, chunk_index),
          UNIQUE (conversation_id, message_id, chunk_index)
        );
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

    def register_embedder(self, embedder) -> int:
        """Register an embedder and create its table."""
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
                    embedder.active,
                    f"embeddings_{embedder.name}",
                    {"provider": embedder.provider, "model_id": embedder.model_id},
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
                    f"embeddings_{embedder.name}",
                    {"provider": embedder.provider, "model_id": embedder.model_id},
                ),
            )
            embedder_id = result[0]["id"]

        # Create the embedder-specific table
        self._create_embedder_table(embedder)

        return embedder_id

    def _create_embedder_table(self, embedder) -> None:
        """Create the table for storing embeddings from this embedder."""
        table_name = f"embeddings_{embedder.name}"
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
        self, dataset_id: int, doc: "RawDocument", chunks: list[tuple[str | None, str]],
        embedder_ids: list[int] | None = None,
    ) -> int:
        """Insert or update a document and its chunks."""
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

            # Update document
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

            # Delete existing chunks (we'll re-add them)
            self._execute("DELETE FROM corpus.chunks WHERE document_id = %s", (doc_id,))
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

        # Add chunks
        cache: dict = {}
        for i, (heading, text) in enumerate(chunks):
            chunk_hash = chunk_content_hash(text)
            row = self._execute(
                """
                INSERT INTO corpus.chunks 
                (document_id, chunk_index, heading, text, metadata, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (doc_id, i, heading, text, psycopg.types.json.Json({}), chunk_hash),
            )
            if embedder_ids is not None:
                self._copy_reusable_embeddings(row[0]["id"], chunk_hash, embedder_ids, cache)

        return doc_id

    def upsert_conversation(
        self,
        dataset_id: int,
        conv: "RawConversation",
        chunked_messages: list[list[tuple[str | None, str]]],
    ) -> int:
        """Insert or update a conversation and its messages/chunks."""
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
                    conv.started_at,
                    conv.ended_at,
                    len(conv.messages),
                    psycopg.types.json.Json(conv.metadata),
                    conv_id,
                ),
            )

            # Delete existing messages and chunks
            self._execute("DELETE FROM corpus.messages WHERE conversation_id = %s", (conv_id,))
        else:
            # Insert new conversation
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
                    conv.started_at,
                    conv.ended_at,
                    len(conv.messages),
                    conv.content_hash,
                    psycopg.types.json.Json(conv.metadata),
                ),
            )
            conv_id = result[0]["id"]

        # Add messages
        message_ids = []
        for i, message in enumerate(conv.messages):
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
                    message.ts,
                    psycopg.types.json.Json(message.metadata),
                ),
            )
            message_ids.append(result[0]["id"])

        # Add chunks for each message
        for msg_idx, chunks_in_msg in enumerate(chunked_messages):
            message_id = message_ids[msg_idx]
            for chunk_idx, (heading, text) in enumerate(chunks_in_msg):
                self._execute(
                    """
                    INSERT INTO corpus.chunks 
                    (conversation_id, message_id, chunk_index, heading, text, metadata, role)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        conv_id,
                        message_id,
                        chunk_idx,
                        heading,
                        text,
                        psycopg.types.json.Json({}),
                        conv.messages[msg_idx].role,
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
        table_name = f"embeddings_{embedder_name}"

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
        table_name = f"embeddings_{embedder_name}"

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
            acquired = cur.fetchone()[0]

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
                INSERT INTO {embedder_table} (chunk_id, embedding)
                SELECT %s, embedding FROM {embedder_table}
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
        source_uri: str,
        content_hash: str,
        text: str,
        parent_revision_id: int | None,
        author_host: str,
        is_tombstone: bool,
        metadata: dict | None = None,
    ) -> dict:
        """Insert a new revision under advisory lock, returning id + revision_number."""
        with self.lock_source(source_uri):
            max_row = self._execute(
                "SELECT MAX(revision_number) AS max FROM corpus.document_revisions WHERE document_id = %s",
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
                    psycopg.types.json.Json(metadata) if metadata else None,
                ),
            )
            return {"id": result[0]["id"], "revision_number": result[0]["revision_number"]}

    def latest_revision(self, document_id: int) -> dict | None:
        """Return the highest revision_number row for a document, or None."""
        rows = self._execute(
            "SELECT * FROM corpus.document_revisions WHERE document_id = %s ORDER BY revision_number DESC LIMIT 1",
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
        """Return revisions from other hosts not yet pulled."""
        last_id = last_pulled_revision_id if last_pulled_revision_id is not None else 0
        return self._execute(
            """
            SELECT r.* FROM corpus.document_revisions r
            JOIN corpus.documents d ON d.id = r.document_id
            WHERE d.dataset_id = %s AND r.id > %s AND r.author_host <> %s
            ORDER BY r.id ASC LIMIT %s
            """,
            (dataset_id, last_id, self_host, limit),
        )

    def mark_revision_pulled(self, source_id: int, revision_id: int) -> None:
        """Advance last_pulled_revision_id for a source using GREATEST."""
        self._execute(
            "UPDATE corpus.sources SET last_pulled_revision_id = GREATEST(COALESCE(last_pulled_revision_id, 0), %s) WHERE id = %s",
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
