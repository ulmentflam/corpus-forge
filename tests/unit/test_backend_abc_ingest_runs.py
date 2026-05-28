"""Unit tests — SR-T2: StorageBackend Protocol shape for ingest-run CRUD.

These tests assert that the methods required by SR-T2 are declared on
the ``StorageBackend`` Protocol at the correct signatures.  They do NOT
exercise any real backend — they operate entirely against the Protocol
class and the ``inspect`` module.

RED condition
-------------
The seven Protocol methods below do not yet exist on
``corpus_forge.backends.base.StorageBackend``.  Every test here will
fail with ``AssertionError`` (missing attribute) or ``AttributeError``
(inspection fails) until SR-G2 adds the Protocol stubs.
"""

from __future__ import annotations

import inspect

import pytest

# ---------------------------------------------------------------------------
# Import — if base.py is unchanged this import itself succeeds; the
# individual tests then fail when they can't find the new methods.
# ---------------------------------------------------------------------------
from corpus_forge.backends.base import StorageBackend

# ── helper ──────────────────────────────────────────────────────────────────


def _protocol_method_names() -> frozenset[str]:
    """Return the set of method names directly declared on StorageBackend."""
    return frozenset(
        name for name, _ in inspect.getmembers(StorageBackend, predicate=inspect.isfunction)
    )


# ── existence tests ──────────────────────────────────────────────────────────


class TestProtocolMethodExistence:
    """Each new ingest-run method must be declared on StorageBackend."""

    def test_start_ingest_run_exists(self) -> None:
        assert "start_ingest_run" in _protocol_method_names(), (
            "StorageBackend.start_ingest_run not found — add Protocol stub (SR-G2)"
        )

    def test_update_ingest_run_exists(self) -> None:
        assert "update_ingest_run" in _protocol_method_names(), (
            "StorageBackend.update_ingest_run not found — add Protocol stub (SR-G2)"
        )

    def test_finish_ingest_run_exists(self) -> None:
        assert "finish_ingest_run" in _protocol_method_names(), (
            "StorageBackend.finish_ingest_run not found — add Protocol stub (SR-G2)"
        )

    def test_latest_ingest_run_exists(self) -> None:
        assert "latest_ingest_run" in _protocol_method_names(), (
            "StorageBackend.latest_ingest_run not found — add Protocol stub (SR-G2)"
        )

    def test_latest_unfinished_ingest_run_exists(self) -> None:
        assert "latest_unfinished_ingest_run" in _protocol_method_names(), (
            "StorageBackend.latest_unfinished_ingest_run not found — add Protocol stub (SR-G2)"
        )

    def test_upsert_ingest_run_source_exists(self) -> None:
        assert "upsert_ingest_run_source" in _protocol_method_names(), (
            "StorageBackend.upsert_ingest_run_source not found — add Protocol stub (SR-G2)"
        )

    def test_find_source_last_scanned_at_exists(self) -> None:
        assert "find_source_last_scanned_at" in _protocol_method_names(), (
            "StorageBackend.find_source_last_scanned_at not found — add Protocol stub (SR-G2)"
        )


# ── signature tests ──────────────────────────────────────────────────────────


class TestProtocolMethodSignatures:
    """Verify the parameter names and kinds for each new method.

    These tests do NOT check type annotations because ``from __future__
    import annotations`` turns every annotation into a forward-ref
    string — checking kind+name is more robust and still catches
    accidental renames or missing keyword-only markers.
    """

    def _params(self, method_name: str) -> dict[str, inspect.Parameter]:
        m = getattr(StorageBackend, method_name, None)
        assert m is not None, f"StorageBackend.{method_name} does not exist"
        sig = inspect.signature(m)
        return dict(sig.parameters)

    # start_ingest_run(self, *, run_id, host, pid, config_digest) -> None
    def test_start_ingest_run_signature(self) -> None:
        params = self._params("start_ingest_run")
        # All four must exist as keyword-only (after *,)
        for name in ("run_id", "host", "pid", "config_digest"):
            assert name in params, f"start_ingest_run missing parameter '{name}'"
            p = params[name]
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"start_ingest_run parameter '{name}' must be keyword-only "
                f"(after *), got kind={p.kind}"
            )

    # update_ingest_run(self, run_id, *, last_op=None, last_done=None, last_total=None) -> None
    def test_update_ingest_run_signature(self) -> None:
        params = self._params("update_ingest_run")
        assert "run_id" in params, "update_ingest_run missing 'run_id'"
        # run_id must be positional-or-keyword (not keyword-only)
        assert params["run_id"].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ), "update_ingest_run 'run_id' should be positional"
        for name in ("last_op", "last_done", "last_total"):
            assert name in params, f"update_ingest_run missing '{name}'"
            p = params[name]
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"update_ingest_run '{name}' must be keyword-only"
            )
            # All three default to None
            assert p.default is None, (
                f"update_ingest_run '{name}' expected default=None, got {p.default!r}"
            )

    # finish_ingest_run(self, run_id, *, status, error=None) -> None
    def test_finish_ingest_run_signature(self) -> None:
        params = self._params("finish_ingest_run")
        assert "run_id" in params, "finish_ingest_run missing 'run_id'"
        assert "status" in params, "finish_ingest_run missing 'status'"
        assert params["status"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "finish_ingest_run 'status' must be keyword-only"
        )
        assert "error" in params, "finish_ingest_run missing 'error'"
        assert params["error"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "finish_ingest_run 'error' must be keyword-only"
        )
        assert params["error"].default is None, "finish_ingest_run 'error' must default to None"

    # latest_ingest_run(self) -> dict | None
    def test_latest_ingest_run_signature(self) -> None:
        params = self._params("latest_ingest_run")
        # Only 'self'
        non_self = [n for n in params if n != "self"]
        assert non_self == [], (
            f"latest_ingest_run should have no parameters beyond self; got {non_self}"
        )

    # latest_unfinished_ingest_run(self) -> dict | None
    def test_latest_unfinished_ingest_run_signature(self) -> None:
        params = self._params("latest_unfinished_ingest_run")
        non_self = [n for n in params if n != "self"]
        assert non_self == [], (
            f"latest_unfinished_ingest_run should have no parameters beyond self; got {non_self}"
        )

    # upsert_ingest_run_source(self, *, run_id, source_uri_prefix, dataset_id, ...)
    def test_upsert_ingest_run_source_signature(self) -> None:
        params = self._params("upsert_ingest_run_source")
        required_kw = (
            "run_id",
            "source_uri_prefix",
            "dataset_id",
            "last_scanned_at",
            "docs_seen_delta",
            "docs_skipped_delta",
            "docs_failed_delta",
            "finished",
        )
        for name in required_kw:
            assert name in params, f"upsert_ingest_run_source missing '{name}'"
            p = params[name]
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"upsert_ingest_run_source '{name}' must be keyword-only"
            )

    # find_source_last_scanned_at(self, source_uri_prefix) -> datetime | None
    def test_find_source_last_scanned_at_signature(self) -> None:
        params = self._params("find_source_last_scanned_at")
        assert "source_uri_prefix" in params, (
            "find_source_last_scanned_at missing 'source_uri_prefix'"
        )


# ── IngestRunInProgressError presence ────────────────────────────────────────


class TestIngestRunInProgressErrorExists:
    """SR-G2 must add IngestRunInProgressError to the backends package.

    The test imports from ``corpus_forge.backends`` (the package __init__)
    and from ``corpus_forge.backends.base`` to match both plausible
    landing spots.  If neither has the exception the test fails.
    """

    def test_exception_importable_from_backends(self) -> None:
        try:
            from corpus_forge.backends import IngestRunInProgressError  # type: ignore[attr-defined]
        except ImportError:
            # Might live in base.py instead of __init__
            try:
                from corpus_forge.backends.base import (  # noqa: F401
                    IngestRunInProgressError,  # type: ignore[attr-defined]
                )
            except ImportError:
                pytest.fail(
                    "IngestRunInProgressError not found in corpus_forge.backends or "
                    "corpus_forge.backends.base — add it in SR-G2"
                )

    def test_exception_is_exception_subclass(self) -> None:
        try:
            from corpus_forge.backends import IngestRunInProgressError  # type: ignore[attr-defined]
        except ImportError:
            try:
                from corpus_forge.backends.base import (
                    IngestRunInProgressError,  # type: ignore[attr-defined]
                )
            except ImportError:
                pytest.fail("IngestRunInProgressError not importable")
                return
        assert issubclass(IngestRunInProgressError, Exception), (
            "IngestRunInProgressError must be a subclass of Exception"
        )
