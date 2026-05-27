"""Unit pins for the shared raw DB-API connection factory.

Surface under test: ``corpus_forge.backends.conn.open_conn``.

``open_conn`` is the deduplicated home for the raw-connection dispatch that
``corpus-forge analyze`` and ``corpus-forge feedback`` both need.  Both branches
are exercised here:

- SQLite → a real ``sqlite3.Connection`` (no mocking needed).
- Postgres → the lazy ``import psycopg`` + ``psycopg.connect(dsn)`` path, driven
  with a fake ``psycopg`` module injected into ``sys.modules`` so the test never
  needs a live Postgres (mirrors how the source keeps the import lazy so the
  SQLite-only install never pulls psycopg).
"""

from __future__ import annotations

import sqlite3
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from corpus_forge.backends.conn import open_conn

pytestmark = pytest.mark.unit


def test_open_conn_sqlite_returns_sqlite_connection(tmp_path):
    db = tmp_path / "c.db"
    sqlite3.connect(db).close()
    cfg = SimpleNamespace(backend=SimpleNamespace(kind="sqlite", dsn=str(db)))

    conn = open_conn(cfg)
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_open_conn_defaults_to_sqlite_when_kind_missing(tmp_path):
    """`kind` defaults to 'sqlite' via getattr when the attribute is absent."""
    db = tmp_path / "c.db"
    sqlite3.connect(db).close()
    cfg = SimpleNamespace(backend=SimpleNamespace(dsn=str(db)))

    conn = open_conn(cfg)
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_open_conn_postgres_lazy_imports_psycopg(monkeypatch):
    """Postgres backend → lazy `import psycopg` + `psycopg.connect(dsn)`.

    A fake `psycopg` module returns a sentinel connection so the test
    asserts the dsn is threaded through without a live Postgres.
    """
    sentinel = object()
    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = MagicMock(return_value=sentinel)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    cfg = SimpleNamespace(backend=SimpleNamespace(kind="postgres", dsn="postgresql://u@h/db"))
    conn = open_conn(cfg)

    assert conn is sentinel
    fake_psycopg.connect.assert_called_once_with("postgresql://u@h/db")


def test_open_conn_unknown_kind_raises_value_error():
    """An unrecognised backend kind (e.g. a typo) raises ValueError instead of
    silently falling through to the Postgres path."""
    cfg = SimpleNamespace(backend=SimpleNamespace(kind="postgres-typo", dsn="x"))
    with pytest.raises(ValueError, match="unsupported backend kind: 'postgres-typo'"):
        open_conn(cfg)
