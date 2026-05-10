"""sqlite-vec optional loader for corpus-forge SQLite backend — B-01.

Exposes:
- SQLITE_VEC_AVAILABLE: bool — True iff sqlite-vec is installed and importable.
- load_sqlite_vec(conn) — enables the sqlite-vec extension on a connection.

Install the extension via the sqlite extra:
    pip install 'corpus-forge[sqlite]'
    # or
    uv sync --extra sqlite
"""

import sqlite3
from types import ModuleType

try:
    # pyrefly: ignore[missing-import]  # optional dep, install via [sqlite] extra
    import sqlite_vec as _sqlite_vec_module

    SQLITE_VEC_AVAILABLE: bool = True
    _sqlite_vec: ModuleType | None = _sqlite_vec_module
except ImportError:
    SQLITE_VEC_AVAILABLE: bool = False
    _sqlite_vec: ModuleType | None = None


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension into *conn*.

    Sequence:
      1. conn.enable_load_extension(True)
      2. sqlite_vec.load(conn)
      3. conn.enable_load_extension(False)

    Raises whatever the connection or sqlite_vec raise — never silently no-ops.
    This means a broken connection (missing enable_load_extension, OperationalError,
    etc.) will propagate to the caller.

    Raises ImportError if sqlite-vec is not installed.
    """
    if _sqlite_vec is None:
        raise ImportError("sqlite-vec is not installed. Install with: uv sync --extra sqlite")
    conn.enable_load_extension(True)
    try:
        _sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
