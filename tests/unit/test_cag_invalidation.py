"""P3-T3 — CAG cache-invalidation contract tests.

Contracts under test
--------------------
1. ``commit_curation`` on any chunk X calls
   ``corpus_forge.cag.cache.invalidate(root, dataset, content_hash)``
   to remove cache files that were built over that chunk's ``content_hash``.

2. ``invalidate_for_chunk(chunk_id, dataset_id, *, root, conn) -> int``
   looks up the chunk's ``content_hash`` then calls ``invalidate``.

3. Invalidation is **best-effort**: failures (missing directory, permission
   error) do NOT fail the curation commit.  Errors are logged, the audit row
   remains intact, and the function returns 0 for failures.

4. ``invalidate_for_chunk`` returns 0 when the chunk has no ``content_hash``.

These tests are *RED*: ``corpus_forge.cag`` does not exist yet.  Every test
fails with ``ModuleNotFoundError: No module named 'corpus_forge.cag'`` or
``AttributeError`` on the missing hook function.

Run:
    uv run pytest tests/unit/test_cag_invalidation.py -x 2>&1 | tail -5
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# ── CAG module under test (does not exist yet → ImportError = correct RED) ──
from corpus_forge.cag.cache import invalidate, invalidate_for_chunk  # type: ignore[import]

# ── In-process MCP harness (same pattern as test_mcp_server_enrichment.py) ──
mcp = pytest.importorskip("mcp")

from corpus_forge.backends.sqlite import SQLiteBackend  # noqa: E402
from corpus_forge.mcp.server import build_server  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    import sqlite3
    from collections.abc import Callable

    from mcp.server import Server

# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------

_CHUNK_TEXT_A = "The eigenvalue decomposition yields orthonormal eigenvectors."
_CHUNK_TEXT_B = "Gradient descent converges given a Lipschitz-continuous gradient."
_DATASET_NAME = "p3-test-ds"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_backend(backend: SQLiteBackend) -> dict[str, object]:
    """Insert one dataset + two chunks; return their ids and content_hashes."""
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind) VALUES (?, ?) RETURNING id",
            (_DATASET_NAME, "text"),
        ).fetchone()[0]

        document_id = conn.execute(
            "INSERT INTO documents"
            " (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                dataset_id,
                "vault://p3/doc.md",
                _sha256("doc-body"),
                "P3 Doc",
                "doc body",
                "{}",
            ),
        ).fetchone()[0]

        hash_a = _sha256(_CHUNK_TEXT_A)
        chunk_id_a = conn.execute(
            "INSERT INTO chunks"
            " (document_id, chunk_index, text, content_hash, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (document_id, 0, _CHUNK_TEXT_A, hash_a, "{}"),
        ).fetchone()[0]

        hash_b = _sha256(_CHUNK_TEXT_B)
        chunk_id_b = conn.execute(
            "INSERT INTO chunks"
            " (document_id, chunk_index, text, content_hash, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (document_id, 1, _CHUNK_TEXT_B, hash_b, "{}"),
        ).fetchone()[0]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id_a": chunk_id_a,
        "hash_a": hash_a,
        "chunk_id_b": chunk_id_b,
        "hash_b": hash_b,
        "dataset_name": _DATASET_NAME,
    }


# ---------------------------------------------------------------------------
# Minimal retriever + MCP server factory
# ---------------------------------------------------------------------------


class _StubRetriever:
    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend


def _build_mcp_server(backend: SQLiteBackend, *, writes_enabled: bool = True) -> Server:
    retriever = _StubRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=writes_enabled)


# ---------------------------------------------------------------------------
# MCP call helper (mirrors test_mcp_server_enrichment.py)
# ---------------------------------------------------------------------------


def _call_tool(server: Server, name: str, arguments: dict[str, object]) -> object:
    """Fire an MCP tool call and return the raw result root."""

    async def _run() -> object:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers.get(CallToolRequest)
        assert handler is not None
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        return wrapper.root

    return asyncio.run(_run())


def _payload(result: object) -> dict[str, object]:
    if getattr(result, "structuredContent", None) is not None:
        return dict(result.structuredContent)
    return json.loads("".join(getattr(b, "text", "") for b in getattr(result, "content", [])))


# ---------------------------------------------------------------------------
# Cache-file helpers (simulate what the CAG builder would write)
# ---------------------------------------------------------------------------


def _write_cache_file(
    root: Path,
    dataset: str,
    content_hash: str,
    payload: dict | None = None,
) -> Path:
    """Write a JSON cache file at the standard path.

    Path convention (per the Wave P3 plan):
        <root>/cag/<dataset>/<content_hash>.json
    """
    cache_dir = root / "cag" / dataset
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{content_hash}.json"
    cache_file.write_text(json.dumps(payload or {"content_hash": content_hash}))
    return cache_file


# ---------------------------------------------------------------------------
# Test class 1 — invalidate() low-level function
# ---------------------------------------------------------------------------


class TestInvalidateFunction:
    """Tests for ``corpus_forge.cag.cache.invalidate(root, dataset, content_hash)``."""

    def test_invalidate_removes_cache_file(self, tmp_path: Path) -> None:
        """Happy path: a cache file at the expected path is deleted."""
        ids = _seed_backend(_make_backend())
        cache_file = _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_a"])
        assert cache_file.exists()

        result = invalidate(tmp_path, ids["dataset_name"], ids["hash_a"])

        assert not cache_file.exists(), "invalidate() must remove the cache file"
        assert result == 1

    def test_invalidate_no_file_present_returns_zero(self, tmp_path: Path) -> None:
        """No cache file → returns 0, no exception."""
        result = invalidate(tmp_path, "some-dataset", "nonexistent-hash")
        assert result == 0

    def test_invalidate_missing_cache_root_returns_zero(self, tmp_path: Path) -> None:
        """Cache root doesn't exist → returns 0, no exception (best-effort)."""
        missing_root = tmp_path / "no-such-root"
        result = invalidate(missing_root, "some-dataset", "some-hash")
        assert result == 0

    def test_invalidate_only_removes_matching_hash(self, tmp_path: Path) -> None:
        """Multiple files present: only the target hash is removed."""
        ids = _seed_backend(_make_backend())
        dataset = ids["dataset_name"]
        file_a = _write_cache_file(tmp_path, dataset, ids["hash_a"])
        file_b = _write_cache_file(tmp_path, dataset, ids["hash_b"])

        invalidate(tmp_path, dataset, ids["hash_a"])

        assert not file_a.exists(), "file for hash_a must be removed"
        assert file_b.exists(), "file for hash_b must NOT be touched"

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
    def test_invalidate_permission_error_returns_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Simulated PermissionError → returns 0, logs a warning (best-effort)."""
        ids = _seed_backend(_make_backend())
        _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_a"])

        def _raise_permission(*args: object, **kwargs: object) -> None:
            raise PermissionError("mocked permission denied")

        monkeypatch.setattr(Path, "unlink", _raise_permission)

        with caplog.at_level(logging.WARNING, logger="corpus_forge.cag.cache"):
            result = invalidate(tmp_path, ids["dataset_name"], ids["hash_a"])

        assert result == 0, "PermissionError must NOT propagate; must return 0"
        assert any(
            "permission" in r.message.lower() or "invalidat" in r.message.lower()
            for r in caplog.records
        ), "A warning must be logged when invalidation fails"


# ---------------------------------------------------------------------------
# Test class 2 — invalidate_for_chunk() hook
# ---------------------------------------------------------------------------


class TestInvalidateForChunk:
    """Tests for ``corpus_forge.cag.cache.invalidate_for_chunk``."""

    def test_returns_zero_when_chunk_has_no_content_hash(self, tmp_path: Path) -> None:
        """chunk.content_hash is NULL → returns 0 immediately, no file ops."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        with backend._get_connection() as conn:
            conn.execute(
                "UPDATE chunks SET content_hash = NULL WHERE id = ?",
                (ids["chunk_id_a"],),
            )
            conn.commit()

        with backend._get_connection() as conn:
            result = invalidate_for_chunk(
                ids["chunk_id_a"],
                ids["dataset_id"],
                root=tmp_path,
                conn=conn,
            )

        assert result == 0

    def test_returns_one_when_cache_file_exists(self, tmp_path: Path) -> None:
        """Chunk has content_hash and matching file exists → returns 1."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_a"])

        with backend._get_connection() as conn:
            result = invalidate_for_chunk(
                ids["chunk_id_a"],
                ids["dataset_id"],
                root=tmp_path,
                conn=conn,
            )

        assert result == 1

    def test_calls_invalidate_with_correct_args(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """invalidate_for_chunk must call invalidate(root, dataset, content_hash)."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        calls: list[tuple[Path, str, str]] = []

        def _capture(root: Path, dataset: str, content_hash: str) -> int:
            calls.append((root, dataset, content_hash))
            return 0

        import corpus_forge.cag.cache as _cache_mod  # type: ignore[import]

        monkeypatch.setattr(_cache_mod, "invalidate", _capture)

        with backend._get_connection() as conn:
            invalidate_for_chunk(
                ids["chunk_id_a"],
                ids["dataset_id"],
                root=tmp_path,
                conn=conn,
            )

        assert len(calls) == 1, "invalidate must be called exactly once"
        root_arg, _dataset_arg, hash_arg = calls[0]
        assert root_arg == tmp_path
        assert hash_arg == ids["hash_a"]


# ---------------------------------------------------------------------------
# Test class 3 — commit_curation integration (MCP harness)
# ---------------------------------------------------------------------------


def _make_real_invalidate_for_chunk(root: Path) -> Callable[..., int]:
    """Factory: returns an invalidate_for_chunk closure wired to ``root``."""

    def _fn(
        chunk_id: int,
        dataset_id: int,
        *,
        root: Path = root,
        conn: sqlite3.Connection,
    ) -> int:
        row = conn.execute("SELECT name FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        dataset_name = row[0] if row else str(dataset_id)
        row2 = conn.execute("SELECT content_hash FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row2 or not row2[0]:
            return 0
        return invalidate(root, dataset_name, row2[0])

    return _fn


class TestCommitCurationTriggersInvalidation:
    """commit_curation must call invalidate_for_chunk after each write."""

    def test_cache_file_removed_after_commit_curation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Build a cache file for chunk X. Call commit_curation. File must be gone."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        cache_file = _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_a"])
        assert cache_file.exists()

        import corpus_forge.cag.cache as _cache_mod  # type: ignore[import]

        monkeypatch.setattr(
            _cache_mod, "invalidate_for_chunk", _make_real_invalidate_for_chunk(tmp_path)
        )
        monkeypatch.setenv("CF_CAG_CACHE_ROOT", str(tmp_path))

        server = _build_mcp_server(backend)
        result = _call_tool(
            server,
            "commit_curation",
            {
                "chunk_id": ids["chunk_id_a"],
                "add_labels": [{"namespace": "topic", "value": "ml"}],
            },
        )

        assert not getattr(result, "isError", False), f"commit_curation returned error: {result}"
        assert not cache_file.exists(), (
            "Cache file for chunk A must be removed after commit_curation"
        )

    def test_only_target_chunk_cache_removed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Multiple cache files: only the one for the mutated chunk is removed."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        file_a = _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_a"])
        file_b = _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_b"])

        import corpus_forge.cag.cache as _cache_mod  # type: ignore[import]

        monkeypatch.setattr(
            _cache_mod, "invalidate_for_chunk", _make_real_invalidate_for_chunk(tmp_path)
        )
        monkeypatch.setenv("CF_CAG_CACHE_ROOT", str(tmp_path))

        server = _build_mcp_server(backend)
        _call_tool(
            server,
            "commit_curation",
            {
                "chunk_id": ids["chunk_id_a"],
                "set_description": "improved desc",
            },
        )

        assert not file_a.exists(), "File for chunk A must be removed"
        assert file_b.exists(), "File for chunk B must be untouched"

    def test_no_cache_files_commit_curation_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No cache files present → commit_curation succeeds, no error."""
        backend = _make_backend()
        ids = _seed_backend(backend)

        import corpus_forge.cag.cache as _cache_mod  # type: ignore[import]

        calls: list[int] = []

        def _spy(chunk_id: int, dataset_id: int, *, root: Path, conn: sqlite3.Connection) -> int:
            calls.append(chunk_id)
            return 0

        monkeypatch.setattr(_cache_mod, "invalidate_for_chunk", _spy)
        monkeypatch.setenv("CF_CAG_CACHE_ROOT", str(tmp_path))

        server = _build_mcp_server(backend)
        result = _call_tool(
            server,
            "commit_curation",
            {
                "chunk_id": ids["chunk_id_a"],
                "set_metadata": {"reviewed": True},
            },
        )

        assert not getattr(result, "isError", False), (
            "commit_curation must succeed even with no cache"
        )
        assert calls == [ids["chunk_id_a"]], (
            "invalidate_for_chunk must still be called (best-effort)"
        )

    def test_missing_cache_root_commit_curation_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cache root absent → commit_curation succeeds, no ERROR-level log."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        missing_root = tmp_path / "no-such-cache-root"
        monkeypatch.setenv("CF_CAG_CACHE_ROOT", str(missing_root))

        server = _build_mcp_server(backend)

        with caplog.at_level(logging.ERROR, logger="corpus_forge"):
            result = _call_tool(
                server,
                "commit_curation",
                {
                    "chunk_id": ids["chunk_id_a"],
                    "add_labels": [{"namespace": "quality", "value": "good"}],
                },
            )

        assert not getattr(result, "isError", False), (
            "Missing cache root must NOT cause commit_curation to fail"
        )
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, f"Unexpected ERROR log: {error_records}"

    def test_permission_error_during_invalidation_does_not_fail_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Simulated PermissionError during invalidation → commit still succeeds."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        _write_cache_file(tmp_path, ids["dataset_name"], ids["hash_a"])

        import corpus_forge.cag.cache as _cache_mod  # type: ignore[import]

        def _raise(chunk_id: int, dataset_id: int, *, root: Path, conn: sqlite3.Connection) -> int:
            raise PermissionError("simulated permission denied on cache dir")

        monkeypatch.setattr(_cache_mod, "invalidate_for_chunk", _raise)
        monkeypatch.setenv("CF_CAG_CACHE_ROOT", str(tmp_path))

        server = _build_mcp_server(backend)

        with caplog.at_level(logging.WARNING, logger="corpus_forge"):
            result = _call_tool(
                server,
                "commit_curation",
                {
                    "chunk_id": ids["chunk_id_a"],
                    "set_description": "new description",
                },
            )

        assert not getattr(result, "isError", False), (
            "PermissionError in invalidate_for_chunk must NOT fail commit_curation"
        )
        warn_or_above = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warn_or_above, "At least one warning must be logged when invalidation raises"

    def test_audit_row_exists_after_commit_regardless_of_invalidation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even when invalidation raises, the audit row must be written."""
        backend = _make_backend()
        ids = _seed_backend(backend)

        import corpus_forge.cag.cache as _cache_mod  # type: ignore[import]

        def _raise(chunk_id: int, dataset_id: int, *, root: Path, conn: sqlite3.Connection) -> int:
            raise OSError("disk full simulation")

        monkeypatch.setattr(_cache_mod, "invalidate_for_chunk", _raise)
        monkeypatch.setenv("CF_CAG_CACHE_ROOT", str(tmp_path))

        server = _build_mcp_server(backend)
        result = _call_tool(
            server,
            "commit_curation",
            {
                "chunk_id": ids["chunk_id_a"],
                "add_labels": [{"namespace": "quality", "value": "verified"}],
            },
        )

        assert not getattr(result, "isError", False), "commit_curation must succeed"
        payload = _payload(result)
        assert payload.get("audit_ids"), (
            f"audit_ids must be non-empty even when CAG invalidation fails; got payload: {payload}"
        )

    def test_invalidate_for_chunk_returns_zero_for_null_content_hash(
        self,
        tmp_path: Path,
    ) -> None:
        """invalidate_for_chunk returns 0 when the chunk has content_hash=NULL."""
        backend = _make_backend()
        ids = _seed_backend(backend)
        with backend._get_connection() as conn:
            conn.execute(
                "UPDATE chunks SET content_hash = NULL WHERE id = ?",
                (ids["chunk_id_a"],),
            )
            conn.commit()

        _write_cache_file(tmp_path, ids["dataset_name"], "would-be-hash")

        with backend._get_connection() as conn:
            result = invalidate_for_chunk(
                ids["chunk_id_a"],
                ids["dataset_id"],
                root=tmp_path,
                conn=conn,
            )

        assert result == 0
