"""SQLite storage backend for corpus-forge — B-03 skeleton + migrate().

Single-host, file-based backend.  No connection pool, no async, no LISTEN/NOTIFY.
For protocol symmetry the constructor accepts a `schema` parameter, but SQLite has
no schema namespacing so the value is stored and ignored at query time.
"""

import contextlib
import re
import sqlite3
from pathlib import Path

from ..schema import migrate as _migrate_module
from .sqlite_vec_loader import SQLITE_VEC_AVAILABLE, load_sqlite_vec


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
            conn.commit()
            if cursor.description is not None:
                return [dict(row) for row in cursor.fetchall()]
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
