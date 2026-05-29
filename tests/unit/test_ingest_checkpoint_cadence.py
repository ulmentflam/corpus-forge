"""Unit tests — SR-T9: checkpoint cadence in ingest_once.

RED condition
-------------
Neither ``corpus_forge.ingest._CHECKPOINT_INTERVAL_S`` nor
``backend.update_ingest_run_progress`` (called at cadence) exist yet.
Every test here will fail with ``AttributeError`` or ``AssertionError``
until SR-G5 implements them.

Contract under test
-------------------
1. ``corpus_forge.ingest._CHECKPOINT_INTERVAL_S`` is a module-level float
   constant equal to 5.0.

2. Inside ``ingest_once``, ``backend.update_ingest_run`` is called:
   - at every source boundary (start + finish), unconditionally.
   - inside the per-doc loop only when
     ``time.monotonic() - _last_checkpoint_at >= _CHECKPOINT_INTERVAL_S``.

3. With a monkeypatched ``time.monotonic`` that advances 0.001 s per call,
   a 1000-doc run produces AT MOST ``ceil(1000 * 0.001 / 5.0) + 2`` total
   checkpoint writes (the "+2" accommodates the start-boundary and the
   end-boundary call; the cadence guard must suppress per-doc writes in
   between).  The "at most" bound is loose enough that a slightly different
   implementation strategy still passes; the important invariant is that
   it is nowhere near 1000 (one per doc).

4. Checkpoint writes are wrapped in try/except — a backend that raises on
   ``update_ingest_run`` MUST NOT kill the ingest loop; documents continue
   to be processed.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

import corpus_forge.ingest as _ingest_module
from corpus_forge.sources.base import RawDocument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_doc(n: int) -> RawDocument:
    """Return a minimal RawDocument with a unique source_uri."""
    return RawDocument(
        source_uri=f"fake://doc_{n}.txt",
        content_hash=f"hash_{n}",
        text=f"Document number {n} — short content",
        title=f"Doc {n}",
        modified_at=float(n),
        metadata={},
        labels=[],
    )


def _make_fake_source(n_docs: int) -> Any:
    """Return a mock Source that yields *n_docs* RawDocuments."""

    class _FakeSource:
        name = "fake"
        dataset_kind = "text"

        def scan(self) -> Iterator[RawDocument]:
            for i in range(n_docs):
                yield _make_raw_doc(i)

        def watch(self, on_event) -> None:  # pragma: no cover
            pass

        def identity(self) -> str:
            return "fake://test"

    return _FakeSource()


def _make_minimal_config(source) -> Any:
    """Build a minimal MagicMock config wired to one fake source."""
    source_config = MagicMock()
    source_config.plugin = "fake"
    source_config.chunker = "markdown"
    source_config.chunker_config = {}
    # max_rows / max_bytes absent so cap enforcement is skipped
    del source_config.max_rows
    del source_config.max_bytes
    source_config.max_scan_age = 0.0

    dataset_config = MagicMock()
    dataset_config.name = "test_dataset"
    dataset_config.kind = "text"
    dataset_config.description = "unit-test fixture"
    dataset_config.sources = [source_config]

    config = MagicMock()
    config.datasets = [dataset_config]
    config.embedders = []
    config.backend.kind = "sqlite"
    config.backend.dsn = ":memory:"
    config.backend.schema = "corpus"
    # ScanConfig.chunker_hard_max_chars must be a real int — ``ingest_once``
    # threads it into ``ingest_one`` and ``enforce_chunk_hard_max`` rejects
    # non-int values (a MagicMock auto-attr would trip the gt=0 check).
    config.scan.chunker_hard_max_chars = 32768
    return config, source_config


# ---------------------------------------------------------------------------
# Test 1 — constant existence and value
# ---------------------------------------------------------------------------


class TestCheckpointIntervalConstant:
    """_CHECKPOINT_INTERVAL_S must exist as a module-level constant == 5.0."""

    def test_constant_exists(self) -> None:
        assert hasattr(_ingest_module, "_CHECKPOINT_INTERVAL_S"), (
            "corpus_forge.ingest._CHECKPOINT_INTERVAL_S not found — "
            "add the module-level constant (SR-G5)"
        )

    def test_constant_value_is_5_0(self) -> None:
        interval = getattr(_ingest_module, "_CHECKPOINT_INTERVAL_S", None)
        assert interval == 5.0, f"_CHECKPOINT_INTERVAL_S must be 5.0, got {interval!r}"

    def test_constant_is_float(self) -> None:
        interval = getattr(_ingest_module, "_CHECKPOINT_INTERVAL_S", None)
        assert isinstance(interval, float), (
            f"_CHECKPOINT_INTERVAL_S must be a float, got {type(interval)!r}"
        )


# ---------------------------------------------------------------------------
# Test 2 — cadence: checkpoint calls << doc count
# ---------------------------------------------------------------------------


class TestCheckpointCadence:
    """update_ingest_run call count is bounded by wall-clock cadence, not doc count."""

    def _run_ingest_with_patched_monotonic(
        self,
        monkeypatch: pytest.MonkeyPatch,
        n_docs: int,
        step_s: float,
    ) -> tuple[MagicMock, int]:
        """Run ingest_once with a rigged monotonic clock.

        Returns (backend_mock, update_ingest_run_call_count).

        ``step_s`` is how many seconds each call to ``time.monotonic()``
        advances the clock.  With ``n_docs=1000`` and ``step_s=0.001``
        the total elapsed time is 1.0 s which yields at most
        ``ceil(1.0 / 5.0) + 2 = 3`` checkpoint writes (boundary x 2 +
        cadence x 0, since 1.0 s < 5.0 s -- the cadence guard fires at
        most once every 5 s).
        """
        fake_source = _make_fake_source(n_docs)
        config, _source_config = _make_minimal_config(fake_source)

        # Backend mock
        backend = MagicMock()
        backend_ctx = MagicMock()
        backend_ctx.__enter__ = MagicMock(return_value=None)
        backend_ctx.__exit__ = MagicMock(return_value=None)
        backend.lock_source.return_value = backend_ctx
        backend.get_hash.return_value = None
        backend.register_embedder.return_value = 1
        backend.chunks_missing_embedding.return_value = []
        backend.get_or_create_dataset.return_value = 1
        backend.register_source.return_value = None
        backend.upsert_document.return_value = None
        # start_ingest_run / finish_ingest_run needed for SR-G5 wiring
        backend.start_ingest_run.return_value = None
        backend.update_ingest_run.return_value = None
        backend.finish_ingest_run.return_value = None

        # Monotonic clock sequence: each call advances by step_s
        _counter = [0]

        def _fake_monotonic() -> float:
            val = _counter[0] * step_s
            _counter[0] += 1
            return val

        # Patch time.monotonic inside the ingest module
        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fake_monotonic)

        # Patch the backend constructors so ingest_once gets our mock
        monkeypatch.setattr(
            "corpus_forge.ingest.SQLiteBackend",
            lambda **_kw: backend,
        )
        monkeypatch.setattr(
            "corpus_forge.ingest.PostgresBackend",
            lambda **_kw: backend,
            raising=False,
        )
        # Stub migrate to no-op
        backend.migrate.return_value = None

        # Stub _instantiate_source to return our fake source
        monkeypatch.setattr(
            "corpus_forge.ingest._instantiate_source",
            lambda _sc, **_kw: fake_source,
        )

        # Stub _plan_ingest to return empty (no ETA needed)
        monkeypatch.setattr(
            "corpus_forge.ingest._plan_ingest",
            lambda _cfg: {},
        )

        # Run
        _ingest_module.ingest_once(config)

        call_count = backend.update_ingest_run.call_count
        return backend, call_count

    def test_checkpoint_count_bounded_by_cadence_not_doc_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """1000 docs at 0.001 s/call = ~1 s total; cadence fires at most once, plus 2 boundaries."""
        n_docs = 1000
        step_s = 0.001  # 0.001 s per call -> total ~1 s
        total_elapsed = n_docs * step_s
        _interval = getattr(_ingest_module, "_CHECKPOINT_INTERVAL_S", 5.0)
        max_expected = math.ceil(total_elapsed / _interval) + 2

        _, call_count = self._run_ingest_with_patched_monotonic(monkeypatch, n_docs, step_s)

        assert call_count <= max_expected, (
            f"update_ingest_run fired {call_count} times for {n_docs} docs "
            f"(max expected: {max_expected}). "
            "Checkpoint writes must be rate-limited by _CHECKPOINT_INTERVAL_S, "
            "not emitted on every document."
        )

    def test_checkpoint_count_far_below_doc_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The call count must be much less than the doc count (not per-doc)."""
        n_docs = 1000
        step_s = 0.001
        _, call_count = self._run_ingest_with_patched_monotonic(monkeypatch, n_docs, step_s)

        assert call_count < n_docs // 2, (
            f"update_ingest_run fired {call_count} times — unexpectedly close to "
            f"the {n_docs} doc count. The cadence guard is not working."
        )

    def test_checkpoint_fires_at_each_cadence_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """step_s=1.0, 20 docs (20 s total): cadence fires ~4 times inside loop + 2 boundaries."""
        n_docs = 20
        step_s = 1.0  # 1 s per call -> total ~20 s -> 4 cadence fires
        total_elapsed = n_docs * step_s
        _interval = getattr(_ingest_module, "_CHECKPOINT_INTERVAL_S", 5.0)
        max_expected = math.ceil(total_elapsed / _interval) + 2
        # Minimum: at least 2 boundary calls (start + finish)
        min_expected = 2

        _, call_count = self._run_ingest_with_patched_monotonic(monkeypatch, n_docs, step_s)

        assert call_count >= min_expected, (
            f"update_ingest_run fired {call_count} times — expected at least "
            f"{min_expected} boundary calls (start + finish of source)."
        )
        assert call_count <= max_expected, (
            f"update_ingest_run fired {call_count} times for {n_docs} docs "
            f"at 1 s/call (max expected: {max_expected})."
        )

    def test_checkpoint_at_source_start_and_finish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """At a minimum, update_ingest_run is called at least twice (source start + finish)."""
        n_docs = 1
        step_s = 0.0  # time never advances — only boundary calls expected

        _, call_count = self._run_ingest_with_patched_monotonic(monkeypatch, n_docs, step_s)

        assert call_count >= 2, (
            f"Expected at least 2 update_ingest_run calls (source boundary start + "
            f"finish) for a 1-doc source, got {call_count}."
        )


# ---------------------------------------------------------------------------
# Test 3 — checkpoint failure does not abort ingest
# ---------------------------------------------------------------------------


class TestCheckpointFailureIsolation:
    """A backend that raises on update_ingest_run must not kill the ingest loop."""

    def test_checkpoint_exception_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """update_ingest_run raising RuntimeError must not propagate to the caller."""
        n_docs = 5
        fake_source = _make_fake_source(n_docs)
        config, _ = _make_minimal_config(fake_source)

        backend = MagicMock()
        backend_ctx = MagicMock()
        backend_ctx.__enter__ = MagicMock(return_value=None)
        backend_ctx.__exit__ = MagicMock(return_value=None)
        backend.lock_source.return_value = backend_ctx
        backend.get_hash.return_value = None
        backend.register_embedder.return_value = 1
        backend.chunks_missing_embedding.return_value = []
        backend.get_or_create_dataset.return_value = 1
        backend.register_source.return_value = None
        backend.upsert_document.return_value = None
        backend.start_ingest_run.return_value = None
        backend.finish_ingest_run.return_value = None
        # Make every checkpoint write raise
        backend.update_ingest_run.side_effect = RuntimeError("simulated DB failure")
        backend.migrate.return_value = None

        monkeypatch.setattr("corpus_forge.ingest.SQLiteBackend", lambda **_kw: backend)
        monkeypatch.setattr(
            "corpus_forge.ingest.PostgresBackend", lambda **_kw: backend, raising=False
        )
        monkeypatch.setattr(
            "corpus_forge.ingest._instantiate_source", lambda _sc, **_kw: fake_source
        )
        monkeypatch.setattr("corpus_forge.ingest._plan_ingest", lambda _cfg: {})

        # Must not raise
        try:
            _ingest_module.ingest_once(config)
        except Exception as exc:
            pytest.fail(
                f"ingest_once raised {type(exc).__name__}: {exc} "
                "even though update_ingest_run exception should be swallowed."
            )

        # All docs must still have been ingested
        assert backend.upsert_document.call_count == n_docs, (
            f"Expected {n_docs} upsert_document calls, got "
            f"{backend.upsert_document.call_count}. "
            "Checkpoint exception must not short-circuit the document loop."
        )

    def test_checkpoint_exception_logged_at_debug(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A swallowed checkpoint exception must emit a DEBUG log line."""
        import logging

        n_docs = 1
        fake_source = _make_fake_source(n_docs)
        config, _ = _make_minimal_config(fake_source)

        backend = MagicMock()
        backend_ctx = MagicMock()
        backend_ctx.__enter__ = MagicMock(return_value=None)
        backend_ctx.__exit__ = MagicMock(return_value=None)
        backend.lock_source.return_value = backend_ctx
        backend.get_hash.return_value = None
        backend.register_embedder.return_value = 1
        backend.chunks_missing_embedding.return_value = []
        backend.get_or_create_dataset.return_value = 1
        backend.register_source.return_value = None
        backend.upsert_document.return_value = None
        backend.start_ingest_run.return_value = None
        backend.finish_ingest_run.return_value = None
        backend.update_ingest_run.side_effect = RuntimeError("db gone")
        backend.migrate.return_value = None

        monkeypatch.setattr("corpus_forge.ingest.SQLiteBackend", lambda **_kw: backend)
        monkeypatch.setattr(
            "corpus_forge.ingest.PostgresBackend", lambda **_kw: backend, raising=False
        )
        monkeypatch.setattr(
            "corpus_forge.ingest._instantiate_source", lambda _sc, **_kw: fake_source
        )
        monkeypatch.setattr("corpus_forge.ingest._plan_ingest", lambda _cfg: {})

        with caplog.at_level(logging.DEBUG, logger="corpus_forge.ingest"):
            _ingest_module.ingest_once(config)

        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("checkpoint" in m.lower() for m in debug_msgs), (
            "Expected a DEBUG log message mentioning 'checkpoint' when "
            "update_ingest_run raises. Got debug messages: " + repr(debug_msgs[:5])
        )


# ---------------------------------------------------------------------------
# Test 4 — module-level import smoke
# ---------------------------------------------------------------------------


class TestModuleImport:
    """Importing corpus_forge.ingest must not crash and must expose the constant."""

    def test_module_importable(self) -> None:
        import corpus_forge.ingest  # noqa: F401

    def test_constant_accessible_after_reimport(self) -> None:
        mod = importlib.import_module("corpus_forge.ingest")
        assert hasattr(mod, "_CHECKPOINT_INTERVAL_S"), (
            "_CHECKPOINT_INTERVAL_S missing after fresh import"
        )
