"""Shared raw DB-API 2.0 connection factory for the CLI subgroups.

``corpus-forge analyze`` and ``corpus-forge feedback`` both need a raw DB-API
2.0 connection (not the full ``StorageBackend`` protocol object) so they can run
ad-hoc read queries against the configured backend.  This module is the single
home for that dispatch so the two CLI modules don't each carry a byte-identical
copy.

The psycopg import stays lazy inside :func:`open_conn` so importing this module
(and therefore the CLI subgroups) never pulls in the Postgres driver on an
environment that only has the SQLite extra installed.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def open_conn(cfg: Any) -> Any:
    """Open and return a raw DB-API 2.0 connection for the given Config object.

    Args:
        cfg: A ``corpus_forge.config.Config`` instance (or mock equivalent).

    Returns:
        A ``sqlite3.Connection`` for SQLite backends, or a psycopg connection
        for Postgres backends.
    """
    backend = cfg.backend
    kind: str = getattr(backend, "kind", "sqlite")
    dsn: str = str(backend.dsn)

    if kind == "sqlite":
        return sqlite3.connect(dsn)
    elif kind == "postgres":
        # RFC fleet-4 — resolve a ``ts://<pg-host>[:port]/<db>`` DSN to a
        # connectable ``postgresql://…`` URL. Non-``ts://`` DSNs pass
        # through unchanged (no Tailscale import on that path).
        from corpus_forge.net import resolve_endpoint_for

        dsn = resolve_endpoint_for(dsn, cfg, default_scheme="postgresql")
        # Lazy import psycopg so CLI startup is unaffected on environments
        # that have only the SQLite extra installed.
        import psycopg

        return psycopg.connect(dsn)
    else:
        raise ValueError(f"unsupported backend kind: {kind!r}")
