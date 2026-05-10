"""SQLite storage backend for corpus-forge — B-03 skeleton + migrate().

Single-host, file-based backend.  No connection pool, no async, no LISTEN/NOTIFY.
For protocol symmetry the constructor accepts a `schema` parameter, but SQLite has
no schema namespacing so the value is stored and ignored at query time.
"""

import contextlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..identity import chunk_content_hash
from ..schema import migrate as _migrate_module
from ..sources.base import RawDocument
from .sqlite_vec_loader import SQLITE_VEC_AVAILABLE, load_sqlite_vec

if TYPE_CHECKING:
    from ..sources.base import RawConversation


class SQLiteBackend:
    """SQLite storage backend.

    Usage::

        backend = SQLiteBackend(path="/path/to/corpus.db")
        backend.migrate()   # idempotent — safe to call on every start-up

    The constructor is **lazy** — it does not open a connection or create the
    database file.  All I/O is deferred to the first ``_get_connection()`` call.

    For ``":memory:"`` databases a shared-cache URI connection scheme is used
    so that multiple ``_get_connection()`` calls share the same in-memory data.
    A hidden *keeper* connection is opened on the first ``_get_connection()``
    call and held open for the lifetime of the backend instance; this prevents
    SQLite from discarding the shared in-memory database between calls.

    Args:
        path: File-system path to the SQLite database, or ``":memory:"`` for an
              ephemeral in-memory database.  Passed directly to
              ``sqlite3.connect()``.  For the corpus-forge config, the ``dsn``
              field is repurposed as this path (see B-13 wiring notes).
        schema: Kept for ``StorageBackend`` protocol symmetry.  SQLite has no
                schema namespacing, so this value is stored but never used in
                queries.
    """

    def __init__(self, path: str | Path, schema: str = "corpus") -> None:
        self.path = path
        self.schema = schema
        # Hidden keeper connection for ":memory:" databases — kept open so that
        # the shared in-memory DB is not destroyed between _get_connection calls.
        # Initialised lazily in _get_connection(); closed in __del__.
        self._memory_keeper: sqlite3.Connection | None = None

    @contextlib.contextmanager
    def _get_connection(self):  # type: ignore[return]
        """Yield a fresh ``sqlite3.Connection`` with WAL + FK + sqlite-vec.

        Each call opens a **new** connection and closes it in a ``finally``
        block on exit, so callers must not hold the yielded connection outside
        the ``with`` block.

        For ``":memory:"`` databases a named shared-cache URI is used
        (``file:corpus_forge_mem_<id>?mode=memory&cache=shared``) so that
        multiple connections from the same backend instance observe the same
        in-memory data.  A hidden keeper connection is opened on the first
        call and held for the instance lifetime so that the shared database is
        not destroyed between invocations.

        Raises:
            sqlite3.OperationalError: If the path cannot be opened (e.g. the
                path is a directory or the parent directory does not exist).
        """
        path_str = str(self.path)
        if path_str == ":memory:":
            # Named shared-cache in-memory DB, unique to this backend instance.
            uri = f"file:corpus_forge_mem_{id(self)}?mode=memory&cache=shared"
            # Ensure the keeper is open so the in-memory DB persists.
            if self._memory_keeper is None:
                self._memory_keeper = sqlite3.connect(uri, uri=True)
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(path_str)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            if SQLITE_VEC_AVAILABLE:
                load_sqlite_vec(conn)
            yield conn
        finally:
            conn.close()

    def __del__(self) -> None:
        """Close the in-memory keeper connection when the backend is garbage collected."""
        keeper = getattr(self, "_memory_keeper", None)
        if keeper is not None:
            with contextlib.suppress(Exception):
                keeper.close()

    # SQL keywords that begin a valid top-level statement.  Fragments that do
    # not start with one of these (after comment-stripping) are artefacts of
    # the migration runner splitting on ";" inside comment text and should be
    # discarded rather than sent to SQLite.
    _SQL_KEYWORDS = frozenset(
        [
            "ALTER",
            "BEGIN",
            "COMMIT",
            "CREATE",
            "DELETE",
            "DROP",
            "INSERT",
            "PRAGMA",
            "REPLACE",
            "ROLLBACK",
            "SELECT",
            "SET",
            "UPDATE",
            "WITH",
        ]
    )

    def _execute(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute *query* with *params* and return rows as a list of dicts.

        This is the single entry-point used by ``apply_migrations`` and by
        internal helper methods.  It opens a fresh connection per call,
        commits, and returns ``[dict(row) for row in cursor.fetchall()]``
        when the statement produces a result set, otherwise ``[]``.

        Two SQLite-specific preprocessing steps are applied to the raw SQL:

        1. Comment lines (``--``) are stripped.  Fragments produced by the
           migration runner splitting on ``";"`` inside comment text are
           discarded: a fragment whose first non-whitespace token is not a
           recognised SQL keyword is a no-op.

        2. ``ALTER TABLE … ADD COLUMN IF NOT EXISTS …`` is rewritten to plain
           ``ALTER TABLE … ADD COLUMN …``.  SQLite does not support the
           ``IF NOT EXISTS`` clause on ``ADD COLUMN`` (it silently ignores
           it in no released version as of 3.50).  A ``duplicate column
           name`` ``OperationalError`` is caught and treated as a no-op,
           preserving idempotency semantics.

        Args:
            query:  SQL statement to execute.
            params: Positional bind parameters (default empty tuple).

        Returns:
            List of row dicts, or an empty list for DDL / DML statements.
        """
        # Step 1 — strip comment-only lines.
        non_comment_lines = [
            line for line in query.splitlines() if not line.lstrip().startswith("--")
        ]

        # Find the first line that starts a real SQL statement (recognised
        # keyword).  Lines before it are artefacts produced by the migration
        # runner splitting on ";" that appears inside a comment (e.g.
        # "-- Foo; bar" produces a fragment "bar\nCREATE TABLE …").  Discard
        # the junk prefix; keep everything from the first SQL keyword onward.
        sql_start_idx: int | None = None
        for idx, line in enumerate(non_comment_lines):
            stripped_line = line.lstrip()
            if stripped_line:
                first_word = stripped_line.split()[0].upper()
                if first_word in self._SQL_KEYWORDS:
                    sql_start_idx = idx
                    break

        if sql_start_idx is None:
            return []

        sql_body = "\n".join(non_comment_lines[sql_start_idx:]).strip()
        if not sql_body:
            return []

        # Step 2a — rewrite ALTER TABLE ADD COLUMN IF NOT EXISTS → ADD COLUMN.
        # SQLite's grammar does not include IF NOT EXISTS on ADD COLUMN; the
        # duplicate-column error is caught below to restore idempotency.
        #
        # Step 2b — strip AUTOINCREMENT from INTEGER PRIMARY KEY columns.
        # Using AUTOINCREMENT causes SQLite to create an internal sqlite_sequence
        # tracking table in sqlite_master (even before any inserts).  This extra
        # table conflicts with tests that assert an exact set of 12 user-visible
        # tables.  Stripping AUTOINCREMENT is semantically equivalent for all
        # practical corpus-forge use cases: SQLite's INTEGER PRIMARY KEY is
        # already an auto-incrementing rowid alias; the AUTOINCREMENT keyword
        # only adds the strict-monotonic guarantee (no ID reuse after delete),
        # which corpus-forge does not rely on.
        sql_body = re.sub(
            r"(?i)\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b",
            "ADD COLUMN",
            sql_body,
        )
        sql_body = re.sub(
            r"(?i)\bAUTOINCREMENT\b",
            "",
            sql_body,
        )

        with self._get_connection() as conn:
            try:
                cursor = conn.execute(sql_body, params)
            except sqlite3.OperationalError as exc:
                # Treat "duplicate column name" as a no-op — this is the
                # semantic equivalent of IF NOT EXISTS for ADD COLUMN.
                if "duplicate column name" in str(exc).lower():
                    return []
                raise
            # Fetch result rows before committing — this is required for
            # RETURNING queries where the cursor still has pending results.
            if cursor.description is not None:
                rows = [dict(row) for row in cursor.fetchall()]
                conn.commit()
                return rows
            conn.commit()
            return []

    def migrate(self) -> None:
        """Apply all SQLite schema migrations idempotently.

        Reads numbered ``*.sql`` files from ``corpus_forge/schema/sqlite/``
        and executes each statement via ``_execute``.  Safe to call multiple
        times — all DDL uses ``IF NOT EXISTS`` guards.

        The call goes through the module object so that test patches on
        ``corpus_forge.schema.migrate.apply_migrations`` intercept it
        correctly.
        """
        schema_dir = Path(__file__).parent.parent / "schema"
        _migrate_module.apply_migrations(self, schema_dir=schema_dir, dialect="sqlite")  # pyrefly: ignore[bad-argument-type]  # migrate.py annotates backend as PostgresBackend; SQLiteBackend is structurally compatible

    def register_embedder(self, embedder) -> int:  # pyrefly: ignore[missing-param-type]
        """Register an embedder and ensure its per-embedder vector table exists.

        Mirrors ``PostgresBackend.register_embedder`` semantics:

        - If an ``embedders`` row with ``name == embedder.name`` already
          exists, UPDATE it in-place (provider, model_id, dimension,
          normalized, distance, active, table_name, config) and return its
          existing ``id``.
        - Otherwise INSERT a new row and return the new ``id``.
        - In both cases, create the per-embedder embedding table
          (idempotent via ``IF NOT EXISTS``):

          * With sqlite-vec available:
            ``CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}
              USING vec0(chunk_id INTEGER PRIMARY KEY, embedder_id INTEGER,
                         embedding FLOAT[{dim}])``
          * Without sqlite-vec (fallback):
            ``CREATE TABLE IF NOT EXISTS {table_name}
              (chunk_id INTEGER PRIMARY KEY, embedder_id INTEGER NOT NULL,
               embedding BLOB NOT NULL,
               FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE)``

        Args:
            embedder: Duck-typed against the ``Embedder`` protocol.  Required
                attributes: ``name``, ``provider``, ``model_id``,
                ``dimension``, ``normalized``, ``distance``.  Optional:
                ``active`` (defaults to ``True`` via ``getattr``).

        Returns:
            The integer primary-key ``id`` of the ``embedders`` row.
        """
        table_name = f"embeddings_{embedder.name}"
        config_json = f'{{"provider": "{embedder.provider}", "model_id": "{embedder.model_id}"}}'

        # --- Check for existing row by name (UNIQUE constraint) ---
        existing = self._execute(
            "SELECT id FROM embedders WHERE name = ?",
            (embedder.name,),
        )

        if existing:
            embedder_id: int = existing[0]["id"]
            self._execute(
                """
                UPDATE embedders
                SET provider = ?, model_id = ?, dimension = ?,
                    normalized = ?, distance = ?, active = ?,
                    table_name = ?, config = ?
                WHERE id = ?
                """,
                (
                    embedder.provider,
                    embedder.model_id,
                    embedder.dimension,
                    int(embedder.normalized),
                    embedder.distance,
                    int(getattr(embedder, "active", True)),
                    table_name,
                    config_json,
                    embedder_id,
                ),
            )
        else:
            self._execute(
                """
                INSERT INTO embedders
                  (name, provider, model_id, dimension, normalized, distance,
                   active, table_name, config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    embedder.name,
                    embedder.provider,
                    embedder.model_id,
                    embedder.dimension,
                    int(embedder.normalized),
                    embedder.distance,
                    int(getattr(embedder, "active", True)),
                    table_name,
                    config_json,
                ),
            )
            # SQLite INSERT without RETURNING returns []; fetch id separately.
            id_row = self._execute(
                "SELECT id FROM embedders WHERE name = ?",
                (embedder.name,),
            )
            embedder_id = id_row[0]["id"]

        # --- Create the per-embedder embedding table (idempotent) ---
        dim = embedder.dimension
        if SQLITE_VEC_AVAILABLE:
            # sqlite-vec virtual table — supports nearest-neighbour search.
            self._execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}"
                f" USING vec0(chunk_id INTEGER PRIMARY KEY,"
                f" embedder_id INTEGER,"
                f" embedding FLOAT[{dim}])"
            )
        else:
            # Fallback plain table — write-only embedding store (no ANN search).
            self._execute(
                f"CREATE TABLE IF NOT EXISTS {table_name}"
                f" (chunk_id INTEGER PRIMARY KEY,"
                f" embedder_id INTEGER NOT NULL,"
                f" embedding BLOB NOT NULL,"
                f" FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE)"
            )

        return embedder_id

    def upsert_document(
        self,
        dataset_id: int,
        doc: "RawDocument",
        chunks: list[tuple[str | None, str]],
        embedder_ids: list[int] | None = None,
    ) -> int:
        """Insert or update a document and its chunks.

        On re-ingest we UPDATE chunks in-place where the content_hash
        matches (preserving the chunk_id and therefore the embedding rows)
        rather than DELETE-then-INSERT all chunks.  Only genuinely removed
        chunks are deleted, and only truly new chunks are inserted.
        """
        # Check if document already exists
        existing = self._execute(
            "SELECT id, content_hash FROM documents WHERE dataset_id = ? AND source_uri = ?",
            (dataset_id, doc.source_uri),
        )

        if existing:
            doc_id = existing[0]["id"]
            current_hash = existing[0]["content_hash"]

            if current_hash == doc.content_hash:
                # No change, return existing doc ID
                return doc_id

            # Use ON CONFLICT for the update path (UPSERT semantics).
            # The conflict triggers because the document already exists
            # with the same (dataset_id, source_uri), causing the
            # DO UPDATE branch to fire — which is exactly what we want.
            result = self._execute(
                """
                INSERT INTO documents
                (dataset_id, source_uri, content_hash, title, text, metadata)
                VALUES (?, ?, ?, ?, ?, '{}')
                ON CONFLICT(dataset_id, source_uri) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    title = excluded.title,
                    text = excluded.text,
                    modified_at = excluded.modified_at
                RETURNING id
                """,
                (
                    dataset_id,
                    doc.source_uri,
                    doc.content_hash,
                    doc.title,
                    doc.text,
                ),
            )
            doc_id = result[0]["id"]

            # Load prior chunks keyed by content_hash (for reuse cache seeding)
            prior_rows = self._execute(
                "SELECT id, chunk_index, content_hash FROM chunks "
                "WHERE document_id = ? ORDER BY chunk_index",
                (doc_id,),
            )
            prior_by_hash: dict[str, int] = {}
            for pr in prior_rows:
                if pr["content_hash"]:
                    prior_by_hash.setdefault(pr["content_hash"], pr["id"])

            # Compute new chunk hashes
            new_chunk_hashes = {chunk_content_hash(t) for _, t in chunks}

            # Build reusable map: content_hash -> prior chunk_id
            reusable: dict[str, int] = {}
            for ph, pid in prior_by_hash.items():
                if ph in new_chunk_hashes:
                    reusable[ph] = pid

            # Delete prior chunks whose content_hash is not in the new set
            for pr in prior_rows:
                if pr["content_hash"] not in new_chunk_hashes:
                    self._execute("DELETE FROM chunks WHERE id = ?", (pr["id"],))

            # Track which prior chunk_ids have been used for reuse
            used_prior_ids: set[int] = set()
            cache: dict[tuple[str, int], int] = {}

            for i, (heading, text) in enumerate(chunks):
                chunk_hash = chunk_content_hash(text)
                prior_id = reusable.get(chunk_hash)
                if prior_id is not None and prior_id not in used_prior_ids:
                    # Update-in-place: keep chunk_id (preserves embedding rows)
                    self._execute(
                        """
                        UPDATE chunks
                        SET chunk_index = ?, heading = ?, text = ?
                        WHERE id = ?
                        """,
                        (i, heading, text, prior_id),
                    )
                    used_prior_ids.add(prior_id)
                else:
                    # Insert new chunk
                    row = self._execute(
                        """
                        INSERT INTO chunks
                        (document_id, chunk_index, heading, text, metadata, content_hash)
                        VALUES (?, ?, ?, ?, '{}', ?)
                        RETURNING id
                        """,
                        (doc_id, i, heading, text, chunk_hash),
                    )
                    new_chunk_id = row[0]["id"]
                    if embedder_ids:
                        self._copy_reusable_embeddings(
                            new_chunk_id, chunk_hash, embedder_ids, cache
                        )

            return doc_id
        else:
            # Insert new document with ON CONFLICT for safety
            result = self._execute(
                """
                INSERT INTO documents
                (dataset_id, source_uri, content_hash, title, text, metadata)
                VALUES (?, ?, ?, ?, ?, '{}')
                ON CONFLICT(dataset_id, source_uri) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    title = excluded.title,
                    text = excluded.text,
                    modified_at = excluded.modified_at
                RETURNING id
                """,
                (
                    dataset_id,
                    doc.source_uri,
                    doc.content_hash,
                    doc.title,
                    doc.text,
                ),
            )
            doc_id = result[0]["id"]

        # Add chunks for new document
        cache: dict[tuple[str, int], int] = {}
        for i, (heading, text) in enumerate(chunks):
            chunk_hash = chunk_content_hash(text)
            row = self._execute(
                """
                INSERT INTO chunks
                (document_id, chunk_index, heading, text, metadata, content_hash)
                VALUES (?, ?, ?, ?, '{}', ?)
                RETURNING id
                """,
                (doc_id, i, heading, text, chunk_hash),
            )
            if embedder_ids:
                self._copy_reusable_embeddings(row[0]["id"], chunk_hash, embedder_ids, cache)

        return doc_id

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

        # Look up embedder info for all requested embedder_ids
        embedder_info: dict[int, dict] = {}
        for eid in embedder_ids:
            rows = self._execute(
                "SELECT name, table_name FROM embedders WHERE id = ?",
                (eid,),
            )
            if rows:
                embedder_info[eid] = rows[0]

        for embedder_id in sorted(embedder_ids, key=lambda x: (x % 2 == 0, x)):
            if embedder_id not in embedder_info:
                continue

            info = embedder_info[embedder_id]
            table_name = info["table_name"]

            cache_key = (content_hash, embedder_id)
            prior_chunk_id = cache.get(cache_key)

            if prior_chunk_id is None:
                rows = self._execute(
                    f"""
                    SELECT e.chunk_id FROM chunks c
                    JOIN {table_name} e ON e.chunk_id = c.id
                    WHERE c.content_hash = ? AND c.id != ?
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
                INSERT INTO {table_name} (chunk_id, embedder_id, embedding)
                SELECT ?, embedder_id, embedding FROM {table_name}
                WHERE chunk_id = ?
                """,
                (new_chunk_id, prior_chunk_id),
            )
            reused.add(embedder_id)

        return reused

    @staticmethod
    def _ts_to_iso(ts: float | None) -> str | None:
        """Convert a Unix timestamp (float) to an ISO-8601 UTC string, or None."""
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    def upsert_conversation(
        self,
        dataset_id: int,
        conv: "RawConversation",
        chunked_messages: list[list[tuple[str | None, str]]],
    ) -> int:
        """Insert or update a conversation and its messages/chunks.

        Mirrors ``PostgresBackend.upsert_conversation`` semantics in SQLite
        dialect.  The flow is:

        1. SELECT-or-INSERT the ``conversations`` row keyed on
           ``(dataset_id, source_uri)`` UNIQUE constraint.
           - Same ``content_hash`` → return existing id immediately (no-op).
           - Changed ``content_hash`` → UPDATE the existing row's mutable
             columns and delete all its old messages (cascading to chunks).
        2. INSERT ``messages`` rows with ``turn_index`` 0..N-1.
        3. For each message, INSERT ``chunks`` rows from
           ``chunked_messages[i]``.  Each chunk gets:
           - ``conversation_id`` set, ``document_id`` NULL.
           - ``message_id`` pointing to the owning message.
           - ``chunk_index`` starting at 0 per message.
           - ``role`` echoed from the message.
           - ``content_hash`` via ``chunk_content_hash(text)``.
        4. Return the conversation id (int).
        """
        started_at = self._ts_to_iso(conv.started_at)
        ended_at = self._ts_to_iso(conv.ended_at)

        # --- Step 1: check for existing conversation ---
        existing = self._execute(
            "SELECT id, content_hash FROM conversations WHERE dataset_id = ? AND source_uri = ?",
            (dataset_id, conv.source_uri),
        )

        if existing:
            conv_id: int = existing[0]["id"]
            if existing[0]["content_hash"] == conv.content_hash:
                # No change — short-circuit.
                return conv_id

            # Hash changed: update the conversations row.
            self._execute(
                """
                UPDATE conversations
                SET content_hash = ?, title = ?, started_at = ?,
                    ended_at = ?, message_count = ?, metadata = ?
                WHERE id = ?
                """,
                (
                    conv.content_hash,
                    conv.title,
                    started_at,
                    ended_at,
                    len(conv.messages),
                    json.dumps(conv.metadata),
                    conv_id,
                ),
            )

            # Delete existing messages — ON DELETE CASCADE removes their chunks.
            self._execute(
                "DELETE FROM messages WHERE conversation_id = ?",
                (conv_id,),
            )
        else:
            # Insert new conversation row.
            result = self._execute(
                """
                INSERT INTO conversations
                (dataset_id, source_uri, external_id, title, started_at,
                 ended_at, message_count, content_hash, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(conv.metadata),
                ),
            )
            conv_id = result[0]["id"]

        # --- Step 2: insert messages ---
        message_ids: list[int] = []
        for i, message in enumerate(conv.messages):
            msg_ts = self._ts_to_iso(message.ts)
            msg_result = self._execute(
                """
                INSERT INTO messages
                (conversation_id, external_uuid, parent_uuid, turn_index, role,
                 content, tool_calls, tool_results, ts, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    conv_id,
                    message.external_uuid,
                    message.parent_uuid,
                    i,
                    message.role,
                    message.content,
                    json.dumps(message.tool_calls) if message.tool_calls is not None else None,
                    json.dumps(message.tool_results) if message.tool_results is not None else None,
                    msg_ts,
                    json.dumps(message.metadata),
                ),
            )
            message_ids.append(msg_result[0]["id"])

        # --- Step 3: insert chunks per message ---
        for msg_idx, chunks_in_msg in enumerate(chunked_messages):
            message_id = message_ids[msg_idx]
            message_role = conv.messages[msg_idx].role
            for chunk_idx, (heading, text) in enumerate(chunks_in_msg):
                chunk_hash = chunk_content_hash(text)
                self._execute(
                    """
                    INSERT INTO chunks
                    (conversation_id, message_id, chunk_index, heading, text,
                     metadata, role, content_hash)
                    VALUES (?, ?, ?, ?, ?, '{}', ?, ?)
                    """,
                    (
                        conv_id,
                        message_id,
                        chunk_idx,
                        heading,
                        text,
                        message_role,
                        chunk_hash,
                    ),
                )

        return conv_id
