"""P2-T1 RED — MCP write tool ``rate_search_result`` integration tests.

Verifies the full dispatch path for the new Phase P Wave 2 write tool
``rate_search_result``.  Tests run in-process using a real
``SQLiteBackend(":memory:")`` (migrated) wired through a ``build_server``
instance — the same pattern used by
``tests/unit/test_mcp_writes_dispatch.py`` and
``tests/integration/test_mcp_writes_postgres.py``.

Tool contract under test
------------------------
``rate_search_result(query_id, chunk_id, signal, value, source,
                     replacement_chunk_id=None)``

- Gated by ``writes_enabled``: not in ``list_tools()`` when
  ``writes_enabled=False``; calling it returns the standard
  ``"unknown tool"`` error payload.
- ``query_id`` (str): key into ``search_sessions``.  If no row exists,
  auto-create one with ``query="(retroactive)"``.
- ``chunk_id`` (int): FK into ``chunks``.  An unknown id returns a clear
  FK-violation error, not a crash.
- ``signal`` (str), ``value`` (float | None), ``source`` (str): stored
  verbatim on ``search_result_events``.
- ``replacement_chunk_id`` (int | None): optional FK into ``chunks``.
- Writes an audit row via the existing ``mcp_audit`` pattern.
- Returns ``{"event_id": int, "session_id": int}``.
- Two consecutive calls with the same ``(query_id, chunk_id, signal)``
  both succeed — event log semantics, not a state machine.
- Output dict is JSON-serialisable.

RED state: ``rate_search_result`` is not registered in ``server.py`` and
``corpus_forge.mcp.writes`` does not contain a ``rate_search_result``
function yet.  Every test is expected to FAIL with either
``AssertionError`` (registration/gate tests) or ``unknown tool`` error
payload (dispatch tests).

Run command::

    uv run pytest tests/integration/test_mcp_rate_search_result.py \\
        -m 'not requires_docker' -x 2>&1 | tail -5

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOOL_NAME = "rate_search_result"

# ---------------------------------------------------------------------------
# In-process MCP harness (mirrors test_mcp_analyze_tools.py)
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _list_tool_names(server: Any) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _call_raw(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Return the raw CallToolResult root (do NOT raise on isError)."""
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(root: Any) -> dict:
    """Extract dict payload from a successful CallToolResult."""
    sc = getattr(root, "structuredContent", None)
    if sc is not None:
        return dict(sc)
    content = getattr(root, "content", [])
    return json.loads(content[0].text)


def _is_error(root: Any) -> bool:
    return bool(getattr(root, "isError", False))


def _error_text(root: Any) -> str:
    content = getattr(root, "content", [])
    return "".join(getattr(b, "text", "") for b in content)


# ---------------------------------------------------------------------------
# Backend + seeding helpers
# ---------------------------------------------------------------------------


def _fresh_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


class _BackedRetriever:
    """Minimal retriever that exposes a real SQLiteBackend."""

    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: Any) -> list[Any]:
        return []


def _seed(backend: SQLiteBackend) -> dict[str, int]:
    """Insert dataset, document, and two chunks; return ids."""
    with backend._get_connection() as conn:
        dataset_id: int = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("rate-test-ds", "text", "P2 rate_search_result test dataset"),
        ).fetchone()[0]

        document_id: int = conn.execute(
            "INSERT INTO documents"
            " (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                dataset_id,
                "test://doc/a.md",
                "doc-hash-a",
                "Doc A",
                "Quantum mechanics foundational text.",
                "{}",
            ),
        ).fetchone()[0]

        chunk_id: int = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (document_id, 0, "Eigenvalue decomposition of the Hamiltonian.", "{}"),
        ).fetchone()[0]

        replacement_chunk_id: int = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (document_id, 1, "Bloch theorem applies to periodic potentials.", "{}"),
        ).fetchone()[0]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "replacement_chunk_id": replacement_chunk_id,
    }


def _build_server(backend: SQLiteBackend, *, writes_enabled: bool) -> Any:
    retriever = _BackedRetriever(backend)
    return build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=writes_enabled,
    )


def _count_events(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM search_result_events")
    return int(rows[0]["n"])


def _count_sessions(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM search_sessions")
    return int(rows[0]["n"])


def _count_audit_rows(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM mcp_audit")
    return int(rows[0]["n"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    return _fresh_backend()


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, int]:
    return _seed(backend)


@pytest.fixture
def server_rw(backend: SQLiteBackend, seeded: dict) -> Any:
    return _build_server(backend, writes_enabled=True)


@pytest.fixture
def server_ro(backend: SQLiteBackend, seeded: dict) -> Any:
    return _build_server(backend, writes_enabled=False)


# ===========================================================================
# 1. Tool registration
# ===========================================================================


class TestToolRegistration:
    """rate_search_result must be registered iff writes_enabled=True."""

    def test_tool_registered_when_writes_enabled(
        self, backend: SQLiteBackend, seeded: dict
    ) -> None:
        """rate_search_result appears in list_tools() when writes_enabled=True."""
        names = _list_tool_names(_build_server(backend, writes_enabled=True))
        assert _TOOL_NAME in names, (
            f"Expected {_TOOL_NAME!r} in tool list with writes_enabled=True; got {names}"
        )

    def test_tool_absent_when_writes_disabled(self, backend: SQLiteBackend, seeded: dict) -> None:
        """rate_search_result is NOT in list_tools() when writes_enabled=False."""
        names = _list_tool_names(_build_server(backend, writes_enabled=False))
        assert _TOOL_NAME not in names, (
            f"Expected {_TOOL_NAME!r} absent from tool list with writes_enabled=False; got {names}"
        )


# ===========================================================================
# 2. Write-gate enforcement
# ===========================================================================


class TestWriteGate:
    """Calling the tool with writes_enabled=False returns the standard gate error."""

    def test_calling_write_gated_tool_returns_error(self, server_ro: Any, seeded: dict) -> None:
        """rate_search_result with writes_enabled=False returns isError or unknown-tool."""
        root = _call_raw(
            server_ro,
            _TOOL_NAME,
            {
                "query_id": "qid-gate-test",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 1.0,
                "source": "human",
            },
        )
        # The MCP server falls through to _error_result("unknown tool: …")
        # when writes_enabled=False (tool not in dispatch table).
        assert _is_error(root), (
            f"Expected isError=True for write-gated call; got payload: {_payload(root)}"
        )

    def test_write_gate_error_message_contains_tool_name(
        self, server_ro: Any, seeded: dict
    ) -> None:
        """The error text from the gated call names the tool or says 'unknown'."""
        root = _call_raw(
            server_ro,
            _TOOL_NAME,
            {
                "query_id": "qid-gate-msg",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 0.5,
                "source": "human",
            },
        )
        text = _error_text(root).lower()
        assert "unknown" in text or _TOOL_NAME in text, (
            f"Expected error text to reference {_TOOL_NAME!r} or 'unknown'; got: {text!r}"
        )


# ===========================================================================
# 3. Happy path — existing session
# ===========================================================================


class TestHappyPath:
    """Nominal round-trip: session exists, valid chunk, row inserted, audit written."""

    def _pre_insert_session(self, backend: SQLiteBackend, query_id: str, dataset_id: int) -> int:
        """Insert a search_sessions row and return its id."""
        rows = backend._execute(
            "INSERT INTO search_sessions (query, dataset_id) VALUES (?, ?) RETURNING id",
            (query_id, dataset_id),
        )
        return int(rows[0]["id"])

    def test_happy_path_existing_session(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """Inserts event row and audit row; returns {event_id, session_id}."""
        self._pre_insert_session(backend, "q-existing-1", seeded["dataset_id"])

        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "q-existing-1",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 0.9,
                "source": "human",
            },
        )
        assert not _is_error(root), f"Unexpected isError: {_error_text(root)}"
        result = _payload(root)
        assert isinstance(result.get("event_id"), int), f"Expected event_id int; got {result}"
        assert isinstance(result.get("session_id"), int), f"Expected session_id int; got {result}"

    def test_happy_path_event_row_persisted(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """A search_result_events row is actually written to the database."""
        self._pre_insert_session(backend, "q-persist-check", seeded["dataset_id"])
        before = _count_events(backend)

        _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "q-persist-check",
                "chunk_id": seeded["chunk_id"],
                "signal": "click",
                "value": 1.0,
                "source": "ui",
            },
        )
        assert _count_events(backend) == before + 1, (
            "Expected exactly one new search_result_events row after rate_search_result"
        )

    def test_happy_path_audit_row_written(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """rate_search_result emits exactly one mcp_audit row."""
        self._pre_insert_session(backend, "q-audit-check", seeded["dataset_id"])
        before = _count_audit_rows(backend)

        _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "q-audit-check",
                "chunk_id": seeded["chunk_id"],
                "signal": "thumbs_up",
                "value": 1.0,
                "source": "human",
            },
        )
        assert _count_audit_rows(backend) == before + 1, (
            "Expected exactly one new mcp_audit row after rate_search_result"
        )

    def test_output_is_json_serialisable(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """The return value round-trips through json.dumps without error."""
        self._pre_insert_session(backend, "q-json-serial", seeded["dataset_id"])

        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "q-json-serial",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 0.7,
                "source": "reranker",
            },
        )
        assert not _is_error(root)
        result = _payload(root)
        serialised = json.dumps(result)  # must not raise TypeError
        assert isinstance(serialised, str)


# ===========================================================================
# 4. Auto-create session when query_id is new
# ===========================================================================


class TestAutoCreateSession:
    """When query_id is not in search_sessions, a row is auto-created."""

    def test_autocreate_session_when_query_id_unknown(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """A brand-new query_id triggers session creation; no exception raised."""
        before_sessions = _count_sessions(backend)
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "brand-new-qid-xyz",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 0.8,
                "source": "human",
            },
        )
        assert not _is_error(root), f"Expected success on new query_id; got: {_error_text(root)}"
        assert _count_sessions(backend) == before_sessions + 1, (
            "Expected one new search_sessions row for an unknown query_id"
        )

    def test_autocreated_session_query_text_matches_query_id(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """Auto-created session stores the supplied query_id as its query text.

        Using query_id as the row's query column means a subsequent
        rate_search_result with the same query_id finds and reuses the
        same session instead of spawning a duplicate.
        """
        _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "retro-qid-001",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 1.0,
                "source": "human",
            },
        )
        rows = backend._execute(
            "SELECT query FROM search_sessions WHERE id IS NOT NULL ORDER BY id DESC LIMIT 1"
        )
        assert rows, "Expected at least one search_sessions row after auto-create"
        assert rows[0]["query"] == "retro-qid-001", (
            f"Expected query='retro-qid-001' on auto-created session; got {rows[0]['query']!r}"
        )

    def test_autocreated_session_id_is_returned(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """The returned session_id matches the auto-created row's id."""
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "retro-qid-002",
                "chunk_id": seeded["chunk_id"],
                "signal": "click",
                "value": None,
                "source": "ui",
            },
        )
        assert not _is_error(root)
        result = _payload(root)
        session_id = result["session_id"]
        rows = backend._execute("SELECT id FROM search_sessions ORDER BY id DESC LIMIT 1")
        assert rows[0]["id"] == session_id, (
            f"Returned session_id={session_id} doesn't match DB row id={rows[0]['id']}"
        )


# ===========================================================================
# 5. replacement_chunk_id
# ===========================================================================


class TestReplacementChunkId:
    """replacement_chunk_id is stored cleanly on the event row."""

    def test_replacement_chunk_id_recorded(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """replacement_chunk_id appears verbatim in the search_result_events row."""
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "qid-replacement",
                "chunk_id": seeded["chunk_id"],
                "signal": "curation_suggestion",
                "value": None,
                "source": "human",
                "replacement_chunk_id": seeded["replacement_chunk_id"],
            },
        )
        assert not _is_error(root), f"Unexpected error: {_error_text(root)}"
        result = _payload(root)
        rows = backend._execute(
            "SELECT replacement_chunk_id FROM search_result_events WHERE id = ?",
            (result["event_id"],),
        )
        assert rows, "Expected event row in search_result_events"
        assert rows[0]["replacement_chunk_id"] == seeded["replacement_chunk_id"], (
            f"Expected replacement_chunk_id={seeded['replacement_chunk_id']}; "
            f"got {rows[0]['replacement_chunk_id']}"
        )

    def test_replacement_chunk_id_defaults_to_null(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """When replacement_chunk_id is omitted, the column is NULL."""
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "qid-no-replacement",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 0.5,
                "source": "human",
            },
        )
        assert not _is_error(root)
        result = _payload(root)
        rows = backend._execute(
            "SELECT replacement_chunk_id FROM search_result_events WHERE id = ?",
            (result["event_id"],),
        )
        assert rows
        assert rows[0]["replacement_chunk_id"] is None, (
            f"Expected NULL replacement_chunk_id when omitted; "
            f"got {rows[0]['replacement_chunk_id']}"
        )


# ===========================================================================
# 6. value=None accepted (signal-only events)
# ===========================================================================


class TestValueNone:
    """value=None is accepted for signals that carry no numeric magnitude."""

    def test_value_none_creates_event_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """thumbs_up signal with value=None inserts a row without error."""
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "qid-value-none",
                "chunk_id": seeded["chunk_id"],
                "signal": "thumbs_up",
                "value": None,
                "source": "human",
            },
        )
        assert not _is_error(root), f"Unexpected error with value=None: {_error_text(root)}"
        result = _payload(root)
        assert isinstance(result.get("event_id"), int)

    def test_value_none_stored_as_null(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """NULL value is persisted verbatim on the event row."""
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "qid-null-val",
                "chunk_id": seeded["chunk_id"],
                "signal": "thumbs_down",
                "value": None,
                "source": "human",
            },
        )
        assert not _is_error(root)
        result = _payload(root)
        rows = backend._execute(
            "SELECT value FROM search_result_events WHERE id = ?",
            (result["event_id"],),
        )
        assert rows
        assert rows[0]["value"] is None, (
            f"Expected NULL value in DB when value=None passed; got {rows[0]['value']}"
        )


# ===========================================================================
# 7. Invalid chunk_id returns clear error (FK violation)
# ===========================================================================


class TestInvalidChunkId:
    """An unknown chunk_id does not silently succeed — a clear error is returned."""

    def test_unknown_chunk_id_returns_error(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """rate_search_result with a non-existent chunk_id returns isError=True."""
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "qid-bad-chunk",
                "chunk_id": 999999,
                "signal": "relevance",
                "value": 0.5,
                "source": "human",
            },
        )
        assert _is_error(root), (
            f"Expected isError=True for unknown chunk_id=999999; got payload: {_payload(root)}"
        )


# ===========================================================================
# 8. Duplicate (query_id, chunk_id, signal) produces two rows (event log)
# ===========================================================================


class TestDuplicateRatingsAreAllRecorded:
    """Two calls with the same (query_id, chunk_id, signal) both succeed and
    both create event rows — rate_search_result is an event log, not a state
    machine with a unique constraint."""

    def test_two_consecutive_ratings_create_two_rows(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        common_args = {
            "query_id": "qid-dup-signal",
            "chunk_id": seeded["chunk_id"],
            "signal": "relevance",
            "value": 0.9,
            "source": "human",
        }
        root1 = _call_raw(server_rw, _TOOL_NAME, common_args)
        root2 = _call_raw(server_rw, _TOOL_NAME, common_args)

        assert not _is_error(root1), f"First call failed: {_error_text(root1)}"
        assert not _is_error(root2), f"Second call failed: {_error_text(root2)}"

        r1 = _payload(root1)
        r2 = _payload(root2)
        # Both event_ids are valid ints.
        assert isinstance(r1.get("event_id"), int)
        assert isinstance(r2.get("event_id"), int)
        # They should be distinct rows.
        assert r1["event_id"] != r2["event_id"], (
            "Expected two distinct event_id values for two identical rating calls"
        )

    def test_two_ratings_yield_exactly_two_db_rows(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """DB count confirms both rows persisted."""
        before = _count_events(backend)
        args = {
            "query_id": "qid-dup-count",
            "chunk_id": seeded["chunk_id"],
            "signal": "click",
            "value": 1.0,
            "source": "ui",
        }
        _call_raw(server_rw, _TOOL_NAME, args)
        _call_raw(server_rw, _TOOL_NAME, args)
        assert _count_events(backend) == before + 2, (
            "Expected exactly 2 new event rows after two identical rating calls"
        )


# ===========================================================================
# 9. Source string preserved verbatim
# ===========================================================================


class TestSourcePreserved:
    """The source field is stored and returned exactly as passed."""

    def test_source_verbatim_in_db(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        source = "reranker-v2/bge-reranker-large"
        root = _call_raw(
            server_rw,
            _TOOL_NAME,
            {
                "query_id": "qid-source-test",
                "chunk_id": seeded["chunk_id"],
                "signal": "relevance",
                "value": 0.75,
                "source": source,
            },
        )
        assert not _is_error(root)
        result = _payload(root)
        rows = backend._execute(
            "SELECT source FROM search_result_events WHERE id = ?",
            (result["event_id"],),
        )
        assert rows
        assert rows[0]["source"] == source, (
            f"Expected source={source!r} in DB; got {rows[0]['source']!r}"
        )


# ---------------------------------------------------------------------------
# Postgres parity — verifies the `?` → `%s` placeholder dispatch in
# corpus_forge.mcp.writes._q() works against a real psycopg connection.
# Requires Docker + testcontainers via the shared `pg_dsn` session fixture.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestRateSearchResultPostgres:
    """Same dispatch contract, exercised against a Postgres backend.

    Pinned regressions:
    - rate_search_result must translate the SQLite-style ``?`` placeholders
      in writes._q() to ``%s`` for psycopg. Any miss raises psycopg's
      ``SyntaxError`` immediately on execute.
    - Auto-created session uses the supplied query_id (not the legacy
      ``"(retroactive)"`` literal) on Postgres too.
    """

    @staticmethod
    def _reset_and_migrate(pg_dsn: str) -> None:
        import re as _re
        from pathlib import Path as _Path

        import psycopg
        from alembic import command
        from alembic.config import Config as _AlembicConfig

        with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")

        repo_root = _Path(__file__).resolve().parents[2]
        cfg = _AlembicConfig(str(repo_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(repo_root / "corpus_forge" / "alembic"))
        cfg.set_main_option(
            "sqlalchemy.url",
            _re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", pg_dsn),
        )
        command.upgrade(cfg, "head")

    @staticmethod
    def _seed_pg(pg_dsn: str) -> dict[str, int]:
        import psycopg

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind, description) "
                "VALUES (%s, %s, %s) RETURNING id",
                ("pg-rate-ds", "text", "Postgres parity for rate_search_result"),
            )
            ds_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO corpus.documents "
                "(dataset_id, source_uri, content_hash, title, text, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (ds_id, "test://pg/doc.md", "pg-doc-hash", "PG Doc", "body", "{}"),
            )
            doc_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO corpus.chunks "
                "(document_id, chunk_index, text, metadata) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (doc_id, 0, "rated chunk on postgres", "{}"),
            )
            chunk_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO corpus.chunks "
                "(document_id, chunk_index, text, metadata) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (doc_id, 1, "replacement chunk on postgres", "{}"),
            )
            repl_id = cur.fetchone()[0]
            conn.commit()
        return {
            "dataset_id": ds_id,
            "document_id": doc_id,
            "chunk_id": chunk_id,
            "replacement_chunk_id": repl_id,
        }

    def _pg_backend(self, pg_dsn: str) -> Any:
        from corpus_forge.backends.postgres import PostgresBackend

        return PostgresBackend(dsn=pg_dsn, schema="corpus")

    def test_pg_rate_search_result_writes_event_row(self, pg_dsn: str) -> None:
        """Happy path on Postgres — psycopg accepts the translated `%s` SQL."""
        self._reset_and_migrate(pg_dsn)
        seeded = self._seed_pg(pg_dsn)
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        root = _call_raw(
            server,
            _TOOL_NAME,
            {
                "query_id": "pg-qid-001",
                "chunk_id": seeded["chunk_id"],
                "signal": "thumbs_up",
                "value": 1.0,
                "source": "human",
            },
        )
        assert not _is_error(root), _error_text(root)
        result = _payload(root)
        assert "event_id" in result
        assert "session_id" in result
        rows = backend._execute(
            "SELECT signal, value, source FROM corpus.search_result_events WHERE id = %s",
            (result["event_id"],),
        )
        assert rows and rows[0]["signal"] == "thumbs_up"
        assert rows[0]["value"] == 1.0

    def test_pg_auto_creates_session_with_query_id(self, pg_dsn: str) -> None:
        """Auto-created session stores query_id (not '(retroactive)')."""
        self._reset_and_migrate(pg_dsn)
        seeded = self._seed_pg(pg_dsn)
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        _call_raw(
            server,
            _TOOL_NAME,
            {
                "query_id": "pg-new-qid",
                "chunk_id": seeded["chunk_id"],
                "signal": "click",
                "value": 0.5,
                "source": "claude_code",
            },
        )
        rows = backend._execute("SELECT query FROM corpus.search_sessions ORDER BY id DESC LIMIT 1")
        assert rows and rows[0]["query"] == "pg-new-qid"

    def test_pg_existing_session_is_reused(self, pg_dsn: str) -> None:
        """Two calls with the same query_id share one session row."""
        self._reset_and_migrate(pg_dsn)
        seeded = self._seed_pg(pg_dsn)
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        args = {
            "query_id": "pg-reused-qid",
            "chunk_id": seeded["chunk_id"],
            "signal": "thumbs_up",
            "value": 0.9,
            "source": "human",
        }
        first = _payload(_call_raw(server, _TOOL_NAME, args))
        second = _payload(_call_raw(server, _TOOL_NAME, args))
        assert first["session_id"] == second["session_id"]
        sess_rows = backend._execute(
            "SELECT COUNT(*) AS n FROM corpus.search_sessions WHERE query = %s",
            ("pg-reused-qid",),
        )
        assert int(sess_rows[0]["n"]) == 1
        evt_rows = backend._execute(
            "SELECT COUNT(*) AS n FROM corpus.search_result_events WHERE session_id = %s",
            (first["session_id"],),
        )
        assert int(evt_rows[0]["n"]) == 2

    def test_pg_unknown_chunk_id_returns_error(self, pg_dsn: str) -> None:
        """FK violation on Postgres surfaces as a clean error payload."""
        self._reset_and_migrate(pg_dsn)
        self._seed_pg(pg_dsn)
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        root = _call_raw(
            server,
            _TOOL_NAME,
            {
                "query_id": "qid-fk",
                "chunk_id": 9999999,
                "signal": "relevance",
                "value": 1.0,
                "source": "human",
            },
        )
        assert _is_error(root)
        assert "does not exist" in _error_text(root)
