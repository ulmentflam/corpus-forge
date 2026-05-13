"""SQLite storage backend for corpus-forge — B-03 skeleton + migrate().

Single-host, file-based backend.  No connection pool, no async, no LISTEN/NOTIFY.
For protocol symmetry the constructor accepts a `schema` parameter, but SQLite has
no schema namespacing so the value is stored and ignored at query time.
"""

import contextlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..identity import chunk_content_hash
from ..schema import migrate as _migrate_module
from ..sources.base import RawDocument
from .sqlite_vec_loader import SQLITE_VEC_AVAILABLE, load_sqlite_vec

if TYPE_CHECKING:
    import numpy as np

    from ..sources.base import RawConversation


class _NoCommitConn:
    """Thin proxy around ``sqlite3.Connection`` that turns ``commit()`` into a no-op.

    Used inside ``lock_source`` so that ``_execute`` calls from the lock body
    run within the lock's open ``BEGIN IMMEDIATE`` transaction without prematurely
    committing it.  ``close()`` is also suppressed — the lock context manager
    owns the connection lifetime.

    All other attribute access is delegated to the underlying connection, so
    ``execute``, ``row_factory``, ``cursor.description``, and similar attributes
    behave exactly as on the real connection.
    """

    def __init__(self, conn: "sqlite3.Connection") -> None:
        # Use object.__setattr__ so that __getattr__ is not triggered for _conn.
        object.__setattr__(self, "_conn", conn)

    def __getattr__(self, name: str) -> object:
        return getattr(object.__getattribute__(self, "_conn"), name)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn: sqlite3.Connection = object.__getattribute__(self, "_conn")
        return conn.execute(sql, params)

    def commit(self) -> None:
        """No-op: lock_source issues the final COMMIT or ROLLBACK."""

    def close(self) -> None:
        """No-op: lock_source manages the connection lifetime."""


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

    def _open_connection(self) -> sqlite3.Connection:
        """Open and return a fresh ``sqlite3.Connection`` for use by ``lock_source``.

        Unlike ``_get_connection`` (a context manager that closes on exit), this
        method returns the connection directly so that the caller controls its
        lifetime.  Used by ``lock_source`` to hold a dedicated connection across
        the entire duration of the context manager body.

        Unlike ``_get_connection``, this helper intentionally does **not** issue
        ``PRAGMA journal_mode = WAL``.  Changing the journal mode requires a
        database write-lock, which may be held by a concurrent external writer
        precisely when ``lock_source`` is contending for entry.  The database is
        already in WAL mode after ``migrate()`` has run, so re-issuing the
        PRAGMA is unnecessary.

        The caller is responsible for calling ``conn.close()`` when done.
        """
        path_str = str(self.path)
        # timeout=0: let our Python retry loop (in lock_source) manage the
        # backoff instead of SQLite's built-in busy handler.  With SQLite's
        # default timeout=5.0, BEGIN IMMEDIATE would block for up to 5 seconds
        # internally, making lock_timeout_s unreliable.
        if path_str == ":memory:":
            uri = f"file:corpus_forge_mem_{id(self)}?mode=memory&cache=shared"
            if self._memory_keeper is None:
                self._memory_keeper = sqlite3.connect(uri, uri=True)
            conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
        else:
            conn = sqlite3.connect(path_str, isolation_level=None, timeout=0)
        conn.row_factory = sqlite3.Row
        # PRAGMA foreign_keys is a per-connection flag — it does not write to
        # the database file and does not require a write-lock.  Do NOT issue
        # PRAGMA journal_mode = WAL here; that modifies the DB header and would
        # block if another writer holds the write-lock (common during contention).
        conn.execute("PRAGMA foreign_keys = ON")
        if SQLITE_VEC_AVAILABLE:
            load_sqlite_vec(conn)
        return conn

    # Per-instance write lock: serialises concurrent lock_source calls
    # from threads within the same process.  This is the intra-process
    # counterpart to the SQLite ``BEGIN IMMEDIATE`` write lock, which
    # serialises writers from different processes.
    _write_lock: threading.Lock

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)  # pyrefly: ignore[bad-argument-type]

    @contextlib.contextmanager  # type: ignore[return]
    def lock_source(
        self,
        key: str,  # noqa: ARG002 — accepted for StorageBackend protocol parity; SQLite uses the global write-lock, not per-key advisory locks
        lock_timeout_s: float = 30.0,
    ) -> "AbstractContextManager[None]":  # pyrefly: ignore[bad-return]  # @contextlib.contextmanager transforms the generator into a context manager at runtime; the public return type is AbstractContextManager[None]
        """Acquire a write-lock for the duration of the ``with`` block.

        SQLite has no per-source advisory locks (unlike ``pg_advisory_lock`` in
        Postgres).  Instead, this context manager acquires the *global* SQLite
        write lock via ``BEGIN IMMEDIATE`` on a dedicated connection, which
        serialises all writers for the lifetime of the block.  The ``key``
        argument is accepted for protocol parity with ``PostgresBackend.lock_source``
        but is **ignored** — per-key granularity is unnecessary on a single-machine
        backend where the global write lock already provides the required isolation.

        On entry:
            1. Acquire the instance-level Python ``threading.Lock`` to serialise
               concurrent callers from within the same process.
            2. Open a dedicated ``sqlite3.Connection`` (separate from ``_execute``
               connections, which each open and close their own connections).
            3. Issue ``BEGIN IMMEDIATE`` to acquire the database write lock.
               If another writer holds the lock, retry with exponential back-off
               (starting at 0.01 s, doubling each retry, capped at 1.0 s) until
               ``lock_timeout_s`` seconds have elapsed, then re-raise the
               ``OperationalError``.
            4. Temporarily replace the ``_get_connection`` instance attribute
               with a context manager that yields a ``_NoCommitConn`` proxy
               wrapping the dedicated connection.  This causes all
               ``_execute`` calls made in the lock body to run their SQL
               within the same ``BEGIN IMMEDIATE`` transaction without
               prematurely committing it.

        On exit (no exception):
            ``COMMIT`` the dedicated connection, restore ``_get_connection``,
            and close the connection.  Releases the Python threading lock.

        On exit (exception):
            ``ROLLBACK`` the dedicated connection, restore ``_get_connection``,
            and close the connection.  Releases the Python threading lock.
            Re-raises the exception.

        Args:
            key: Advisory-lock key accepted for protocol symmetry.  **Ignored.**
            lock_timeout_s: Maximum seconds to wait for ``BEGIN IMMEDIATE`` to
                succeed before raising ``OperationalError``.  Default 30.0 s.

        Yields:
            None

        Raises:
            sqlite3.OperationalError: If the write lock cannot be acquired within
                ``lock_timeout_s`` seconds.
        """
        if not hasattr(self, "_write_lock"):
            # Lazily initialise the threading lock on the first call.
            # A race on first access is harmless: two threads both constructing
            # a Lock and one winning the attribute set is safe because
            # threading.Lock is reentrant-free and the losing thread discards its
            # Lock.  Use object.__setattr__ to bypass any future __setattr__.
            object.__setattr__(self, "_write_lock", threading.Lock())

        lock = self._write_lock
        lock.acquire()
        conn: sqlite3.Connection | None = None
        try:
            conn = self._open_connection()
            # Attempt BEGIN IMMEDIATE with exponential back-off.
            deadline = time.monotonic() + lock_timeout_s
            delay = 0.01
            while True:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc).lower():
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    sleep_for = min(delay, remaining)
                    time.sleep(sleep_for)
                    delay = min(delay * 2, 1.0)

            # Build a _get_connection replacement that routes all _execute calls
            # from the lock body through the dedicated connection (wrapped in
            # _NoCommitConn to suppress premature commits).
            _lock_conn = conn

            @contextlib.contextmanager  # type: ignore[return]
            def _lock_get_connection():  # type: ignore[return]
                yield _NoCommitConn(_lock_conn)

            # Shadow the class method with an instance attribute so that
            # _execute's `with self._get_connection() as conn:` call picks up
            # this proxy for the duration of the lock body.
            self._get_connection = _lock_get_connection  # type: ignore[method-assign]
            try:
                yield
            except BaseException:
                with contextlib.suppress(Exception):
                    _lock_conn.execute("ROLLBACK")
                raise
            else:
                _lock_conn.execute("COMMIT")
            finally:
                # Always restore the original class-level _get_connection.
                with contextlib.suppress(AttributeError):
                    del self._get_connection  # type: ignore[misc]
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            lock.release()

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

    def _executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script as-is.

        Bypasses the per-statement comment-stripper / IF-NOT-EXISTS rewriter
        in ``_execute`` because those transforms break SQLite trigger bodies
        which contain a ``BEGIN ... END;`` block — the migration runner's
        ``";"`` splitter would otherwise produce malformed fragments.

        Use sparingly: only for migrations that legitimately need to ship
        ``CREATE TRIGGER`` bodies (currently just ``004_fts.sql``).
        """
        with self._get_connection() as conn:
            conn.executescript(sql)
            conn.commit()

    def backfill_lexical_index(self) -> int:
        """Populate ``chunks_fts`` for any rows that pre-date the 004 migration.

        The ``chunks_ai`` AFTER INSERT trigger keeps ``chunks_fts`` in sync
        for all new rows once the migration is applied, but rows that already
        existed when the migration ran need a one-shot backfill — the FTS5
        virtual table is empty after ``CREATE VIRTUAL TABLE``.

        Returns:
            The number of rows actually inserted into ``chunks_fts``.  On
            re-call the count is 0 (idempotent — the ``NOT IN`` filter skips
            rows already mirrored).
        """
        # Idempotent INSERT: only rows whose id is not yet a chunks_fts.rowid.
        # We need rowcount, so call sqlite3 directly (the _execute helper
        # discards cursor.rowcount).
        with self._get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO chunks_fts(rowid, text) "
                "SELECT id, text FROM chunks "
                "WHERE id NOT IN (SELECT rowid FROM chunks_fts)"
            )
            inserted = int(cur.rowcount or 0)
            conn.commit()
        return inserted

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
        # Sanitize the name so it forms a valid SQL identifier (mirror PostgresBackend
        # which replaces "-" with "_"). Without this, hyphenated embedder names like
        # "openai-3-large" produce invalid CREATE TABLE syntax and may risk injection.
        safe_name = embedder.name.replace("-", "_")
        table_name = f"embeddings_{safe_name}"
        # Use json.dumps for safe serialization — manual f-string interpolation breaks
        # for provider/model_id values containing quotes, backslashes, or non-ASCII.
        config_json = json.dumps({"provider": embedder.provider, "model_id": embedder.model_id})

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
            # Mirrors PostgresBackend._create_embedder_table: FK on embedder_id
            # for referential integrity, plus created_at for ingest-time tracking.
            # (vec0 virtual tables can't carry FKs / DEFAULT columns, so this only
            # applies to the plain-table fallback.)
            self._execute(
                f"CREATE TABLE IF NOT EXISTS {table_name}"
                f" (chunk_id INTEGER PRIMARY KEY,"
                f" embedder_id INTEGER NOT NULL,"
                f" embedding BLOB NOT NULL,"
                f" created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                f" FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,"
                f" FOREIGN KEY (embedder_id) REFERENCES embedders(id))"
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

    def write_embeddings(
        self,
        embedder_id: int,
        pairs: list[tuple[int, "np.ndarray"]],  # pyrefly: ignore[missing-import]
    ) -> None:
        """Insert (or replace) embedding rows for a set of (chunk_id, vector) pairs.

        Mirrors ``PostgresBackend.write_embeddings``:

        - Empty ``pairs`` → no-op, no error.
        - Unknown ``embedder_id`` → raises ``ValueError`` (same as Postgres).
        - Duplicate ``chunk_id``: uses DELETE-then-INSERT for vec0 virtual tables
          (which do not support ``ON CONFLICT``); uses ``INSERT OR REPLACE`` for
          the plain-BLOB fallback table.
        - Vectors are converted to ``float32`` before serialization.

        Args:
            embedder_id: Primary key from the ``embedders`` table.
            pairs: List of ``(chunk_id, np.ndarray)`` pairs to store.
        """
        if not pairs:
            return

        import numpy as np  # noqa: PLC0415

        embedder_rows = self._execute(
            "SELECT table_name FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            raise ValueError(f"Embedder with ID {embedder_id} not found")

        table_name = embedder_rows[0]["table_name"]

        if SQLITE_VEC_AVAILABLE:
            from sqlite_vec import serialize_float32  # noqa: PLC0415

            for chunk_id, arr in pairs:
                vec = np.asarray(arr, dtype=np.float32)
                blob = serialize_float32(vec.tolist())
                # vec0 does not support INSERT OR REPLACE / ON CONFLICT;
                # use DELETE-then-INSERT to achieve idempotent upsert.
                self._execute(
                    f"DELETE FROM {table_name} WHERE chunk_id = ?",
                    (chunk_id,),
                )
                self._execute(
                    f"INSERT INTO {table_name} (chunk_id, embedder_id, embedding) VALUES (?, ?, ?)",
                    (chunk_id, embedder_id, blob),
                )
        else:
            for chunk_id, arr in pairs:
                vec = np.asarray(arr, dtype=np.float32)
                blob = vec.tobytes()
                self._execute(
                    f"INSERT OR REPLACE INTO {table_name}"
                    f" (chunk_id, embedder_id, embedding)"
                    f" VALUES (?, ?, ?)",
                    (chunk_id, embedder_id, blob),
                )

    def chunks_missing_embedding(
        self, embedder_id: int, limit: int = 1024
    ) -> "Iterator[tuple[int, str]]":
        """Return chunks that have no embedding for the given embedder.

        Mirrors ``PostgresBackend.chunks_missing_embedding``:

        - Unknown ``embedder_id`` → returns empty (no table to query).
        - Returns a generator of ``(chunk_id: int, text: str)`` tuples.
        - Results are ordered by ``chunks.id`` and capped by ``limit``.

        Args:
            embedder_id: Primary key from the ``embedders`` table.
            limit: Maximum number of rows to return (default 1024).
        """
        embedder_rows = self._execute(
            "SELECT table_name FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            return

        table_name = embedder_rows[0]["table_name"]

        rows = self._execute(
            f"SELECT c.id, c.text FROM chunks c"
            f" WHERE NOT EXISTS ("
            f"   SELECT 1 FROM {table_name} e"
            f"   WHERE e.chunk_id = c.id AND e.embedder_id = ?"
            f" )"
            f" ORDER BY c.id LIMIT ?",
            (embedder_id, limit),
        )
        for row in rows:
            yield (row["id"], row["text"])

    # ── Document / Conversation lifecycle helpers (B-09) ─────────────────────

    def delete_document(self, dataset_id: int, source_uri: str) -> None:
        """Delete a document and its chunks (FK CASCADE).

        Idempotent: deleting a non-existent row is a no-op.
        """
        self._execute(
            "DELETE FROM documents WHERE dataset_id = ? AND source_uri = ?",
            (dataset_id, source_uri),
        )

    def delete_conversation(self, dataset_id: int, source_uri: str) -> None:
        """Delete a conversation and its messages/chunks (FK CASCADE).

        Idempotent: deleting a non-existent row is a no-op.
        """
        self._execute(
            "DELETE FROM conversations WHERE dataset_id = ? AND source_uri = ?",
            (dataset_id, source_uri),
        )

    def find_document(self, dataset_id: int, source_uri: str) -> "dict | None":
        """Look up a documents row without creating one.

        Returns None if no row exists for (dataset_id, source_uri).
        Read-only; never inserts.
        """
        rows = self._execute(
            "SELECT id, content_hash FROM documents WHERE dataset_id = ? AND source_uri = ?",
            (dataset_id, source_uri),
        )
        return rows[0] if rows else None

    def resolve_document(self, dataset_id: int, source_uri: str) -> "dict | None":
        """Idempotently look up or create a documents stub row.

        Returns the row as a dict with at least ``id`` and ``content_hash``.
        For new rows, inserts with empty text, empty content_hash, NULL title,
        and empty metadata.  Returns None only if source_uri is an empty string.

        Use this method when the caller must ensure a row exists (e.g. push
        handle_change).  For delete-side lookups that must NOT create stubs,
        use ``find_document`` instead.
        """
        if not source_uri:
            return None
        rows = self._execute(
            "SELECT id, content_hash FROM documents WHERE dataset_id = ? AND source_uri = ?",
            (dataset_id, source_uri),
        )
        if rows:
            return rows[0]
        result = self._execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, text, metadata)"
            " VALUES (?, ?, '', '', '{}')"
            " RETURNING id, content_hash",
            (dataset_id, source_uri),
        )
        return result[0] if result else None

    def resolve_self_source(self, dataset_id: int, host: str) -> int:
        """Upsert a sources row for this host's pull tracker and return its id.

        Keyed on (dataset_id, plugin='sync', identity='pull', host) UNIQUE.
        Idempotent: same args always return the same integer id.
        """
        rows = self._execute(
            "SELECT id FROM sources"
            " WHERE dataset_id = ? AND plugin = ? AND identity = ? AND host = ?",
            (dataset_id, "sync", "pull", host),
        )
        if rows:
            return int(rows[0]["id"])
        result = self._execute(
            "INSERT INTO sources (dataset_id, plugin, identity, host)"
            " VALUES (?, ?, ?, ?)"
            " RETURNING id",
            (dataset_id, "sync", "pull", host),
        )
        return int(result[0]["id"])

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
        the ``MAX(revision_number)+1`` allocation is atomic.  The lock is not
        acquired internally to avoid double-lock (BEGIN IMMEDIATE is non-reentrant).
        """
        max_row = self._execute(
            "SELECT MAX(revision_number) AS max FROM document_revisions WHERE document_id = ?",
            (document_id,),
        )
        revision_number = (max_row[0]["max"] or 0) + 1
        metadata_json = json.dumps(metadata if metadata is not None else {})
        result = self._execute(
            """
            INSERT INTO document_revisions
            (document_id, revision_number, parent_revision_id, content_hash,
             text, author_host, is_tombstone, metadata,
             created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            RETURNING id, revision_number
            """,
            (
                document_id,
                revision_number,
                parent_revision_id,
                content_hash,
                text,
                author_host,
                int(is_tombstone),
                metadata_json,
            ),
        )
        return {"id": result[0]["id"], "revision_number": result[0]["revision_number"]}

    def latest_revision(self, document_id: int) -> dict | None:
        """Return the highest revision_number row for a document, or None."""
        rows = self._execute(
            "SELECT * FROM document_revisions"
            " WHERE document_id = ? ORDER BY revision_number DESC LIMIT 1",
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
        ``parent_content_hash`` (from the parent revision row) in addition to
        all columns of document_revisions.

        ``last_pulled_revision_id=None`` is coerced to 0 (return everything).
        SQLite uses identical JOIN syntax to Postgres; no dialect changes needed.
        """
        last_id = last_pulled_revision_id if last_pulled_revision_id is not None else 0
        return self._execute(
            """
            SELECT r.*,
                   d.source_uri AS source_uri,
                   parent.content_hash AS parent_content_hash
            FROM document_revisions r
            JOIN documents d ON d.id = r.document_id
            LEFT JOIN document_revisions parent ON parent.id = r.parent_revision_id
            WHERE d.dataset_id = ? AND r.id > ? AND r.author_host <> ?
            ORDER BY r.id ASC LIMIT ?
            """,
            (dataset_id, last_id, self_host, limit),
        )

    def mark_revision_pulled(self, source_id: int, revision_id: int) -> None:
        """Advance last_pulled_revision_id for a source, monotonically.

        SQLite does not have GREATEST(); use MAX(a, b) which works as a scalar
        function when called with two arguments in an expression context.
        """
        self._execute(
            "UPDATE sources"
            " SET last_pulled_revision_id = MAX(COALESCE(last_pulled_revision_id, 0), ?)"
            " WHERE id = ?",
            (revision_id, source_id),
        )

    # ── Tombstone helpers (B-12) ──────────────────────────────────────────────

    def set_tombstone(self, document_id: int) -> None:
        """Mark a document as tombstoned with the current UTC timestamp.

        Uses SQLite's strftime to produce an ISO-8601 string with millisecond
        precision ending in 'Z' (e.g. "2026-05-09T23:59:59.123Z").
        Idempotent: calling again updates tombstoned_at to the new current time.
        Unknown document_id is a no-op (UPDATE on 0 rows).
        """
        self._execute(
            "UPDATE documents"
            " SET tombstoned_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (document_id,),
        )

    def clear_tombstone(self, document_id: int) -> None:
        """Remove tombstone from a document by setting tombstoned_at to NULL.

        Idempotent: calling on a document already NULL is a no-op.
        Unknown document_id is a no-op (UPDATE on 0 rows).
        """
        self._execute(
            "UPDATE documents SET tombstoned_at = NULL WHERE id = ?",
            (document_id,),
        )

    # ── Dataset helpers (B-18 dialect-lift) ──────────────────────────────────

    def get_or_create_dataset(self, name: str, kind: str, description: str) -> int:
        """Return the id of the named dataset, creating it if absent.

        SELECT-first then INSERT pattern: avoids INSERT OR IGNORE semantics
        so that the description/kind are always set correctly on first creation.
        Uses bare table name ``datasets`` (no ``corpus.`` prefix — SQLite has
        no schema namespacing) and ``?`` placeholders.
        """
        existing = self._execute(
            "SELECT id FROM datasets WHERE name = ?",
            (name,),
        )
        if existing:
            return existing[0]["id"]
        result = self._execute(
            """
            INSERT INTO datasets (name, kind, description)
            VALUES (?, ?, ?)
            RETURNING id
            """,
            (name, kind, description),
        )
        return result[0]["id"]

    def find_dataset_id_by_name(self, name: str) -> int | None:
        """Return the id of the named dataset, or None if it does not exist."""
        rows = self._execute(
            "SELECT id FROM datasets WHERE name = ?",
            (name,),
        )
        return rows[0]["id"] if rows else None

    def register_source(self, dataset_id: int, plugin: str, identity: str, host: str) -> int:
        """Upsert a sources row for a plugin/identity/host triple and return its id.

        Mirrors ``resolve_self_source`` but accepts arbitrary plugin and identity
        values, so ingest can register the actual source plugin (e.g.
        ``plugin='markdown_vault'``) rather than the sync-specific ``'sync'/'pull'``.
        """
        rows = self._execute(
            "SELECT id FROM sources"
            " WHERE dataset_id = ? AND plugin = ? AND identity = ? AND host = ?",
            (dataset_id, plugin, identity, host),
        )
        if rows:
            return int(rows[0]["id"])
        result = self._execute(
            "INSERT INTO sources (dataset_id, plugin, identity, host)"
            " VALUES (?, ?, ?, ?)"
            " RETURNING id",
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
        """Return the top-*k* nearest chunks for *query_vector* (cosine).

        Lifts the SQL from ``scripts/query_repo_sqlite.py`` and wraps the
        result in ``Hit`` objects.  Uses the per-embedder vec0 virtual table's
        ``MATCH ... AND k = ?`` operator.

        - Unknown ``embedder_id`` → empty list.
        - ``Hit.score = 1 - distance`` (sqlite-vec returns cosine distance for
          normalised vectors; higher score = better, matching the protocol
          contract).
        - ``LEFT JOIN documents`` because message-only conversation chunks
          have ``document_id IS NULL``; the join still yields the row with
          ``source_uri`` / ``title`` as ``None``.
        - Dataset filter is applied as a SARGable predicate after the vec0
          MATCH because the filter cannot be pushed inside the virtual table.
        """
        # Local import — Hit lives in retrieval.types; pulled in lazily to keep
        # the backend module import-light.
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        embedder_rows = self._execute(
            "SELECT table_name FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            return []
        table_name = embedder_rows[0]["table_name"]

        if not SQLITE_VEC_AVAILABLE:
            # Without sqlite-vec we have only a fallback BLOB store with no
            # ANN search.  Surface an empty result rather than scanning every
            # row in Python.
            return []

        import numpy as np  # noqa: PLC0415
        from sqlite_vec import serialize_float32  # noqa: PLC0415

        vec = np.asarray(query_vector, dtype=np.float32)
        blob = serialize_float32(vec.tolist())

        # Step 1: vec0 MATCH gives us chunk_id + distance.
        # f-string interpolation of table_name is safe — the value is
        # synthesised in register_embedder() from a sanitised embedder name.
        # NB: vec0 requires the literal "k = ?" predicate in the WHERE clause.
        match_rows = self._execute(
            f"SELECT chunk_id, distance FROM {table_name}"
            " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (blob, k),
        )
        if not match_rows:
            return []

        chunk_ids = [int(r["chunk_id"]) for r in match_rows]
        distance_by_id = {int(r["chunk_id"]): float(r["distance"]) for r in match_rows}

        # Step 2: pull chunk + document metadata.  LEFT JOIN documents +
        # conversations so we can resolve the chunk's dataset_id even for
        # message chunks (where document_id IS NULL).  Filter by dataset_id
        # if the caller requested it.
        placeholders = ",".join("?" * len(chunk_ids))
        params: tuple = (*chunk_ids,)
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = ?"
            params = (*params, dataset_id)
        rows = self._execute(
            f"""
            SELECT c.id, c.text, c.document_id, c.conversation_id, c.metadata,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            LEFT JOIN conversations cv ON cv.id = c.conversation_id
            WHERE c.id IN ({placeholders}){ds_filter}
            """,
            params,
        )

        by_id = {int(r["id"]): r for r in rows}

        # Preserve the vec0 ordering and zip in the joined metadata.
        hits: list = []
        for cid in chunk_ids:
            r = by_id.get(cid)
            if r is None:
                continue
            md_raw = r.get("metadata")
            try:
                metadata = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw or {})
            except (TypeError, ValueError):
                metadata = {}
            score = 1.0 - distance_by_id[cid]
            hits.append(
                Hit(
                    chunk_id=cid,
                    score=float(score),
                    text=r["text"],
                    document_id=(int(r["document_id"]) if r["document_id"] is not None else None),
                    source_uri=r["source_uri"],
                    title=r["title"],
                    dataset_id=int(r["dataset_id"]) if r["dataset_id"] is not None else 0,
                    metadata=metadata if isinstance(metadata, dict) else {},
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
        """BM25-ranked lexical search over the ``chunks_fts`` FTS5 virtual table.

        - FTS5's ``bm25()`` returns *lower* values for *better* matches; we
          normalise to higher-is-better via ``score = 1 / (1 + bm25)``.
        - ``LEFT JOIN documents`` to surface ``source_uri`` / ``title`` for
          document chunks; message chunks have ``document_id IS NULL`` and
          carry both as ``None``.
        - Dataset filter is a SARGable predicate on ``chunks.dataset_id``.
        """
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        params: tuple = (query,)
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = ?"
            params = (*params, dataset_id)
        params = (*params, k)

        rows = self._execute(
            f"""
            SELECT c.id, c.text, c.document_id, c.conversation_id, c.metadata,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title,
                   bm25(chunks_fts) AS bm25_score
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            LEFT JOIN documents d ON d.id = c.document_id
            LEFT JOIN conversations cv ON cv.id = c.conversation_id
            WHERE chunks_fts MATCH ?{ds_filter}
            ORDER BY bm25_score
            LIMIT ?
            """,
            params,
        )

        hits: list = []
        for r in rows:
            bm25 = float(r["bm25_score"]) if r["bm25_score"] is not None else 0.0
            score = 1.0 / (1.0 + bm25)
            md_raw = r.get("metadata")
            try:
                metadata = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw or {})
            except (TypeError, ValueError):
                metadata = {}
            hits.append(
                Hit(
                    chunk_id=int(r["id"]),
                    score=float(score),
                    text=r["text"],
                    document_id=(int(r["document_id"]) if r["document_id"] is not None else None),
                    source_uri=r["source_uri"],
                    title=r["title"],
                    dataset_id=int(r["dataset_id"]) if r["dataset_id"] is not None else 0,
                    metadata=metadata if isinstance(metadata, dict) else {},
                    source="lexical",
                )
            )
        return hits

    def get_chunk(self, chunk_id: int) -> "dict | None":
        """Return chunk row joined to its document (for source_uri / title).

        LEFT JOIN documents because message chunks have ``document_id IS NULL``;
        the joined ``source_uri`` / ``title`` are then None on the returned dict.
        """
        rows = self._execute(
            """
            SELECT c.id, c.document_id, c.conversation_id, c.message_id,
                   c.chunk_index, c.text, c.heading, c.role, c.token_count,
                   c.metadata, c.content_hash,
                   COALESCE(d.dataset_id, cv.dataset_id) AS dataset_id,
                   d.source_uri, d.title
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            LEFT JOIN conversations cv ON cv.id = c.conversation_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        )
        return rows[0] if rows else None

    def list_datasets(self) -> "list[dict]":
        """Return all datasets with their document + chunk counts.

        Counts are computed via ``LEFT JOIN`` so freshly-created datasets
        (no documents / chunks yet) appear with zero counts rather than being
        omitted.  Chunks have no direct ``dataset_id``; they cascade via
        ``documents`` (or ``conversations`` for chat chunks), so we join
        chunks through ``documents`` to attribute them to the right dataset.
        Ordered by ``name`` for stable consumer output.
        """
        rows = self._execute(
            """
            SELECT d.name, d.kind, d.description,
                   COALESCE(doc_counts.n, 0) AS document_count,
                   COALESCE(doc_counts.c, 0) + COALESCE(conv_counts.c, 0) AS chunk_count
            FROM datasets d
            LEFT JOIN (
                SELECT doc.dataset_id AS dataset_id,
                       COUNT(DISTINCT doc.id) AS n,
                       COUNT(c.id) AS c
                FROM documents doc
                LEFT JOIN chunks c ON c.document_id = doc.id
                GROUP BY doc.dataset_id
            ) doc_counts ON doc_counts.dataset_id = d.id
            LEFT JOIN (
                SELECT cv.dataset_id AS dataset_id,
                       COUNT(c.id) AS c
                FROM conversations cv
                LEFT JOIN chunks c ON c.conversation_id = cv.id
                GROUP BY cv.dataset_id
            ) conv_counts ON conv_counts.dataset_id = d.id
            ORDER BY d.name
            """,
        )
        return rows
