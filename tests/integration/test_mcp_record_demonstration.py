"""Q1-T2 RED — MCP write tool ``record_demonstration`` integration tests.

Verifies the full dispatch path for the new Phase Q Wave 1 write tool
``record_demonstration``.  Tests run in-process using a real
``SQLiteBackend(":memory:")`` (migrated) wired through a ``build_server``
instance — the same pattern used by ``tests/integration/test_mcp_rate_search_result.py``.

Tool contract under test
------------------------
``record_demonstration(query, student_messages, teacher_messages, target,
                       source, dataset=None, trace_id=None)``

- ``query`` (str): the search/curation query that prompted the correction.
- ``student_messages`` (list[dict]): ``[{role, content}]`` — the model's
  *prior* output (student perspective).
- ``teacher_messages`` (list[dict]): ``[{role, content}]`` — the curation
  prompt or expert demonstration (teacher perspective).
- ``target`` (str): the corrected/improved output text.
- ``source`` (str): must be one of the ``SDFTSource`` enum values defined in
  ``corpus_forge/sdft/sources.py``.
- ``dataset`` (str | None): dataset name for FK resolution.  When ``None``
  the tool must resolve the dataset or use a default.
- ``trace_id`` (str | None): optional cross-system tracing key.
- Computes ``content_hash = sha256(canonical_json(query, student_messages,
  teacher_messages, target))``.
- ``INSERT ... ON CONFLICT (content_hash) DO NOTHING`` — idempotent.
  A second identical call returns the existing row's ``id`` (``deduped=True``),
  and does NOT insert a new row.
- Writes one ``mcp_audit`` row per call (including deduped calls).
- Returns ``{"demonstration_id": int, "deduped": bool}``.
- Gated by ``writes_enabled``: not in ``list_tools()`` when
  ``writes_enabled=False``; calling it returns the standard error payload.

RED state
---------
``record_demonstration`` is not registered in ``server.py`` and
``corpus_forge.mcp.writes`` does not contain a ``record_demonstration``
function yet.  Expected failures:

- Registration tests: ``AssertionError`` (tool name absent from list_tools()).
- Dispatch tests: ``isError`` with "unknown tool" text.
- Source-enum tests: ``ImportError`` or ``ModuleNotFoundError`` on
  ``corpus_forge.sdft.sources``.

Run command::

    uv run pytest tests/integration/test_mcp_record_demonstration.py \\
        -m 'not requires_docker' -x 2>&1 | tail -30

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Coroutine

    from mcp.server import Server

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOOL_NAME = "record_demonstration"

# The full list of valid SDFTSource values as strings.
# The enum is defined in corpus_forge/sdft/sources.py (TBD by coder).
_VALID_SOURCES = {
    "curation_commit",
    "rate_search_result",
    "record_demonstration",
    "cli_feedback",
    "claude_code",
    "gemini",
    "opencode",
    "codex",
}

# ---------------------------------------------------------------------------
# In-process MCP harness (mirrors test_mcp_rate_search_result.py)
# ---------------------------------------------------------------------------


def _run(coro: Coroutine[object, object, object]) -> object:
    return asyncio.run(coro)


def _list_tool_names(server: Server) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _call_raw(server: Server, name: str, arguments: dict[str, object]) -> object:
    """Return the raw CallToolResult root (do NOT raise on isError)."""
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(root: object) -> dict[str, object]:
    """Extract dict payload from a successful CallToolResult."""
    sc = getattr(root, "structuredContent", None)
    if sc is not None:
        return dict(sc)
    content = getattr(root, "content", [])
    return json.loads(content[0].text)


def _is_error(root: object) -> bool:
    return bool(getattr(root, "isError", False))


def _error_text(root: object) -> str:
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

    def search(self, query: str, options: object) -> list[object]:
        return []


def _seed_dataset(backend: SQLiteBackend) -> dict[str, int]:
    """Insert a dataset; return ids."""
    with backend._get_connection() as conn:
        dataset_id: int = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("sdft-test-ds", "text", "Q1 record_demonstration test dataset"),
        ).fetchone()[0]
        conn.commit()

    return {"dataset_id": dataset_id}


def _build_server(backend: SQLiteBackend, *, writes_enabled: bool) -> Server:
    retriever = _BackedRetriever(backend)
    return build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=writes_enabled,
    )


def _count_demonstrations(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM sdft_demonstrations")
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
    return _seed_dataset(backend)


@pytest.fixture
def server_rw(backend: SQLiteBackend, seeded: dict) -> Server:
    return _build_server(backend, writes_enabled=True)


@pytest.fixture
def server_ro(backend: SQLiteBackend, seeded: dict) -> Server:
    return _build_server(backend, writes_enabled=False)


# ---------------------------------------------------------------------------
# Canonical demonstration payload factory
# ---------------------------------------------------------------------------


def _demo_args(
    *,
    dataset: str = "sdft-test-ds",
    source: str = "record_demonstration",
    query: str = "What is the eigenvalue of the Hamiltonian for a hydrogen atom?",
    trace_id: str | None = None,
) -> dict[str, object]:
    """Return a valid record_demonstration argument dict."""
    args: dict[str, object] = {
        "query": query,
        "student_messages": [
            {"role": "assistant", "content": "The eigenvalue is approximately -13.6 eV."}
        ],
        "teacher_messages": [
            {"role": "user", "content": "Explain why the ground state energy is -13.6 eV."}
        ],
        "target": "Ground-state energy of hydrogen is -13.6 eV derived from Bohr model.",
        "source": source,
        "dataset": dataset,
    }
    if trace_id is not None:
        args["trace_id"] = trace_id
    return args


# ===========================================================================
# 1. Tool registration
# ===========================================================================


class TestToolRegistration:
    """record_demonstration must be registered iff writes_enabled=True."""

    def test_tool_registered_when_writes_enabled(
        self, backend: SQLiteBackend, seeded: dict
    ) -> None:
        """record_demonstration appears in list_tools() when writes_enabled=True."""
        names = _list_tool_names(_build_server(backend, writes_enabled=True))
        assert _TOOL_NAME in names, (
            f"Expected {_TOOL_NAME!r} in tool list with writes_enabled=True; got {names}"
        )

    def test_tool_absent_when_writes_disabled(self, backend: SQLiteBackend, seeded: dict) -> None:
        """record_demonstration is NOT in list_tools() when writes_enabled=False."""
        names = _list_tool_names(_build_server(backend, writes_enabled=False))
        assert _TOOL_NAME not in names, (
            f"Expected {_TOOL_NAME!r} absent from tool list with writes_enabled=False; got {names}"
        )


# ===========================================================================
# 2. Write-gate enforcement
# ===========================================================================


class TestWriteGate:
    """Calling the tool with writes_enabled=False returns the standard gate error."""

    def test_calling_write_gated_tool_returns_error(self, server_ro: Server, seeded: dict) -> None:
        """record_demonstration with writes_enabled=False returns isError=True."""
        root = _call_raw(server_ro, _TOOL_NAME, _demo_args())
        assert _is_error(root), (
            f"Expected isError=True for write-gated call; got payload: {_payload(root)}"
        )

    def test_write_gate_error_message_names_tool_or_unknown(
        self, server_ro: Server, seeded: dict
    ) -> None:
        """The error text from the gated call names the tool or says 'unknown'."""
        root = _call_raw(server_ro, _TOOL_NAME, _demo_args())
        text = _error_text(root).lower()
        assert "unknown" in text or _TOOL_NAME in text, (
            f"Expected error text to reference {_TOOL_NAME!r} or 'unknown'; got: {text!r}"
        )


# ===========================================================================
# 3. Happy path
# ===========================================================================


class TestHappyPath:
    """Nominal round-trip: valid args → row inserted, audit written, payload returned."""

    def test_happy_path_returns_demonstration_id_and_deduped_flag(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """Returns {demonstration_id: int, deduped: bool}."""
        root = _call_raw(server_rw, _TOOL_NAME, _demo_args())
        assert not _is_error(root), f"Unexpected isError: {_error_text(root)}"
        result = _payload(root)
        assert isinstance(result.get("demonstration_id"), int), (
            f"Expected demonstration_id int; got {result}"
        )
        assert isinstance(result.get("deduped"), bool), f"Expected deduped bool; got {result}"

    def test_happy_path_deduped_false_on_first_call(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """First call must return deduped=False (not a dedup hit)."""
        root = _call_raw(server_rw, _TOOL_NAME, _demo_args())
        assert not _is_error(root)
        result = _payload(root)
        assert result["deduped"] is False, (
            f"Expected deduped=False on first call; got {result['deduped']!r}"
        )

    def test_happy_path_row_persisted_in_db(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """A sdft_demonstrations row is actually written to the database."""
        before = _count_demonstrations(backend)
        _call_raw(server_rw, _TOOL_NAME, _demo_args())
        assert _count_demonstrations(backend) == before + 1, (
            "Expected exactly one new sdft_demonstrations row after record_demonstration"
        )

    def test_happy_path_audit_row_written(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """record_demonstration emits exactly one mcp_audit row."""
        before = _count_audit_rows(backend)
        _call_raw(server_rw, _TOOL_NAME, _demo_args())
        assert _count_audit_rows(backend) == before + 1, (
            "Expected exactly one new mcp_audit row after record_demonstration"
        )

    def test_output_is_json_serialisable(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """The return value round-trips through json.dumps without error."""
        root = _call_raw(server_rw, _TOOL_NAME, _demo_args())
        assert not _is_error(root)
        result = _payload(root)
        serialised = json.dumps(result)  # must not raise TypeError
        assert isinstance(serialised, str)


# ===========================================================================
# 4. Idempotent dedup (ON CONFLICT DO NOTHING)
# ===========================================================================


class TestIdempotentDedup:
    """Identical calls deduplicate via content_hash; second call returns deduped=True."""

    def test_second_identical_call_returns_deduped_true(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """Second call with same payload must return deduped=True."""
        args = _demo_args()
        _call_raw(server_rw, _TOOL_NAME, args)
        root2 = _call_raw(server_rw, _TOOL_NAME, args)
        assert not _is_error(root2), f"Second call should not error; got: {_error_text(root2)}"
        result2 = _payload(root2)
        assert result2["deduped"] is True, (
            f"Expected deduped=True on second identical call; got {result2['deduped']!r}"
        )

    def test_second_identical_call_returns_same_demonstration_id(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """Deduped call returns the same demonstration_id as the original."""
        args = _demo_args()
        root1 = _call_raw(server_rw, _TOOL_NAME, args)
        root2 = _call_raw(server_rw, _TOOL_NAME, args)
        r1 = _payload(root1)
        r2 = _payload(root2)
        assert r1["demonstration_id"] == r2["demonstration_id"], (
            f"Deduped call must return same demonstration_id. "
            f"first={r1['demonstration_id']} second={r2['demonstration_id']}"
        )

    def test_second_identical_call_adds_no_new_db_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """DB row count stays at 1 after two identical calls."""
        args = _demo_args()
        _call_raw(server_rw, _TOOL_NAME, args)
        count_after_first = _count_demonstrations(backend)
        _call_raw(server_rw, _TOOL_NAME, args)
        count_after_second = _count_demonstrations(backend)
        assert count_after_first == count_after_second == 1, (
            f"Expected exactly 1 row after two identical calls; "
            f"after first={count_after_first}, after second={count_after_second}"
        )

    def test_deduped_call_still_writes_audit_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """Even a deduped call emits an mcp_audit row for traceability."""
        args = _demo_args()
        before = _count_audit_rows(backend)
        _call_raw(server_rw, _TOOL_NAME, args)
        _call_raw(server_rw, _TOOL_NAME, args)
        after = _count_audit_rows(backend)
        assert after == before + 2, (
            f"Expected 2 audit rows (one per call, including dedup); got {after - before}"
        )

    def test_different_query_not_deduped(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """Two calls with different queries produce two distinct rows."""
        args1 = _demo_args(query="First distinct question about physics.")
        args2 = _demo_args(query="Second distinct question about chemistry.")
        _call_raw(server_rw, _TOOL_NAME, args1)
        root2 = _call_raw(server_rw, _TOOL_NAME, args2)
        assert not _is_error(root2)
        result2 = _payload(root2)
        assert result2["deduped"] is False, "Different queries must not be considered duplicates"
        assert _count_demonstrations(backend) == 2, (
            "Expected 2 distinct rows for two different queries"
        )


# ===========================================================================
# 5. Source taxonomy validation
# ===========================================================================


class TestSourceTaxonomy:
    """The source field must come from the SDFTSource enum."""

    @pytest.mark.parametrize("source", sorted(_VALID_SOURCES))
    def test_valid_source_accepted(self, backend: SQLiteBackend, seeded: dict, source: str) -> None:
        """Every defined SDFTSource value is accepted by the tool."""
        server = _build_server(backend, writes_enabled=True)
        # Use a unique query per source to avoid content_hash collision.
        args = _demo_args(source=source, query=f"Unique query for source={source!r}")
        root = _call_raw(server, _TOOL_NAME, args)
        assert not _is_error(root), f"Valid source {source!r} was rejected: {_error_text(root)}"

    def test_invalid_source_returns_error(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """An unrecognised source value must return isError=True."""
        args = _demo_args(source="not_a_real_source_value_xyz")
        root = _call_raw(server_rw, _TOOL_NAME, args)
        assert _is_error(root), (
            f"Expected isError=True for invalid source; got payload: {_payload(root)}"
        )

    def test_sdft_source_enum_importable(self) -> None:
        """SDFTSource enum must be importable from corpus_forge.sdft.sources."""
        from corpus_forge.sdft.sources import SDFTSource

        assert SDFTSource is not None

    def test_sdft_source_enum_covers_all_expected_values(self) -> None:
        """SDFTSource enum must cover all eight required source names."""
        from corpus_forge.sdft.sources import SDFTSource

        enum_values = {e.value for e in SDFTSource}
        missing = _VALID_SOURCES - enum_values
        assert not missing, f"SDFTSource enum is missing these required values: {missing}"


# ===========================================================================
# 6. trace_id round-trip
# ===========================================================================


class TestTraceIdRoundTrip:
    """trace_id is stored and returned if provided."""

    def test_trace_id_stored_in_db(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """trace_id appears verbatim in the sdft_demonstrations row."""
        trace = "trace-q1-test-001"
        root = _call_raw(server_rw, _TOOL_NAME, _demo_args(trace_id=trace))
        assert not _is_error(root)
        result = _payload(root)
        demo_id = result["demonstration_id"]
        rows = backend._execute(
            "SELECT trace_id FROM sdft_demonstrations WHERE id = ?",
            (demo_id,),
        )
        assert rows, "Expected row in sdft_demonstrations"
        assert rows[0]["trace_id"] == trace, (
            f"Expected trace_id={trace!r}; got {rows[0]['trace_id']!r}"
        )

    def test_trace_id_null_when_omitted(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """trace_id is NULL in DB when not passed."""
        root = _call_raw(server_rw, _TOOL_NAME, _demo_args())
        assert not _is_error(root)
        result = _payload(root)
        demo_id = result["demonstration_id"]
        rows = backend._execute(
            "SELECT trace_id FROM sdft_demonstrations WHERE id = ?",
            (demo_id,),
        )
        assert rows
        assert rows[0]["trace_id"] is None, (
            f"Expected trace_id=NULL when omitted; got {rows[0]['trace_id']!r}"
        )


# ===========================================================================
# 7. FK violation on bad dataset
# ===========================================================================


class TestFKViolation:
    """Passing a dataset that does not exist must return isError=True."""

    def test_unknown_dataset_returns_error(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """record_demonstration with a non-existent dataset returns isError=True."""
        args = _demo_args(dataset="nonexistent-dataset-xyz-9999")
        root = _call_raw(server_rw, _TOOL_NAME, args)
        assert _is_error(root), (
            f"Expected isError=True for unknown dataset; got payload: {_payload(root)}"
        )

    def test_unknown_dataset_error_text_is_descriptive(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Server
    ) -> None:
        """Error message for unknown dataset names the dataset or says 'not found'."""
        args = _demo_args(dataset="no-such-dataset-abc")
        root = _call_raw(server_rw, _TOOL_NAME, args)
        text = _error_text(root).lower()
        assert "dataset" in text or "not found" in text or "unknown" in text, (
            f"Expected descriptive error for unknown dataset; got: {text!r}"
        )


# ---------------------------------------------------------------------------
# Postgres parity — verifies the record_demonstration write tool works
# end-to-end against a real psycopg connection. Pins:
# - corpus_forge.sdft.capture.record_demonstration commits the Postgres
#   transaction (regression: prior versions left the INSERT in an open
#   transaction so the row was rolled back when _get_connection closed).
# - INSERT ... ON CONFLICT DO NOTHING dedup path works on PG.
# - dataset_id resolution via backend.find_dataset_id_by_name works on PG.
# Requires Docker + testcontainers via the shared `pg_dsn` session fixture.
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestRecordDemonstrationPostgres:
    """Same dispatch contract, exercised against a Postgres backend."""

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
    def _seed_dataset_pg(pg_dsn: str, name: str = "pg-sdft-ds") -> int:
        import psycopg

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind, description) "
                "VALUES (%s, %s, %s) RETURNING id",
                (name, "text", "Postgres parity for record_demonstration"),
            )
            ds_id = cur.fetchone()[0]
            conn.commit()
        return int(ds_id)

    @staticmethod
    def _pg_backend(pg_dsn: str) -> object:
        from corpus_forge.backends.postgres import PostgresBackend

        return PostgresBackend(dsn=pg_dsn, schema="corpus")

    def test_pg_record_demonstration_writes_row(self, pg_dsn: str) -> None:
        """Happy path on Postgres — INSERT is committed and durable."""
        self._reset_and_migrate(pg_dsn)
        ds_id = self._seed_dataset_pg(pg_dsn, name="pg-sdft-ds")
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        root = _call_raw(server, _TOOL_NAME, _demo_args(dataset="pg-sdft-ds"))
        assert not _is_error(root), _error_text(root)
        result = _payload(root)
        assert "demonstration_id" in result
        assert result["deduped"] is False
        rows = backend._execute(
            "SELECT dataset_id, source FROM corpus.sdft_demonstrations WHERE id = %s",
            (result["demonstration_id"],),
        )
        assert rows
        assert int(rows[0]["dataset_id"]) == ds_id

    def test_pg_record_demonstration_dedupes_on_repeat(self, pg_dsn: str) -> None:
        """Identical payloads return the same id; only one row exists."""
        self._reset_and_migrate(pg_dsn)
        self._seed_dataset_pg(pg_dsn, name="pg-sdft-dedup")
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        args = _demo_args(dataset="pg-sdft-dedup")
        first = _payload(_call_raw(server, _TOOL_NAME, args))
        second = _payload(_call_raw(server, _TOOL_NAME, args))
        assert first["demonstration_id"] == second["demonstration_id"]
        assert first["deduped"] is False
        assert second["deduped"] is True
        rows = backend._execute("SELECT COUNT(*) AS n FROM corpus.sdft_demonstrations")
        assert int(rows[0]["n"]) == 1

    def test_pg_record_demonstration_unknown_dataset_returns_error(self, pg_dsn: str) -> None:
        """Unknown dataset name surfaces as a clean error payload."""
        self._reset_and_migrate(pg_dsn)
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        root = _call_raw(server, _TOOL_NAME, _demo_args(dataset="no-such-dataset-pg-xyz"))
        assert _is_error(root)
        text = _error_text(root).lower()
        assert "dataset" in text or "not found" in text or "unknown" in text

    def test_pg_record_demonstration_invalid_source_rejected(self, pg_dsn: str) -> None:
        """Source not in SDFTSource is rejected before touching the DB."""
        self._reset_and_migrate(pg_dsn)
        self._seed_dataset_pg(pg_dsn, name="pg-sdft-invalid-src")
        backend = self._pg_backend(pg_dsn)
        server = build_server(
            retriever_builder=lambda: _BackedRetriever(backend),  # type: ignore[arg-type]
            writes_enabled=True,
        )
        args = _demo_args(
            dataset="pg-sdft-invalid-src",
            source="__not_a_real_source__",
        )
        root = _call_raw(server, _TOOL_NAME, args)
        assert _is_error(root)
        assert "SDFTSource" in _error_text(root)
