"""SQLite storage backend for corpus-forge — B-03 skeleton + migrate().

Single-host, file-based backend.  No connection pool, no async, no LISTEN/NOTIFY.
For protocol symmetry the constructor accepts a `schema` parameter, but SQLite has
no schema namespacing so the value is stored and ignored at query time.
"""

import contextlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..chunkers.base import TextChunk
from ..identity import chunk_content_hash
from ..schema import migrate as _migrate_module
from ..sources.base import RawDocument
from .base import IngestRunInProgressError, normalize_extensions_filter
from .sqlite_vec_loader import SQLITE_VEC_AVAILABLE, load_sqlite_vec

# Width of the legacy ``(heading, text)`` chunk shape accepted by
# :func:`_coerce_to_textchunk`. See ``postgres.py`` for rationale.
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


if TYPE_CHECKING:
    import numpy as np

    from corpus_forge.classifiers.base import ClassifiableDocument

    from ..retrieval.types import Hit
    from ..sources.base import RawConversation

logger = logging.getLogger(__name__)

# Valid entity types for labels and feedback helpers.
_LABEL_ENTITY_TYPES: tuple[str, ...] = ("chunk", "document", "conversation")
_FEEDBACK_ENTITY_TYPES: tuple[str, ...] = (*_LABEL_ENTITY_TYPES, "message")

# Maps entity_type -> (table_name, fk_column_name)
_LABEL_TABLE_MAP: dict[str, tuple[str, str]] = {
    "chunk": ("chunk_labels", "chunk_id"),
    "document": ("document_labels", "document_id"),
    "conversation": ("conversation_labels", "conversation_id"),
}

# Maps entity_type -> table_name for patch_metadata / set_description
_ENTITY_TABLE_MAP: dict[str, str] = {
    "chunk": "chunks",
    "document": "documents",
    "conversation": "conversations",
}

# Maximum number of recent feedback rows returned per entity by hydrate_hit_metadata.
_RECENT_FEEDBACK_LIMIT: int = 5


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
        key: str,
        lock_timeout_s: float = 30.0,
        *,
        wait: bool = True,
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
            key: Advisory-lock key.  If it starts with ``"ingest-run://"``, a
                cross-process file lock is used (see below).  Otherwise ignored.
            lock_timeout_s: Maximum seconds to wait for ``BEGIN IMMEDIATE`` to
                succeed before raising ``OperationalError``.  Default 30.0 s.
            wait: Controls behaviour on contention for the ingest-run lock.
                ``True`` (default) → block until the lock becomes available.
                ``False`` → try once; on failure raise
                ``IngestRunInProgressError``.  Ignored for per-document keys.

        Yields:
            None

        Raises:
            sqlite3.OperationalError: If the per-doc write lock cannot be
                acquired within ``lock_timeout_s`` seconds.
            IngestRunInProgressError: If ``key`` is an ingest-run key,
                ``wait=False``, and the file lock is already held.
        """
        # SR-G5: Ingest-run advisory lock uses a file lock instead of
        # BEGIN IMMEDIATE so it does not conflict with per-document locks
        # (which also use BEGIN IMMEDIATE + _write_lock).  This avoids a
        # reentrant-lock deadlock when ingest_once holds the ingest-run
        # lock while ingest_one holds a per-doc lock on the same backend.
        if key.startswith("ingest-run://"):
            from ..scanner.filelock import acquire as _fl_acquire  # noqa: PLC0415

            db_path = Path(getattr(self, "path", ":memory:"))
            if str(db_path) == ":memory:":
                # In-memory DB: no filesystem lock needed; yield immediately.
                yield
                return
            lock_file = db_path.parent / f".{db_path.stem}.ingest.lock"

            with _fl_acquire(lock_file, wait=wait) as acquired:
                if not acquired:
                    raise IngestRunInProgressError(
                        f"Another ingest run is in progress on this host "
                        f"(lock file: {lock_file}). "
                        "Use --wait to block until the running ingest finishes."
                    )
                yield
            return

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

        Because ``chunks_fts`` is declared as an **external-content** table
        (``content='chunks', content_rowid='id'``), the canonical backfill is
        the FTS5 ``'rebuild'`` command, which re-tokenises every row.  We only
        run it when there is at least one chunk not yet mirrored, so a
        second call returns ``0`` and is truly idempotent.

        Returns:
            The number of rows that were absent from ``chunks_fts`` before
            this call.  ``0`` on subsequent calls.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT rowid FROM chunks_fts)"
            ).fetchone()
            missing = int(row[0]) if row is not None else 0
            if missing > 0:
                # External-content FTS5: the 'rebuild' command re-indexes
                # every row referenced by the content_rowid.  Cheaper
                # alternatives (per-row INSERT) silently no-op on external
                # content, so 'rebuild' is the only reliable path.
                conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                conn.commit()
        return missing

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
        chunks: "list[TextChunk] | list[tuple[str | None, str]]",
        embedder_ids: list[int] | None = None,
    ) -> int:
        """Insert or update a document and its chunks.

        On re-ingest we UPDATE chunks in-place where the content_hash
        matches (preserving the chunk_id and therefore the embedding rows)
        rather than DELETE-then-INSERT all chunks.  Only genuinely removed
        chunks are deleted, and only truly new chunks are inserted.

        Accepts either :class:`TextChunk` instances (production path —
        persists ``metadata``/``role``/``token_count``) or legacy
        ``(heading, text)`` 2-tuples (defaults metadata to ``{}``).
        """
        # Phase D housekeeping (HK-2): normalize at the boundary so the
        # rest of this method can assume TextChunk shape.
        norm_chunks: list[TextChunk] = [_coerce_to_textchunk(c) for c in chunks]

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
            new_chunk_hashes = {chunk_content_hash(c.text) for c in norm_chunks}

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

            for i, chunk in enumerate(norm_chunks):
                chunk_hash = chunk_content_hash(chunk.text)
                meta_json = json.dumps(chunk.metadata or {})
                prior_id = reusable.get(chunk_hash)
                if prior_id is not None and prior_id not in used_prior_ids:
                    # Update-in-place: keep chunk_id (preserves embedding rows).
                    # HK-2: also refresh metadata/role/token_count so
                    # extractor-emitted labels propagate on re-ingest.
                    self._execute(
                        """
                        UPDATE chunks
                        SET chunk_index = ?, heading = ?, text = ?,
                            metadata = ?, role = ?, token_count = ?
                        WHERE id = ?
                        """,
                        (
                            i,
                            chunk.heading,
                            chunk.text,
                            meta_json,
                            chunk.role,
                            chunk.token_count,
                            prior_id,
                        ),
                    )
                    used_prior_ids.add(prior_id)
                else:
                    # Insert new chunk
                    row = self._execute(
                        """
                        INSERT INTO chunks
                        (document_id, chunk_index, heading, text, metadata,
                         role, token_count, content_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        RETURNING id
                        """,
                        (
                            doc_id,
                            i,
                            chunk.heading,
                            chunk.text,
                            meta_json,
                            chunk.role,
                            chunk.token_count,
                            chunk_hash,
                        ),
                    )
                    new_chunk_id = row[0]["id"]
                    if embedder_ids:
                        self._copy_reusable_embeddings(
                            new_chunk_id, chunk_hash, embedder_ids, cache
                        )

            # Phase D / Wave 3 — persist extractor-emitted labels on the
            # document row. Idempotent.
            self._apply_document_labels(doc_id, doc)

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
        for i, chunk in enumerate(norm_chunks):
            chunk_hash = chunk_content_hash(chunk.text)
            meta_json = json.dumps(chunk.metadata or {})
            row = self._execute(
                """
                INSERT INTO chunks
                (document_id, chunk_index, heading, text, metadata,
                 role, token_count, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    doc_id,
                    i,
                    chunk.heading,
                    chunk.text,
                    meta_json,
                    chunk.role,
                    chunk.token_count,
                    chunk_hash,
                ),
            )
            if embedder_ids:
                self._copy_reusable_embeddings(row[0]["id"], chunk_hash, embedder_ids, cache)

        # Phase D / Wave 3 — persist extractor-emitted labels on the
        # document row. Idempotent.
        self._apply_document_labels(doc_id, doc)

        return doc_id

    def _apply_document_labels(self, doc_id: int, doc: "RawDocument") -> None:
        """Persist ``doc.labels`` against the ``document_labels`` junction.

        Mirrors :meth:`PostgresBackend._apply_document_labels`. Forward-
        compatible with ``ExtractedDocument.labels`` emitted by the
        Phase D multi-format extractor stack. Tolerates a missing or
        empty labels list silently; per-label failures are logged at
        DEBUG and skipped so the rest of the upsert stays atomic.
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
        chunked_messages: "list[list[TextChunk]] | list[list[tuple[str | None, str]]]",
    ) -> int:
        """Insert or update a conversation and its messages/chunks.

        Phase D housekeeping (HK-2): ``chunked_messages`` accepts either
        :class:`TextChunk` lists (preferred) or legacy
        ``(heading, text)`` 2-tuple lists, normalized at the boundary.

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
            for chunk_idx, raw_chunk in enumerate(chunks_in_msg):
                chunk = _coerce_to_textchunk(raw_chunk)
                chunk_hash = chunk_content_hash(chunk.text)
                meta_json = json.dumps(chunk.metadata or {})
                # The message role wins over chunk.role for conversation
                # chunks — chunk-level role is reserved for non-chat
                # chunkers. Token count is optional and may be None.
                self._execute(
                    """
                    INSERT INTO chunks
                    (conversation_id, message_id, chunk_index, heading, text,
                     metadata, role, token_count, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conv_id,
                        message_id,
                        chunk_idx,
                        chunk.heading,
                        chunk.text,
                        meta_json,
                        message_role,
                        chunk.token_count,
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
        self,
        embedder_id: int,
        limit: int = 1024,
        *,
        extensions: list[str] | None = None,
        after_id: int | None = None,
    ) -> "Iterator[tuple[int, str, str]]":
        """Return chunks that have no embedding for the given embedder.

        Mirrors ``PostgresBackend.chunks_missing_embedding`` — PR #81
        widened the tuple to ``(chunk_id, text, source_uri)`` so the
        routing layer can pick the right specialist / catchall per chunk
        via :func:`corpus_forge.embedders.routing.claims`.

        Post-#81 bugfix: ``extensions`` pushes a case-insensitive suffix
        allow-list into SQL so the paging caller doesn't re-fetch the
        same non-matching first page forever. See
        :func:`corpus_forge.backends.base.normalize_extensions_filter`.

        - Unknown ``embedder_id`` → returns empty (no table to query).
        - Results are ordered by ``chunks.id`` and capped by ``limit``.

        Args:
            embedder_id: Primary key from the ``embedders`` table.
            limit: Maximum number of rows to return (default 1024).
            extensions: Optional case-insensitive suffix allow-list.
        """
        embedder_rows = self._execute(
            "SELECT table_name FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            return

        table_name = embedder_rows[0]["table_name"]

        # JOIN documents AND conversations (chunks XOR the two parents)
        # so the route layer has source_uri without a second query.
        # COALESCE falls through to '' for the (defensive) orphan case.
        norm_exts = normalize_extensions_filter(extensions)
        ext_clause = ""
        ext_params: tuple = ()
        if norm_exts:
            # SQLite ``LIKE`` is ASCII case-insensitive by default but we
            # wrap both sides in ``lower()`` for explicit parity with the
            # Postgres implementation.  ``LIKE`` patterns use ``?``
            # placeholders here (vs. ``%s`` on psycopg).
            like_clauses = " OR ".join(
                "lower(COALESCE(d.source_uri, cv.source_uri, '')) LIKE ?" for _ in norm_exts
            )
            ext_clause = f" AND ({like_clauses})"
            ext_params = tuple(f"%{e}" for e in norm_exts)

        # Forward-progress cursor (see base.py docstring).
        cursor_clause = ""
        cursor_params: tuple = ()
        if after_id is not None:
            cursor_clause = " AND c.id > ?"
            cursor_params = (after_id,)

        rows = self._execute(
            f"SELECT c.id, c.text, "
            f"  COALESCE(d.source_uri, cv.source_uri, '') AS source_uri "
            f"FROM chunks c "
            f"LEFT JOIN documents d ON d.id = c.document_id "
            f"LEFT JOIN conversations cv ON cv.id = c.conversation_id "
            f"WHERE NOT EXISTS ("
            f"  SELECT 1 FROM {table_name} e"
            f"  WHERE e.chunk_id = c.id AND e.embedder_id = ?"
            f"){ext_clause}{cursor_clause} "
            f"ORDER BY c.id LIMIT ?",
            (embedder_id, *ext_params, *cursor_params, limit),
        )
        for row in rows:
            yield (row["id"], row["text"], row["source_uri"] or "")

    # ── Phase L Wave 6 — embedder-fingerprint helpers ─────────────────────

    def find_embedder_row_by_name(self, name: str) -> dict | None:
        """Return the ``embedders`` row for ``name`` (or None).

        Phase L Wave 6 — mirror of
        :meth:`PostgresBackend.find_embedder_row_by_name`.  SQLite
        stores ``config`` as a JSON string and bools as INTEGER 0/1; we
        decode both shapes on the way out so the helper returns a
        callable-friendly dict.
        """

        rows = self._execute(
            """
            SELECT id, name, provider, model_id, dimension, normalized,
                   distance, active, table_name, config
              FROM embedders
             WHERE name = ?
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
        row["normalized"] = bool(row.get("normalized"))
        row["active"] = bool(row.get("active"))
        return row

    def count_existing_embeddings(self, embedder: int | str) -> int:
        """Count embedding rows already written for ``embedder``.

        Phase L Wave 6 — mirror of
        :meth:`PostgresBackend.count_existing_embeddings`.  Resolves the
        per-embedder table via ``embedders.table_name`` then runs
        ``SELECT COUNT(*) FROM <table> WHERE embedder_id = ?``.  Returns
        0 when the embedder row is missing (never raises).
        """

        if isinstance(embedder, int):
            row_q = "SELECT id, table_name FROM embedders WHERE id = ?"
        else:
            row_q = "SELECT id, table_name FROM embedders WHERE name = ?"
        rows = self._execute(row_q, (embedder,))
        if not rows:
            return 0
        embedder_id = rows[0]["id"]
        table_name = rows[0]["table_name"]
        # ``table_name`` is synthesised from a sanitised embedder name in
        # :meth:`register_embedder` — safe to f-string.
        count_rows = self._execute(
            f"SELECT COUNT(*) AS n FROM {table_name} WHERE embedder_id = ?",
            (embedder_id,),
        )
        return int(count_rows[0]["n"]) if count_rows else 0

    def update_embedder_config_blob(self, embedder: int | str, config_blob: dict) -> None:
        """Update the ``embedders.config`` JSON for ``embedder``.

        Phase L Wave 6 — mirror of
        :meth:`PostgresBackend.update_embedder_config_blob`.  SQLite
        stores ``config`` as a JSON-serialised TEXT column so we encode
        with :func:`json.dumps` here.
        """

        config_json = json.dumps(config_blob)
        if isinstance(embedder, int):
            self._execute(
                "UPDATE embedders SET config = ? WHERE id = ?",
                (config_json, embedder),
            )
        else:
            self._execute(
                "UPDATE embedders SET config = ? WHERE name = ?",
                (config_json, embedder),
            )

    def count_chunks_missing_embedding(
        self,
        embedder_id: int,
        *,
        extensions: list[str] | None = None,
    ) -> int:
        """Total number of chunks missing an embedding for ``embedder_id``.

        Phase L Wave 4 — mirrors :meth:`PostgresBackend.count_chunks_missing_embedding`.
        Unknown ``embedder_id`` → 0 (no table to count).

        Post-PR-#81 bugfix: accepts ``extensions=`` for symmetry with
        :meth:`chunks_missing_embedding`.  Counts only chunks whose
        ``COALESCE(documents.source_uri, conversations.source_uri, '')``
        ends with one of the normalised extensions; without this the
        embed progress bar over-reports work for specialist embedders.
        """
        embedder_rows = self._execute(
            "SELECT table_name FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            return 0
        table_name = embedder_rows[0]["table_name"]

        norm_exts = normalize_extensions_filter(extensions)
        # The count query JOINs documents + conversations so the LIKE
        # filter can reference the same COALESCE expression as
        # :meth:`chunks_missing_embedding` — keep the two queries in
        # lockstep or the progress bar lies.
        if norm_exts:
            like_clauses = " OR ".join(
                "lower(COALESCE(d.source_uri, cv.source_uri, '')) LIKE ?" for _ in norm_exts
            )
            ext_clause = f" AND ({like_clauses})"
            ext_params = tuple(f"%{e}" for e in norm_exts)
        else:
            ext_clause = ""
            ext_params = ()

        rows = self._execute(
            f"SELECT COUNT(*) AS n FROM chunks c"
            f" LEFT JOIN documents d ON d.id = c.document_id"
            f" LEFT JOIN conversations cv ON cv.id = c.conversation_id"
            f" WHERE NOT EXISTS ("
            f"   SELECT 1 FROM {table_name} e"
            f"   WHERE e.chunk_id = c.id AND e.embedder_id = ?"
            f" ){ext_clause}",
            (embedder_id, *ext_params),
        )
        return int(rows[0]["n"]) if rows else 0

    def pending_documents(
        self, *, dataset_id: int | None = None, limit: int = 5
    ) -> tuple[int, list[str]]:
        """Documents that have no chunks yet — count + sample source URIs.

        Phase L Wave 4 — mirrors :meth:`PostgresBackend.pending_documents`.
        """
        if dataset_id is None:
            count_rows = self._execute(
                "SELECT COUNT(*) AS n FROM documents d"
                " WHERE NOT EXISTS ("
                "   SELECT 1 FROM chunks c WHERE c.document_id = d.id"
                " )"
            )
            sample_rows = self._execute(
                "SELECT d.source_uri FROM documents d"
                " WHERE NOT EXISTS ("
                "   SELECT 1 FROM chunks c WHERE c.document_id = d.id"
                " ) ORDER BY d.id LIMIT ?",
                (limit,),
            )
        else:
            count_rows = self._execute(
                "SELECT COUNT(*) AS n FROM documents d"
                " WHERE d.dataset_id = ? AND NOT EXISTS ("
                "   SELECT 1 FROM chunks c WHERE c.document_id = d.id"
                " )",
                (dataset_id,),
            )
            sample_rows = self._execute(
                "SELECT d.source_uri FROM documents d"
                " WHERE d.dataset_id = ? AND NOT EXISTS ("
                "   SELECT 1 FROM chunks c WHERE c.document_id = d.id"
                " ) ORDER BY d.id LIMIT ?",
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
        """Register a multi-modal embedder + provision its image table.

        Same pattern as :meth:`register_embedder` but flags the row
        ``image=1`` and creates an ``image_embeddings_<name>`` table.
        Uses the sqlite-vec ``vec0`` virtual table when available; falls
        back to a plain ``BLOB`` table otherwise.
        """
        safe_name = name.replace("-", "_")
        table_name = f"image_embeddings_{safe_name}"
        config_json = json.dumps({"provider": "multimodal", "model_id": model_id})

        existing = self._execute(
            "SELECT id FROM embedders WHERE name = ?",
            (name,),
        )
        if existing:
            embedder_id: int = existing[0]["id"]
            self._execute(
                """
                UPDATE embedders
                SET provider = ?, model_id = ?, dimension = ?,
                    normalized = ?, distance = ?, active = ?,
                    table_name = ?, config = ?, image = 1
                WHERE id = ?
                """,
                (
                    "multimodal",
                    model_id,
                    dimension,
                    1,
                    "cosine",
                    1,
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
                   active, table_name, config, image)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    name,
                    "multimodal",
                    model_id,
                    dimension,
                    1,
                    "cosine",
                    1,
                    table_name,
                    config_json,
                ),
            )
            id_row = self._execute(
                "SELECT id FROM embedders WHERE name = ?",
                (name,),
            )
            embedder_id = id_row[0]["id"]

        if SQLITE_VEC_AVAILABLE:
            self._execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}"
                f" USING vec0(chunk_id INTEGER PRIMARY KEY,"
                f" embedder_id INTEGER,"
                f" embedding FLOAT[{dimension}])"
            )
        else:
            self._execute(
                f"CREATE TABLE IF NOT EXISTS {table_name}"
                f" (chunk_id INTEGER PRIMARY KEY,"
                f" embedder_id INTEGER NOT NULL,"
                f" embedding BLOB NOT NULL,"
                f" model TEXT,"
                f" created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                f" FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,"
                f" FOREIGN KEY (embedder_id) REFERENCES embedders(id))"
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
        import numpy as np  # noqa: PLC0415

        embedder_rows = self._execute(
            "SELECT table_name, model_id FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            raise ValueError(f"Embedder with ID {embedder_id} not found")

        table_name = embedder_rows[0]["table_name"]
        model_id = embedder_rows[0]["model_id"]

        if SQLITE_VEC_AVAILABLE:
            from sqlite_vec import serialize_float32  # noqa: PLC0415

            for chunk_id, embedding in pairs:
                vec = np.asarray(embedding, dtype=np.float32)
                blob = serialize_float32(vec.tolist())
                self._execute(
                    f"DELETE FROM {table_name} WHERE chunk_id = ?",
                    (chunk_id,),
                )
                self._execute(
                    f"INSERT INTO {table_name} (chunk_id, embedder_id, embedding) VALUES (?, ?, ?)",
                    (chunk_id, embedder_id, blob),
                )
        else:
            for chunk_id, embedding in pairs:
                vec = np.asarray(embedding, dtype=np.float32)
                blob = vec.tobytes()
                self._execute(
                    f"INSERT OR REPLACE INTO {table_name}"
                    f" (chunk_id, embedder_id, embedding, model)"
                    f" VALUES (?, ?, ?, ?)",
                    (chunk_id, embedder_id, blob, model_id),
                )

    def image_chunks_missing_embedding(
        self, embedder_id: int, *, limit: int = 1024
    ) -> "Iterator[tuple[int, dict]]":
        """Yield ``(chunk_id, metadata_dict)`` for image chunks needing embeddings."""
        embedder_rows = self._execute(
            "SELECT table_name FROM embedders WHERE id = ?",
            (embedder_id,),
        )
        if not embedder_rows:
            return
        table_name = embedder_rows[0]["table_name"]

        rows = self._execute(
            f"SELECT c.id, c.text, c.metadata FROM chunks c"
            f" JOIN documents d ON d.id = c.document_id"
            f" JOIN document_labels dl ON dl.document_id = d.id"
            f" JOIN labels l ON l.id = dl.label_id"
            f" WHERE NOT EXISTS ("
            f"   SELECT 1 FROM {table_name} e"
            f"   WHERE e.chunk_id = c.id AND e.embedder_id = ?"
            f" )"
            f" AND l.namespace = 'format' AND l.value = 'image'"
            f" ORDER BY c.id LIMIT ?",
            (embedder_id, limit),
        )
        for row in rows:
            raw_meta = row["metadata"]
            if isinstance(raw_meta, str):
                try:
                    meta = json.loads(raw_meta)
                except json.JSONDecodeError:
                    meta = {}
            elif isinstance(raw_meta, dict):
                meta = raw_meta
            else:
                meta = {}
            if "text" not in meta:
                meta = {**meta, "text": row["text"]}
            yield (row["id"], meta)

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
        chunk_ids: "frozenset[int] | None" = None,
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

        Phase N Wave 3 — ``chunk_ids`` filter:

        - ``None`` (default): pre-Wave-3 behaviour.
        - empty ``frozenset()``: returns ``[]`` immediately.
        - non-empty: filtered in Python after the vec0 MATCH step (vec0
          can't accept an IN-list predicate inside the MATCH clause).
          We over-pull vec0's ``k`` argument so the filtered set still
          fills the caller's ``k`` when possible.
        """
        # Local import — Hit lives in retrieval.types; pulled in lazily to keep
        # the backend module import-light.
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        # Phase N Wave 3 — empty filter short-circuits.
        if chunk_ids is not None and not chunk_ids:
            return []

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
        #
        # When a chunk_ids filter is in play, over-pull from vec0 so the
        # post-filter slice can still fill ``k`` results.  Cap at 5x the
        # caller's k for the over-pull (good enough for the Wave 3 fast
        # tier's top-200 → main top-10 pipeline).
        vec_k = k if chunk_ids is None else max(k, min(k * 5, len(chunk_ids) + k))
        match_rows = self._execute(
            f"SELECT chunk_id, distance FROM {table_name}"
            " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (blob, vec_k),
        )
        if not match_rows:
            return []

        all_chunk_ids = [int(r["chunk_id"]) for r in match_rows]
        # Apply the Wave 3 chunk_ids filter in Python (vec0 can't see
        # arbitrary IN-lists inside the MATCH clause).
        if chunk_ids is not None:
            filtered_ids = [cid for cid in all_chunk_ids if cid in chunk_ids]
            # Truncate to the caller's k AFTER the filter.
            filtered_ids = filtered_ids[:k]
        else:
            filtered_ids = all_chunk_ids
        if not filtered_ids:
            return []
        distance_by_id = {int(r["chunk_id"]): float(r["distance"]) for r in match_rows}

        # Step 2: pull chunk + document metadata.  LEFT JOIN documents +
        # conversations so we can resolve the chunk's dataset_id even for
        # message chunks (where document_id IS NULL).  Filter by dataset_id
        # if the caller requested it.
        placeholders = ",".join("?" * len(filtered_ids))
        params: tuple = (*filtered_ids,)
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
        for cid in filtered_ids:
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
        chunk_ids: "frozenset[int] | None" = None,
    ) -> "list":
        """BM25-ranked lexical search over the ``chunks_fts`` FTS5 virtual table.

        - FTS5's ``bm25()`` returns *lower* values for *better* matches; we
          normalise to higher-is-better via ``score = 1 / (1 + bm25)``.
        - ``LEFT JOIN documents`` to surface ``source_uri`` / ``title`` for
          document chunks; message chunks have ``document_id IS NULL`` and
          carry both as ``None``.
        - Dataset filter is a SARGable predicate on ``chunks.dataset_id``.
        - **Query sanitisation**: FTS5's MATCH parser is opinionated about
          punctuation (``?`` / quotes) and bare tokens that collide with
          column names (``host``, ``k`` etc.).  We tokenise to alnum runs
          and OR-join them so natural-language queries dispatch cleanly
          regardless of casing or punctuation.  Discovered when the R3
          eval CLI ran the bundled ``forge_self`` gold set and crashed
          on the FIRST question with a trailing ``?``.

        Phase N Wave 3 — ``chunk_ids`` filter:

        - ``None`` (default): pre-Wave-3 behaviour.
        - empty ``frozenset()``: returns ``[]`` immediately.
        - non-empty: ``AND c.id IN (...)`` restricts the FTS join to the
          candidate pool.
        """
        import re  # noqa: PLC0415 — local import keeps the cold-start lean

        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        # Phase N Wave 3 — empty filter short-circuits.
        if chunk_ids is not None and not chunk_ids:
            return []

        # Sanitise: alnum runs of >= min_token_chars chars, OR-joined.
        # Empty after tokenisation → no results (saves an FTS5 round-trip).
        min_token_chars = 2
        tokens = [t for t in re.findall(r"\w+", query) if len(t) >= min_token_chars]
        if not tokens:
            return []
        match_expr = " OR ".join(tokens)

        params: tuple = (match_expr,)
        ds_filter = ""
        if dataset_id is not None:
            ds_filter = " AND COALESCE(d.dataset_id, cv.dataset_id) = ?"
            params = (*params, dataset_id)
        chunk_filter = ""
        if chunk_ids is not None:
            placeholders = ",".join("?" * len(chunk_ids))
            chunk_filter = f" AND c.id IN ({placeholders})"
            params = (*params, *chunk_ids)
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
            WHERE chunks_fts MATCH ?{ds_filter}{chunk_filter}
            ORDER BY bm25_score
            LIMIT ?
            """,
            params,
        )

        hits: list = []
        for r in rows:
            # SQLite FTS5 bm25() returns a *non-positive* real value where
            # values closer to 0 indicate stronger relevance.  Negate to get
            # a non-negative relevance score, then squash to [0, 1] via
            # x/(1+x), which is monotonic and bounded.
            bm25 = float(r["bm25_score"]) if r["bm25_score"] is not None else 0.0
            relevance = -bm25 if bm25 < 0 else bm25
            score = relevance / (1.0 + relevance)
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

        Additive (agent-chunk-explorer): also includes ``prev_chunk_id``
        and ``next_chunk_id`` (``int | None``) so callers can chain
        follow-up lookups without an extra query.
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
                "SELECT id FROM chunks "
                "WHERE document_id = ? AND chunk_index < ? "
                "ORDER BY chunk_index DESC LIMIT 1",
                (doc_id, idx),
            )
            nxt = self._execute(
                "SELECT id FROM chunks "
                "WHERE document_id = ? AND chunk_index > ? "
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
                "SELECT id FROM chunks "
                "WHERE conversation_id = ? "
                "  AND (message_id < ? OR (message_id = ? AND chunk_index < ?)) "
                "ORDER BY message_id DESC, chunk_index DESC LIMIT 1",
                (convo_id, msg_id, msg_id, idx),
            )
            nxt = self._execute(
                "SELECT id FROM chunks "
                "WHERE conversation_id = ? "
                "  AND (message_id > ? OR (message_id = ? AND chunk_index > ?)) "
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
            "FROM chunks WHERE id = ?",
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
            "FROM chunks c "
            "LEFT JOIN documents d ON d.id = c.document_id "
            "LEFT JOIN conversations cv ON cv.id = c.conversation_id"
        )
        out: list[dict] = []
        if anchor["document_id"] is not None:
            doc_id = anchor["document_id"]
            idx = anchor["chunk_index"]
            if before > 0:
                prev_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.document_id = ? AND c.chunk_index < ? "
                    f"ORDER BY c.chunk_index DESC LIMIT ?",
                    (doc_id, idx, before),
                )
                out.extend(reversed([dict(r) for r in prev_rows]))
            if after > 0:
                next_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.document_id = ? AND c.chunk_index > ? "
                    f"ORDER BY c.chunk_index ASC LIMIT ?",
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
                    f"WHERE c.conversation_id = ? "
                    f"  AND (c.message_id < ? OR (c.message_id = ? AND c.chunk_index < ?)) "
                    f"ORDER BY c.message_id DESC, c.chunk_index DESC LIMIT ?",
                    (convo_id, msg_id, msg_id, idx, before),
                )
                out.extend(reversed([dict(r) for r in prev_rows]))
            if after > 0:
                next_rows = self._execute(
                    f"SELECT {select_cols} {joins} "
                    f"WHERE c.conversation_id = ? "
                    f"  AND (c.message_id > ? OR (c.message_id = ? AND c.chunk_index > ?)) "
                    f"ORDER BY c.message_id ASC, c.chunk_index ASC LIMIT ?",
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
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            WHERE c.document_id = ?
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
            "SELECT id, chunk_index, content_hash FROM chunks "
            "WHERE document_id = ? ORDER BY chunk_index",
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

        for pr in prior_rows:
            if pr["content_hash"] not in new_chunk_hashes:
                self._execute("DELETE FROM chunks WHERE id = ?", (pr["id"],))

        used_prior_ids: set[int] = set()
        cache: dict[tuple[str, int], int] = {}

        for i, chunk in enumerate(norm_chunks):
            chunk_hash = chunk_content_hash(chunk.text)
            meta_json = json.dumps(chunk.metadata or {})
            prior_id = reusable.get(chunk_hash)
            if prior_id is not None and prior_id not in used_prior_ids:
                self._execute(
                    """
                    UPDATE chunks
                    SET chunk_index = ?, heading = ?, text = ?,
                        metadata = ?, role = ?, token_count = ?
                    WHERE id = ?
                    """,
                    (
                        i,
                        chunk.heading,
                        chunk.text,
                        meta_json,
                        chunk.role,
                        chunk.token_count,
                        prior_id,
                    ),
                )
                used_prior_ids.add(prior_id)
            else:
                row = self._execute(
                    """
                    INSERT INTO chunks
                    (document_id, chunk_index, heading, text, metadata,
                     role, token_count, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        document_id,
                        i,
                        chunk.heading,
                        chunk.text,
                        meta_json,
                        chunk.role,
                        chunk.token_count,
                        chunk_hash,
                    ),
                )
                new_chunk_id = row[0]["id"]
                if embedder_ids:
                    self._copy_reusable_embeddings(new_chunk_id, chunk_hash, embedder_ids, cache)

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
            "SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        )
        return [r["text"] for r in rows]

    def get_document_chunk_metadatas(self, document_id: int) -> "list[dict]":
        """Return the metadata dicts of all chunks attached to ``document_id``.

        Phase F (F-04): used by the ``rechunk`` CLI idempotency check.
        Returns one dict per chunk in chunk-index order; an empty dict
        is substituted for rows whose ``metadata`` column is NULL or
        malformed JSON.
        """
        rows = self._execute(
            "SELECT metadata FROM chunks WHERE document_id = ? ORDER BY chunk_index",
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

        Non-string / unmatched inputs (e.g. ``""``, ``None``, ``b"…"``) return
        ``None`` rather than raising; SQLite's parameter binding simply fails
        to match anything.
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
            WHERE c.content_hash = ?
            ORDER BY c.id ASC
            LIMIT 1
            """,
            (content_hash,),
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

    # ── F-02 write helpers ────────────────────────────────────────────────────

    def get_entity_metadata(self, entity_type: str, entity_id: int) -> dict:
        """Return the current metadata dict for a document or conversation entity.

        Uses the backend's native execute path (? placeholders).  Returns {}
        when the entity is not found or has NULL/empty metadata.
        """
        table = _ENTITY_TABLE_MAP[entity_type]
        rows = self._execute(f"SELECT metadata FROM {table} WHERE id = ?", (entity_id,))
        if not rows or rows[0]["metadata"] is None:
            return {}
        raw = rows[0]["metadata"]
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)

    def get_entity_description(self, entity_type: str, entity_id: int) -> "str | None":
        """Return the current description for a document or conversation entity.

        Uses the backend's native execute path (? placeholders).  Returns
        None when the entity is not found or has NULL description.
        """
        table = _ENTITY_TABLE_MAP[entity_type]
        rows = self._execute(f"SELECT description FROM {table} WHERE id = ?", (entity_id,))
        if not rows:
            return None
        return rows[0]["description"]

    def count_messages(self, conversation_id: int) -> int:
        """Return the current message count for a conversation.

        Returns 0 for an empty (or missing) conversation.
        """
        rows = self._execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS m FROM messages WHERE conversation_id = ?",
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

        with self._get_connection() as conn:
            # Upsert the canonical label row, ignoring if it already exists.
            conn.execute(
                "INSERT OR IGNORE INTO labels (namespace, value) VALUES (?, ?)",
                (namespace, value),
            )
            label_row = conn.execute(
                "SELECT id FROM labels WHERE namespace = ? AND value = ?",
                (namespace, value),
            ).fetchone()
            label_id: int = label_row["id"]

            # Check whether the junction row already exists.
            existing = conn.execute(
                f"SELECT 1 FROM {junction_table}"
                f" WHERE {fk_col} = ? AND label_id = ? AND source = ?",
                (entity_id, label_id, source),
            ).fetchone()
            created = existing is None

            if created:
                if entity_type in ("chunk", "document"):
                    # Phase E (C-04 + C-06): ``document_labels`` gained
                    # an optional ``confidence REAL`` column mirroring
                    # ``chunk_labels.confidence``. The same INSERT shape
                    # now covers both entities.
                    conn.execute(
                        f"INSERT OR IGNORE INTO {junction_table}"
                        f" ({fk_col}, label_id, confidence, source)"
                        f" VALUES (?, ?, ?, ?)",
                        (entity_id, label_id, confidence, source),
                    )
                else:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {junction_table}"
                        f" ({fk_col}, label_id, source)"
                        f" VALUES (?, ?, ?)",
                        (entity_id, label_id, source),
                    )

            conn.commit()

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

        with self._get_connection() as conn:
            label_row = conn.execute(
                "SELECT id FROM labels WHERE namespace = ? AND value = ?",
                (namespace, value),
            ).fetchone()
            if label_row is None:
                return False

            label_id = label_row["id"]
            cursor = conn.execute(
                f"DELETE FROM {junction_table} WHERE {fk_col} = ? AND label_id = ?",
                (entity_id, label_id),
            )
            deleted = cursor.rowcount > 0
            conn.commit()

        return deleted

    def patch_metadata(
        self,
        entity_type: str,
        entity_id: int,
        key: str,
        value: Any,
    ) -> tuple[dict, dict]:
        """Merge a single ``key: value`` pair into the entity's metadata JSON.

        Returns ``(before, after)`` as Python dicts.  Uses SQLite's
        ``json_patch`` function (available since 3.38, which we require).
        """
        if entity_type not in _LABEL_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {entity_type!r} not valid; must be one of {_LABEL_ENTITY_TYPES}"
            )

        table = _ENTITY_TABLE_MAP[entity_type]

        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT metadata FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()
            before: dict = json.loads(row["metadata"]) if row and row["metadata"] else {}

            patch_json = json.dumps({key: value})
            conn.execute(
                f"UPDATE {table} SET metadata = json_patch(metadata, ?) WHERE id = ?",
                (patch_json, entity_id),
            )
            conn.commit()

            after_row = conn.execute(
                f"SELECT metadata FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()
            after: dict = (
                json.loads(after_row["metadata"]) if after_row and after_row["metadata"] else {}
            )

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

        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT description FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()
            before: str | None = row["description"] if row else None

            conn.execute(
                f"UPDATE {table} SET description = ? WHERE id = ?",
                (text, entity_id),
            )
            conn.commit()

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
        that ``search_lexical`` (which queries the ``chunks_fts`` FTS5 table)
        can find appended content immediately.
        """
        meta_json = json.dumps(metadata or {})
        started_str: str | None = None
        if started_at is not None:
            started_str = started_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

        # Generate a unique source_uri and content_hash for this new conversation.
        import hashlib  # noqa: PLC0415
        import uuid as _uuid  # noqa: PLC0415

        source_uri = f"append://{_uuid.uuid4()}"
        content_hash = hashlib.sha256(source_uri.encode()).hexdigest()

        with self._get_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO conversations
                  (dataset_id, source_uri, content_hash, title, started_at,
                   message_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    dataset_id,
                    source_uri,
                    content_hash,
                    title,
                    started_str,
                    len(messages),
                    meta_json,
                ),
            ).fetchone()
            conv_id: int = row[0]

            # Insert messages and collect their ids for chunk linkage.
            message_ids: list[int] = []
            for i, msg in enumerate(messages):
                tool_calls = msg.get("tool_calls")
                tool_results = msg.get("tool_results")
                ts_val = msg.get("ts")
                msg_meta = msg.get("metadata", {})
                msg_row = conn.execute(
                    """
                    INSERT INTO messages
                      (conversation_id, turn_index, role, content,
                       tool_calls, tool_results, ts, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        conv_id,
                        i,
                        msg["role"],
                        msg["content"],
                        json.dumps(tool_calls) if tool_calls is not None else None,
                        json.dumps(tool_results) if tool_results is not None else None,
                        ts_val,
                        json.dumps(msg_meta),
                    ),
                ).fetchone()
                message_ids.append(msg_row[0])

            # Insert one chunk per non-empty message (mirrors the per_message daemon path).
            for i, msg in enumerate(messages):
                text = msg.get("content", "")
                if not text.strip():
                    continue
                role = msg.get("role", "")
                ch = chunk_content_hash(text)
                conn.execute(
                    """
                    INSERT INTO chunks
                      (conversation_id, message_id, chunk_index, text,
                       role, metadata, content_hash)
                    VALUES (?, ?, ?, ?, ?, '{}', ?)
                    """,
                    (conv_id, message_ids[i], 0, text, role, ch),
                )

            conn.commit()

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

        Computes ``turn_index`` as ``MAX(turn_index) + 1`` under a write lock
        so concurrent callers get distinct indexes.  Also inserts a chunk row
        so the message is immediately searchable via the FTS5 index.

        Returns ``(message_id, turn_index)``.
        """
        ts_str: str | None = None
        if ts is not None:
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        meta_json = json.dumps(metadata or {})
        tc_json = json.dumps(tool_calls) if tool_calls is not None else None
        tr_json = json.dumps(tool_results) if tool_results is not None else None

        # Serialise concurrent append_message calls within the same process
        # using the instance-level threading.Lock (same pattern as lock_source).
        # This avoids "database table is locked" from SQLite when two threads
        # both attempt BEGIN IMMEDIATE on the shared-cache in-memory DB.
        if not hasattr(self, "_write_lock"):
            object.__setattr__(self, "_write_lock", threading.Lock())

        with self._write_lock, self._get_connection() as conn:
            max_row = conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) AS m FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            turn_index: int = int(max_row["m"]) + 1

            cursor = conn.execute(
                """
                    INSERT INTO messages
                      (conversation_id, turn_index, role, content,
                       tool_calls, tool_results, ts, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                (
                    conversation_id,
                    turn_index,
                    role,
                    content,
                    tc_json,
                    tr_json,
                    ts_str,
                    meta_json,
                ),
            )
            msg_row = cursor.fetchone()
            message_id: int = msg_row[0]

            # Insert a chunk so the message is indexed by search_lexical.
            if content.strip():
                ch = chunk_content_hash(content)
                conn.execute(
                    """
                    INSERT INTO chunks
                      (conversation_id, message_id, chunk_index, text,
                       role, metadata, content_hash)
                    VALUES (?, ?, ?, ?, ?, '{}', ?)
                    """,
                    (conversation_id, message_id, 0, content, role, ch),
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

        import socket  # noqa: PLC0415

        host = socket.gethostname()
        # feedback.metadata has NOT NULL DEFAULT '{}' — always pass a JSON string.
        meta_json = json.dumps(metadata if metadata is not None else {})

        with self._get_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO feedback
                  (host, entity_type, entity_id, kind, rating, text, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (host, entity_type, entity_id, kind, rating, text, meta_json),
            ).fetchone()
            fb_id: int = row[0]
            conn.commit()

        return fb_id

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

        ``before`` and ``after`` are serialised to JSON text.  Either may be
        ``None`` (stored as SQL NULL).
        """
        before_json: str | None = json.dumps(before) if before is not None else None
        after_json: str | None = json.dumps(after) if after is not None else None

        with self._get_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO mcp_audit
                  (host, client, session_id, tool, entity_type, entity_id,
                   before, after, dry_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    host,
                    client,
                    session_id,
                    tool,
                    entity_type,
                    entity_id,
                    before_json,
                    after_json,
                    1 if dry_run else 0,
                ),
            ).fetchone()
            audit_id: int = row[0]
            conn.commit()

        return audit_id

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
        # Build a UNION of all three junction tables.
        parts: list[str] = []
        params: list[object] = []

        for et, (jt, _fk) in _LABEL_TABLE_MAP.items():
            if entity_type is not None and et != entity_type:
                continue
            parts.append(
                f"SELECT '{et}' AS entity_type, l.namespace, l.value, COUNT(*) AS count"
                f" FROM {jt} j JOIN labels l ON l.id = j.label_id"
                + (" WHERE l.namespace = ?" if namespace is not None else "")
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

        Each entity-type bucket issues at most 3 queries regardless of hit count
        — no N+1.
        """
        import dataclasses  # noqa: PLC0415

        if not hits:
            return []

        # Collect chunk_ids (the primary entity type for retrieval hits).
        chunk_ids = [h.chunk_id for h in hits]

        # --- bulk-fetch labels for all chunk_ids ---
        placeholders = ",".join("?" * len(chunk_ids))

        label_rows = self._execute(
            f"""
            SELECT cl.chunk_id, l.namespace, l.value
            FROM chunk_labels cl
            JOIN labels l ON l.id = cl.label_id
            WHERE cl.chunk_id IN ({placeholders})
            """,
            tuple(chunk_ids),
        )
        labels_by_chunk: dict[int, list[tuple[str, str]]] = {cid: [] for cid in chunk_ids}
        for lr in label_rows:
            labels_by_chunk[lr["chunk_id"]].append((lr["namespace"], lr["value"]))

        # --- bulk-fetch descriptions for all chunk_ids ---
        desc_rows = self._execute(
            f"SELECT id, description FROM chunks WHERE id IN ({placeholders})",
            tuple(chunk_ids),
        )
        desc_by_chunk: dict[int, str | None] = dict.fromkeys(chunk_ids)
        for dr in desc_rows:
            desc_by_chunk[dr["id"]] = dr["description"]

        # --- bulk-fetch up to _RECENT_FEEDBACK_LIMIT most-recent feedback per chunk ---
        feedback_rows = self._execute(
            f"""
            SELECT entity_id, kind, rating, text, ts
            FROM feedback
            WHERE entity_type = 'chunk' AND entity_id IN ({placeholders})
            ORDER BY entity_id, id DESC
            """,
            tuple(chunk_ids),
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

    def hydrate_document_metadata(self, document_ids: list[int]) -> list[dict]:
        """Bulk-load labels, description, and recent_feedback for a list of document ids.

        Mirrors :meth:`hydrate_hit_metadata` but operates on the document entity
        type instead of chunks.  Returns one dict per document_id with keys
        ``document_id``, ``labels`` (list of ``(namespace, value)`` tuples),
        ``description`` (``str | None``), and ``recent_feedback`` (list of dicts).

        Issues at most 3 queries regardless of the number of document ids — no N+1.
        """
        if not document_ids:
            return []

        placeholders = ",".join("?" * len(document_ids))

        # --- bulk-fetch labels for all document_ids ---
        label_rows = self._execute(
            f"""
            SELECT dl.document_id, l.namespace, l.value
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE dl.document_id IN ({placeholders})
            """,
            tuple(document_ids),
        )
        labels_by_doc: dict[int, list[tuple[str, str]]] = {did: [] for did in document_ids}
        for lr in label_rows:
            labels_by_doc[lr["document_id"]].append((lr["namespace"], lr["value"]))

        # --- bulk-fetch descriptions for all document_ids ---
        desc_rows = self._execute(
            f"SELECT id, description FROM documents WHERE id IN ({placeholders})",
            tuple(document_ids),
        )
        desc_by_doc: dict[int, str | None] = dict.fromkeys(document_ids)
        for dr in desc_rows:
            desc_by_doc[dr["id"]] = dr["description"]

        # --- bulk-fetch up to _RECENT_FEEDBACK_LIMIT most-recent feedback per document ---
        feedback_rows = self._execute(
            f"""
            SELECT entity_id, kind, rating, text, ts
            FROM feedback
            WHERE entity_type = 'document' AND entity_id IN ({placeholders})
            ORDER BY entity_id, id DESC
            """,
            tuple(document_ids),
        )
        feedback_by_doc: dict[int, list[dict]] = {did: [] for did in document_ids}
        for fr in feedback_rows:
            eid = fr["entity_id"]
            if len(feedback_by_doc[eid]) < _RECENT_FEEDBACK_LIMIT:
                feedback_by_doc[eid].append(
                    {
                        "kind": fr["kind"],
                        "rating": fr["rating"],
                        "text": fr["text"],
                        "ts": fr["ts"],
                    }
                )

        return [
            {
                "document_id": did,
                "labels": labels_by_doc.get(did, []),
                "description": desc_by_doc.get(did),
                "recent_feedback": feedback_by_doc.get(did, []),
            }
            for did in document_ids
        ]

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

        Uses INSERT OR IGNORE so that a duplicate name is silently ignored.
        Returns ``created=True`` when a new row was inserted, ``False`` when
        the name already existed (existing id is returned in both cases).
        """
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM chat_templates WHERE name = ?", (name,)
            ).fetchone()
            if existing is not None:
                return int(existing["id"]), False

            cursor = conn.execute(
                """
                INSERT INTO chat_templates (name, source, jinja, model_id, description, host)
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (name, source, jinja, model_id, description, host),
            )
            row = cursor.fetchone()
            conn.commit()

        return int(row["id"]), True

    def list_chat_templates(self) -> list[dict]:
        """Return all rows from chat_templates as a list of dicts."""
        return self._execute("SELECT * FROM chat_templates ORDER BY id")

    def get_chat_template_by_name(self, name: str) -> dict | None:
        """Return the chat_templates row for *name*, or None if absent."""
        rows = self._execute("SELECT * FROM chat_templates WHERE name = ? LIMIT 1", (name,))
        return rows[0] if rows else None

    # ── G-03 conversation helpers ─────────────────────────────────────────────

    def get_conversation(self, conversation_id: int) -> "dict | None":
        """Return the conversations row for *conversation_id*, or None if absent."""
        rows = self._execute(
            "SELECT * FROM conversations WHERE id = ? LIMIT 1",
            (conversation_id,),
        )
        return rows[0] if rows else None

    def list_conversations_for_dataset(self, dataset_id: int) -> "list[dict]":
        """Return all conversations for *dataset_id* as a list of dicts."""
        return self._execute(
            "SELECT * FROM conversations WHERE dataset_id = ? ORDER BY id",
            (dataset_id,),
        )

    def list_conversation_messages(self, conversation_id: int) -> "list[dict]":
        """Return all messages for *conversation_id* ordered by turn_index.

        Each dict has at minimum: id, conversation_id, turn_index, role, content.
        """
        return self._execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY turn_index",
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

        Uses INSERT OR IGNORE so a duplicate key is silently skipped.
        Returns the id of the existing or newly-created row.
        """
        started_at_str = str(started_at)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO feedback_sessions
                  (client, session_id, host, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (client, session_id, host, started_at_str),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM feedback_sessions WHERE client = ? AND session_id = ?",
                (client, session_id),
            ).fetchone()
        return int(row["id"])

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
        with self._get_connection() as conn:
            row = conn.execute(
                """
                INSERT INTO feedback_events
                  (feedback_session_id, audit_id, feedback_id, entity_type, entity_id)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
                """,
                (feedback_session_id, audit_id, feedback_id, entity_type, entity_id),
            ).fetchone()
            conn.commit()
        return int(row["id"])

    def end_feedback_session(self, client: str, session_id: str) -> bool:
        """Set ended_at on the matching open session row.

        Returns True if a row was updated, False if no open row was found.
        """
        from datetime import UTC, datetime  # noqa: PLC0415

        ended_at = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE feedback_sessions
                SET ended_at = ?
                WHERE client = ? AND session_id = ? AND ended_at IS NULL
                """,
                (ended_at, client, session_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def link_feedback_session_to_conversation(
        self, client: str, session_id: str, conversation_id: int
    ) -> bool:
        """Set feedback_sessions.conversation_id if currently NULL.

        Returns True if a row was updated, False if no matching row or already linked.
        """
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE feedback_sessions
                SET conversation_id = ?
                WHERE client = ? AND session_id = ? AND conversation_id IS NULL
                """,
                (conversation_id, client, session_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_feedback_session_by_key(self, client: str, session_id: str) -> "dict | None":
        """Return the feedback_sessions row for (client, session_id), or None."""
        rows = self._execute(
            "SELECT * FROM feedback_sessions WHERE client = ? AND session_id = ? LIMIT 1",
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
        fields: client, session_id (as session_id), host, and conversation_id.
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
            FROM feedback_events fe
            JOIN feedback_sessions fs ON fs.id = fe.feedback_session_id
            JOIN conversations c ON c.id = fs.conversation_id
            WHERE c.dataset_id = ?
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

        Each row's ``student_messages`` and ``teacher_messages`` columns are
        deserialized from the stored JSON text to Python lists.

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
            placeholders = ",".join("?" * len(include_sources))
            rows = self._execute(
                f"SELECT id, query, student_messages, teacher_messages, target, source,"
                f" dataset_id, trace_id, content_hash"
                f" FROM sdft_demonstrations"
                f" WHERE dataset_id = ? AND source IN ({placeholders})"
                f" ORDER BY id",
                (dataset_id, *include_sources),
            )
        else:
            rows = self._execute(
                "SELECT id, query, student_messages, teacher_messages, target, source,"
                " dataset_id, trace_id, content_hash"
                " FROM sdft_demonstrations"
                " WHERE dataset_id = ?"
                " ORDER BY id",
                (dataset_id,),
            )

        # Deserialize JSON text columns to Python lists.
        for row in rows:
            for col in ("student_messages", "teacher_messages"):
                if isinstance(row.get(col), str):
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        row[col] = json.loads(row[col])
        return rows

    def get_audit_event(self, audit_id: int) -> "dict | None":
        """Return the mcp_audit row for *audit_id*, or None on miss."""
        rows = self._execute(
            "SELECT * FROM mcp_audit WHERE id = ? LIMIT 1",
            (audit_id,),
        )
        if not rows:
            return None
        row = rows[0]
        # before/after are stored as JSON text in SQLite — deserialise them.
        for col in ("before", "after"):
            if isinstance(row.get(col), str):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    row[col] = json.loads(row[col])
        return row

    def get_feedback(self, feedback_id: int) -> "dict | None":
        """Return the feedback row for *feedback_id*, or None on miss."""
        rows = self._execute(
            "SELECT * FROM feedback WHERE id = ? LIMIT 1",
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
                SELECT * FROM messages
                WHERE conversation_id = ? AND ts <= ?
                ORDER BY turn_index
                """,
                (conversation_id, ts),
            )
            if rows:
                return rows
        return self._execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY turn_index",
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

        Mirrors :meth:`PostgresBackend.iter_documents_for_classification`
        (see that method's docstring for the contract).
        """
        from corpus_forge.classifiers.base import (  # noqa: PLC0415
            ClassifiableDocument,
        )

        where_clauses: list[str] = []
        params: list[Any] = []
        if dataset_id is not None:
            where_clauses.append("d.dataset_id = ?")
            params.append(dataset_id)

        if not include_classified:
            where_clauses.append(
                "NOT EXISTS ("
                "  SELECT 1 FROM document_labels dl2"
                "  JOIN labels l2 ON l2.id = dl2.label_id"
                "  WHERE dl2.document_id = d.id"
                "    AND l2.namespace = 'class'"
                "    AND dl2.source LIKE 'classifier:%'"
                ")"
            )

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        doc_rows = self._execute(
            f"""
            SELECT d.id AS document_id, d.source_uri, d.title, d.text, d.metadata
            FROM documents d
            {where_sql}
            ORDER BY d.id
            """,
            tuple(params),
        )
        if not doc_rows:
            return

        ids = [int(r["document_id"]) for r in doc_rows]
        placeholders = ",".join("?" * len(ids))
        label_rows = self._execute(
            f"""
            SELECT dl.document_id, l.namespace, l.value
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE dl.document_id IN ({placeholders})
            """,
            tuple(ids),
        )
        labels_by_doc: dict[int, list[tuple[str, str]]] = {i: [] for i in ids}
        for lr in label_rows:
            labels_by_doc[int(lr["document_id"])].append((lr["namespace"], lr["value"]))

        for row in doc_rows:
            doc_id = int(row["document_id"])
            md_raw = row["metadata"]
            if isinstance(md_raw, str):
                try:
                    md = json.loads(md_raw)
                except (TypeError, ValueError):
                    md = {}
            elif isinstance(md_raw, dict):
                md = md_raw
            else:
                md = {}
            yield ClassifiableDocument(
                document_id=doc_id,
                source_uri=row["source_uri"],
                title=row.get("title") if hasattr(row, "get") else row["title"],
                text=row["text"] or "",
                format_labels=labels_by_doc.get(doc_id, []),
                metadata=md,
            )

    # ── Code-enrichment surface (Phase H) ─────────────────────────────────

    def iter_code_chunks_for_enrichment(
        self,
        model_tag: str,
        dataset_id: "int | None" = None,
    ) -> "Iterator[tuple[int, TextChunk, str]]":
        """Yield ``(chunk_id, TextChunk, language)`` for code chunks to enrich.

        Mirrors :meth:`PostgresBackend.iter_code_chunks_for_enrichment`.
        Idempotency check (``metadata.enrichment.model != model_tag``)
        runs in Python after the row is decoded.
        """
        params: list[Any] = []
        ds_clause = ""
        if dataset_id is not None:
            ds_clause = "AND d.dataset_id = ?"
            params.append(dataset_id)

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
                       FROM document_labels dl2
                       JOIN labels l2 ON l2.id = dl2.label_id
                       WHERE dl2.document_id = d.id AND l2.namespace = 'language'
                       LIMIT 1
                   ) AS doc_language
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN document_labels dl ON dl.document_id = d.id
            JOIN labels l ON l.id = dl.label_id
            WHERE l.namespace = 'class' AND l.value = 'code'
              {ds_clause}
            ORDER BY c.id
            """,
            tuple(params),
        )

        for row in rows:
            md_raw = row["chunk_metadata"]
            if isinstance(md_raw, str):
                try:
                    md = json.loads(md_raw)
                except (TypeError, ValueError):
                    md = {}
            elif isinstance(md_raw, dict):
                md = md_raw
            else:
                md = {}
            existing = md.get("enrichment") or {}
            if isinstance(existing, dict) and existing.get("model") == model_tag:
                continue

            chunk_language = (
                md.get("language")
                or (row.get("doc_language") if hasattr(row, "get") else row["doc_language"])
                or "unknown"
            )
            chunk = TextChunk(
                text=row["text"] or "",
                heading=row.get("heading") if hasattr(row, "get") else row["heading"],
                role=row.get("role") if hasattr(row, "get") else row["role"],
                token_count=(row.get("token_count") if hasattr(row, "get") else row["token_count"]),
                metadata=md,
            )
            yield int(row["chunk_id"]), chunk, str(chunk_language)

    def update_chunk_enrichment(
        self,
        chunk_id: int,
        enrichment: Any,
    ) -> None:
        """Merge ``enrichment.to_metadata()`` into ``chunks.metadata.enrichment``.

        SQLite path: read-modify-write the JSON column (no JSONB merge
        operator available) preserving every other key.
        """
        payload = (
            enrichment.to_metadata() if hasattr(enrichment, "to_metadata") else dict(enrichment)
        )
        rows = self._execute(
            "SELECT metadata FROM chunks WHERE id = ?",
            (chunk_id,),
        )
        if not rows:
            return
        md_raw = rows[0]["metadata"]
        if isinstance(md_raw, str):
            try:
                md = json.loads(md_raw)
            except (TypeError, ValueError):
                md = {}
        elif isinstance(md_raw, dict):
            md = dict(md_raw)
        else:
            md = {}
        if not isinstance(md, dict):
            md = {}
        md["enrichment"] = payload
        self._execute(
            "UPDATE chunks SET metadata = ? WHERE id = ?",
            (json.dumps(md), chunk_id),
        )

    # -------------------------------------------------------------------------
    # Ingest-run state — SR-G3 (SQLite implementation of SR-G2 Protocol)
    # -------------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        """Return current UTC time as an ISO-8601 string with Z suffix."""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    @staticmethod
    def _parse_iso_dt(value: "str | None") -> "datetime | None":
        """Parse a stored ISO-8601 UTC string into an aware :class:`datetime`.

        Accepts both ``Z`` suffix and ``+00:00`` offset.  Returns ``None``
        when *value* is ``None`` or empty.
        """
        if not value:
            return None
        # Normalise 'Z' suffix to '+00:00' for fromisoformat compatibility.
        normalised = value.rstrip("Z")
        if normalised != value:
            normalised += "+00:00"
        try:
            return datetime.fromisoformat(normalised)
        except ValueError:
            return None

    def start_ingest_run(
        self,
        *,
        run_id: str,
        host: str,
        pid: int,
        config_digest: str,
    ) -> None:
        """Insert a new ingest-run row with status='running'.

        On conflict (same run_id — resume path): flip status back to
        'running', clear ended_at, and bump last_progress_at without
        creating a duplicate row.

        Raises:
            sqlite3.IntegrityError: If run_id is empty (NOT NULL constraint
                or UNIQUE empty-string insert depending on SQLite version).
        """
        if not run_id:
            raise ValueError("run_id must be non-empty")

        now = self._now_iso()
        self._execute(
            """
            INSERT INTO ingest_runs
                (run_id, started_at, last_progress_at, status, last_done,
                 host, pid, config_digest)
            VALUES (?, ?, ?, 'running', 0, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status           = 'running',
                ended_at         = NULL,
                last_progress_at = excluded.last_progress_at,
                host             = excluded.host,
                pid              = excluded.pid,
                config_digest    = excluded.config_digest,
                error            = NULL
                -- Deliberately NOT updating started_at: resuming continues
                -- the same logical run; only process identity refreshes.
                -- Mirrors Postgres backend semantics.
            """,
            (run_id, now, now, host, pid, config_digest),
        )

    def update_ingest_run(
        self,
        run_id: str,
        *,
        last_op: "str | None" = None,
        last_done: "int | None" = None,
        last_total: "int | None" = None,
    ) -> None:
        """Best-effort heartbeat update for an ingest run.

        Builds a dynamic SET clause from whichever optional fields were
        supplied.  Always bumps last_progress_at.

        Per protocol contract: swallows ``sqlite3.OperationalError`` and
        logs at DEBUG so a flaky DB does not kill ingest.
        """
        sets: list[str] = ["last_progress_at = ?"]
        params: list[object] = [self._now_iso()]

        if last_op is not None:
            sets.append("last_op = ?")
            params.append(last_op)
        if last_done is not None:
            sets.append("last_done = ?")
            params.append(last_done)
        if last_total is not None:
            sets.append("last_total = ?")
            params.append(last_total)

        params.append(run_id)
        sql = f"UPDATE ingest_runs SET {', '.join(sets)} WHERE run_id = ?"

        try:
            self._execute(sql, tuple(params))
        except sqlite3.OperationalError as exc:
            logger.debug("ingest_run checkpoint write failed: %r", exc)

    def finish_ingest_run(
        self,
        run_id: str,
        *,
        status: "str",
        error: "str | None" = None,
    ) -> None:
        """Set ended_at, status, and optional error on the ingest-run row.

        Raises:
            ValueError: If *status* is not one of the three allowed values.
        """
        allowed = {"completed", "interrupted", "failed"}
        if status not in allowed:
            raise ValueError(f"status must be one of {allowed!r}, got {status!r}")
        now = self._now_iso()
        self._execute(
            """
            UPDATE ingest_runs
               SET status           = ?,
                   ended_at         = ?,
                   last_progress_at = ?,
                   error            = ?
             WHERE run_id = ?
            """,
            (status, now, now, error, run_id),
        )

    def latest_ingest_run(self) -> "dict | None":
        """Return the row with the most-recent started_at (any status).

        Returns ``None`` when the table is empty.  Timestamps are returned
        as UTC-aware :class:`datetime` objects.
        """
        rows = self._execute(
            """
            SELECT id, run_id, started_at, ended_at, last_progress_at,
                   status, last_op, last_done, last_total, error, host,
                   pid, config_digest
              FROM ingest_runs
             ORDER BY started_at DESC
             LIMIT 1
            """,
            (),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "started_at": self._parse_iso_dt(row["started_at"]),
            "ended_at": self._parse_iso_dt(row["ended_at"]),
            "last_progress_at": self._parse_iso_dt(row["last_progress_at"]),
            "status": row["status"],
            "last_op": row["last_op"],
            "last_done": row["last_done"],
            "last_total": row["last_total"],
            "error": row["error"],
            "host": row["host"],
            "pid": row["pid"],
            "config_digest": row["config_digest"],
        }

    def latest_unfinished_ingest_run(self, host: "str | None" = None) -> "dict | None":
        """Return the most-recent row with status IN ('running', 'interrupted').

        host=None (default) returns any unfinished row regardless of host (back-compat).
        host='X' adds AND host = 'X' to the WHERE clause.

        Returns ``None`` when no such row exists.
        """
        rows = self._execute(
            """
            SELECT id, run_id, started_at, ended_at, last_progress_at,
                   status, last_op, last_done, last_total, error, host,
                   pid, config_digest
              FROM ingest_runs
             WHERE status IN ('running', 'interrupted')
               AND (? IS NULL OR host = ?)
             ORDER BY started_at DESC
             LIMIT 1
            """,
            (host, host),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "started_at": self._parse_iso_dt(row["started_at"]),
            "ended_at": self._parse_iso_dt(row["ended_at"]),
            "last_progress_at": self._parse_iso_dt(row["last_progress_at"]),
            "status": row["status"],
            "last_op": row["last_op"],
            "last_done": row["last_done"],
            "last_total": row["last_total"],
            "error": row["error"],
            "host": row["host"],
            "pid": row["pid"],
            "config_digest": row["config_digest"],
        }

    def upsert_ingest_run_source(
        self,
        *,
        run_id: str,
        source_uri_prefix: str,
        dataset_id: int,
        last_scanned_at: "datetime | None" = None,
        docs_seen_delta: int = 0,
        docs_skipped_delta: int = 0,
        docs_failed_delta: int = 0,
        finished: bool = False,
    ) -> None:
        """UPSERT on ``(run_id, source_uri_prefix)``.

        Counters are accumulated via ``col = col + delta`` so repeated calls
        from the ingest hot-path add up correctly.  ``finished=True`` sets
        ``finished_at`` to NOW; ``False`` (default) leaves it NULL.
        ``last_scanned_at`` is stored as an ISO-8601 TEXT; ``None`` leaves
        the column unchanged on conflict.
        """
        last_scanned_str: str | None = None
        if last_scanned_at is not None:
            last_scanned_str = last_scanned_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

        finished_at_str: str | None = self._now_iso() if finished else None

        self._execute(
            """
            INSERT INTO ingest_run_sources
                (run_id, source_uri_prefix, dataset_id, last_scanned_at,
                 docs_seen, docs_skipped, docs_failed, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, source_uri_prefix) DO UPDATE SET
                docs_seen       = docs_seen       + excluded.docs_seen,
                docs_skipped    = docs_skipped    + excluded.docs_skipped,
                docs_failed     = docs_failed     + excluded.docs_failed,
                last_scanned_at = COALESCE(excluded.last_scanned_at,
                                           ingest_run_sources.last_scanned_at),
                finished_at     = COALESCE(excluded.finished_at,
                                           ingest_run_sources.finished_at)
            """,
            (
                run_id,
                source_uri_prefix,
                dataset_id,
                last_scanned_str,
                docs_seen_delta,
                docs_skipped_delta,
                docs_failed_delta,
                finished_at_str,
            ),
        )

    def find_source_last_scanned_at(self, source_uri_prefix: str) -> "datetime | None":
        """Return the max ``finished_at`` across completed/interrupted runs.

        Only rows whose parent ingest run has status in
        ``('completed', 'interrupted')`` are considered — a still-running
        run's scan timestamps are excluded so the resume-skip logic sees the
        last *finished* scan date, not an in-progress one.  Also excludes
        rows where ``finished_at IS NULL`` (source still in-progress within
        a run).

        Uses ``finished_at`` (not ``last_scanned_at``) to match Postgres
        backend semantics per the binding contract in tasks.md §5.

        Returns ``None`` if the source has never been fully scanned.
        """
        rows = self._execute(
            """
            SELECT MAX(irs.finished_at) AS max_scanned
              FROM ingest_run_sources AS irs
              JOIN ingest_runs AS ir ON ir.run_id = irs.run_id
             WHERE irs.source_uri_prefix = ?
               AND ir.status IN ('completed', 'interrupted')
               AND irs.finished_at IS NOT NULL
            """,
            (source_uri_prefix,),
        )
        if not rows:
            return None
        raw = rows[0]["max_scanned"]
        return self._parse_iso_dt(raw)

    def mark_stale_runs(
        self,
        threshold_seconds: float,
        *,
        host: "str | None" = None,
    ) -> int:
        """Transition stale 'running' rows to 'failed'.

        Short-circuits immediately (returns 0) when threshold_seconds <= 0.
        Wraps sqlite3.OperationalError and returns 0 (best-effort idiom).
        """
        if threshold_seconds <= 0:
            return 0
        now_iso = self._now_iso()
        threshold_days = threshold_seconds / 86400.0
        try:
            # SELECT eligible rows first so we can build the error string
            # from the prior host/pid values stored in the row.
            eligible = self._execute(
                """
                SELECT run_id, host, pid
                  FROM ingest_runs
                 WHERE status = 'running'
                   AND (julianday('now') - julianday(last_progress_at)) > ?
                   AND (? IS NULL OR host = ?)
                """,
                (threshold_days, host, host),
            )
            if not eligible:
                return 0
            # Per-row UPDATE uses ``RETURNING run_id`` so we count only rows
            # we ACTUALLY flipped — and the UPDATE WHERE clause re-asserts
            # ``status = 'running'`` so a concurrent worker (or a legitimate
            # finish_ingest_run racing our SELECT) cannot be clobbered: if
            # another writer transitioned the row in the meantime, our
            # UPDATE matches zero rows, RETURNING is empty, we skip it.
            # We stay on ``self._execute`` (rather than dropping to a raw
            # cursor) so callers' mocks of _execute (e.g. for OperationalError
            # swallow tests) still apply uniformly.
            count = 0
            for row in eligible:
                prior_host = row["host"]
                prior_pid = row["pid"]
                error_msg = (
                    f"stale heartbeat: last progress > {threshold_seconds:.0f}s ago; "
                    f"host {prior_host}/pid {prior_pid} presumed dead"
                )
                updated = self._execute(
                    """
                    UPDATE ingest_runs
                       SET status   = 'failed',
                           ended_at = ?,
                           error    = ?
                     WHERE run_id = ?
                       AND status = 'running'
                  RETURNING run_id
                    """,
                    (now_iso, error_msg, row["run_id"]),
                )
                if updated:
                    count += 1
            return count
        except sqlite3.OperationalError as exc:
            logger.debug("mark_stale_runs swallowed OperationalError: %r", exc)
            return 0
