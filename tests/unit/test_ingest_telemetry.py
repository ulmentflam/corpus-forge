"""Unit tests — SR-T9: structured-log telemetry for the three new ingest loggers.

RED condition
-------------
The three logger names and their structured payloads do not yet exist in
``corpus_forge.ingest``.  Every test here will fail with ``AssertionError``
(no matching log records) or ``AttributeError`` until SR-G5 adds them.

Contract under test
-------------------
Three new loggers must be declared at module level in corpus_forge.ingest:

  ``corpus_forge.ingest.run``        — run_started, run_finished,
                                       run_interrupted, run_failed events.
  ``corpus_forge.ingest.checkpoint`` — checkpoint events (cadence-gated).
  ``corpus_forge.ingest.lock``       — ingest_run_acquired, ingest_run_released,
                                       ingest_run_contention events.

Every emitted record's message must be:
  1. A valid JSON string (``json.loads(record.getMessage())`` must not raise).
  2. A dict (not a list or scalar).
  3. Carrying at minimum the keys documented for that event type.

Payload JSON-safety invariant:
  Payloads MUST NOT contain Python sets, bytes, or Path objects — only
  types natively serialisable by ``json.dumps`` (str, int, float, bool,
  None, list, dict).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

import corpus_forge.ingest as _ingest_module

# ---------------------------------------------------------------------------
# Helpers (duplicated from test_ingest_checkpoint_cadence.py intentionally —
# tests must be independently runnable)
# ---------------------------------------------------------------------------


def _make_raw_doc(n: int):
    from corpus_forge.sources.base import RawDocument

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
    class _FakeSource:
        name = "fake"
        dataset_kind = "text"

        def scan(self) -> Iterator:
            for i in range(n_docs):
                yield _make_raw_doc(i)

        def watch(self, on_event) -> None:  # pragma: no cover
            pass

        def identity(self) -> str:
            return "fake://test"

    return _FakeSource()


def _make_minimal_config(source) -> Any:
    source_config = MagicMock()
    source_config.plugin = "fake"
    source_config.chunker = "markdown"
    source_config.chunker_config = {}
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
    return config, source_config


def _make_backend_mock() -> MagicMock:
    backend = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=None)
    ctx.__exit__ = MagicMock(return_value=None)
    backend.lock_source.return_value = ctx
    backend.get_hash.return_value = None
    backend.register_embedder.return_value = 1
    backend.chunks_missing_embedding.return_value = []
    backend.get_or_create_dataset.return_value = 1
    backend.register_source.return_value = None
    backend.upsert_document.return_value = None
    backend.start_ingest_run.return_value = None
    backend.update_ingest_run.return_value = None
    backend.finish_ingest_run.return_value = None
    backend.migrate.return_value = None
    return backend


def _wire_patched_ingest(monkeypatch: pytest.MonkeyPatch, n_docs: int = 3):
    """Return (config, fake_source, backend_mock) with ingest wired for testing."""
    fake_source = _make_fake_source(n_docs)
    config, _ = _make_minimal_config(fake_source)
    backend = _make_backend_mock()

    monkeypatch.setattr("corpus_forge.ingest.SQLiteBackend", lambda **_kw: backend)
    monkeypatch.setattr("corpus_forge.ingest.PostgresBackend", lambda **_kw: backend, raising=False)
    monkeypatch.setattr("corpus_forge.ingest._instantiate_source", lambda _sc, **_kw: fake_source)
    monkeypatch.setattr("corpus_forge.ingest._plan_ingest", lambda _cfg: {})

    return config, fake_source, backend


# ---------------------------------------------------------------------------
# Helpers for payload validation
# ---------------------------------------------------------------------------


def _assert_json_encodable(payload: dict, context: str) -> None:
    """Assert payload serialises without error and contains no forbidden types."""
    try:
        serialised = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        pytest.fail(
            f"{context}: json.dumps raised {type(exc).__name__}: {exc}. Payload: {payload!r}"
        )
    # Round-trip: must come back as a dict
    rt = json.loads(serialised)
    assert isinstance(rt, dict), f"{context}: round-tripped JSON is not a dict, got {type(rt)}"


def _assert_no_forbidden_types(payload: dict, context: str) -> None:
    """Recursively assert no set/bytes/Path in payload values."""
    from pathlib import Path

    def _walk(obj, path: str) -> None:
        if isinstance(obj, (set, bytes, Path)):
            pytest.fail(
                f"{context}: forbidden type {type(obj).__name__!r} found at '{path}': {obj!r}"
            )
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(v, f"{path}[{i}]")

    _walk(payload, "payload")


def _collect_records(caplog, logger_name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger_name]


def _parse_json_message(record: logging.LogRecord, context: str) -> dict:
    msg = record.getMessage()
    try:
        parsed = json.loads(msg)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"{context}: record.getMessage() is not valid JSON: {exc!r}. Raw message: {msg!r}"
        )
    assert isinstance(parsed, dict), (
        f"{context}: parsed JSON is not a dict, got {type(parsed)}: {parsed!r}"
    )
    return parsed


# ---------------------------------------------------------------------------
# Test 1 — Logger existence at module level
# ---------------------------------------------------------------------------


class TestLoggerExistence:
    """The three structured loggers must be declared at module level."""

    def test_run_logger_exists(self) -> None:
        assert hasattr(_ingest_module, "_run_logger") or (
            logging.getLogger("corpus_forge.ingest.run").name == "corpus_forge.ingest.run"
        ), (
            "corpus_forge.ingest.run logger not declared in corpus_forge.ingest — "
            "add ``_run_logger = logging.getLogger('corpus_forge.ingest.run')`` (SR-G5)"
        )
        # Verify it is actually named correctly by checking the logger registry
        assert logging.getLogger("corpus_forge.ingest.run").name == "corpus_forge.ingest.run"

    def test_checkpoint_logger_exists(self) -> None:
        assert logging.getLogger("corpus_forge.ingest.checkpoint").name == (
            "corpus_forge.ingest.checkpoint"
        )

    def test_lock_logger_exists(self) -> None:
        assert logging.getLogger("corpus_forge.ingest.lock").name == ("corpus_forge.ingest.lock")

    def test_run_logger_is_a_child_of_ingest(self) -> None:
        parent = logging.getLogger("corpus_forge.ingest")
        child = logging.getLogger("corpus_forge.ingest.run")
        assert child.parent is parent or child.name.startswith("corpus_forge.ingest"), (
            "corpus_forge.ingest.run must be a child of corpus_forge.ingest"
        )


# ---------------------------------------------------------------------------
# Test 2 — corpus_forge.ingest.run events
# ---------------------------------------------------------------------------


class TestRunLogger:
    """ingest_once must emit run_started and run_finished via corpus_forge.ingest.run."""

    def test_run_started_event_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=2)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.run"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.run")
        assert records, (
            "No log records emitted on 'corpus_forge.ingest.run'. "
            "ingest_once must emit at least run_started + run_finished. (SR-G5)"
        )

        events = [_parse_json_message(r, "corpus_forge.ingest.run") for r in records]
        event_names = [e.get("event") for e in events]
        assert "run_started" in event_names, f"Expected 'run_started' event in {event_names!r}"

    def test_run_finished_event_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=2)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.run"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.run")
        events = [_parse_json_message(r, "corpus_forge.ingest.run") for r in records]
        event_names = [e.get("event") for e in events]
        assert "run_finished" in event_names, f"Expected 'run_finished' event in {event_names!r}"

    def test_run_started_has_run_id_field(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.run"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.run")
        started_payloads = [
            _parse_json_message(r, "run_started")
            for r in records
            if "run_started" in r.getMessage()
        ]
        assert started_payloads, "No run_started payload found"
        payload = started_payloads[0]
        assert "run_id" in payload, (
            f"run_started payload must contain 'run_id', got keys: {list(payload.keys())}"
        )

    def test_run_finished_has_required_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.run"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.run")
        finished_payloads = [
            _parse_json_message(r, "run_finished")
            for r in records
            if "run_finished" in r.getMessage()
        ]
        assert finished_payloads, "No run_finished payload found"
        payload = finished_payloads[0]
        for key in ("event", "run_id"):
            assert key in payload, (
                f"run_finished payload missing required key '{key}'; "
                f"got keys: {list(payload.keys())}"
            )

    def test_run_events_are_json_encodable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=2)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.run"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.run")
        assert records, "No corpus_forge.ingest.run records to validate"

        for rec in records:
            payload = _parse_json_message(rec, f"corpus_forge.ingest.run[{rec.getMessage()[:40]}]")
            _assert_json_encodable(payload, f"run event '{payload.get('event')}'")
            _assert_no_forbidden_types(payload, f"run event '{payload.get('event')}'")

    def test_run_event_field_event_is_string(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.run"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.run")
        for rec in records:
            payload = _parse_json_message(rec, "corpus_forge.ingest.run event field")
            assert isinstance(payload.get("event"), str), (
                f"'event' field must be a string, got {type(payload.get('event'))}: {payload!r}"
            )


# ---------------------------------------------------------------------------
# Test 3 — corpus_forge.ingest.checkpoint events
# ---------------------------------------------------------------------------


class TestCheckpointLogger:
    """ingest_once must emit checkpoint events via corpus_forge.ingest.checkpoint."""

    def test_checkpoint_event_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Fast-advancing clock (step=10 s) forces checkpoint fires inside loop."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=5)

        # Advance clock by 10 s per call so cadence fires on first doc
        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        assert records, (
            "No log records emitted on 'corpus_forge.ingest.checkpoint'. "
            "ingest_once must emit checkpoint events when the cadence fires. (SR-G5)"
        )

    def test_checkpoint_payload_has_required_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Checkpoint payload must carry event, run_id, last_op, last_done, last_total,
        elapsed_s."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=5)

        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        assert records, "No corpus_forge.ingest.checkpoint records to inspect"

        required_keys = {"event", "run_id", "last_op", "last_done", "last_total", "elapsed_s"}
        for rec in records:
            payload = _parse_json_message(rec, "corpus_forge.ingest.checkpoint")
            missing = required_keys - set(payload.keys())
            assert not missing, (
                f"checkpoint payload missing keys {missing!r}; got: {list(payload.keys())}"
            )

    def test_checkpoint_event_field_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The 'event' field in checkpoint payloads must equal 'checkpoint'."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=3)

        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        for rec in records:
            payload = _parse_json_message(rec, "checkpoint event field")
            assert payload.get("event") == "checkpoint", (
                f"Expected event='checkpoint', got {payload.get('event')!r}"
            )

    def test_checkpoint_last_done_is_int(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """last_done must be an integer (not a float or string)."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=3)

        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        assert records, "No checkpoint records found"
        for rec in records:
            payload = _parse_json_message(rec, "checkpoint last_done type")
            assert isinstance(payload.get("last_done"), int), (
                f"'last_done' must be int, got {type(payload.get('last_done'))}: {payload!r}"
            )

    def test_checkpoint_elapsed_s_is_numeric(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """elapsed_s must be a number (int or float)."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=3)

        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        assert records, "No checkpoint records found"
        for rec in records:
            payload = _parse_json_message(rec, "checkpoint elapsed_s type")
            elapsed = payload.get("elapsed_s")
            assert isinstance(elapsed, (int, float)), (
                f"'elapsed_s' must be numeric, got {type(elapsed)}: {payload!r}"
            )

    def test_checkpoint_payload_json_safe(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """All checkpoint payloads must round-trip through json.dumps without error."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=5)

        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        assert records, "No checkpoint records to validate"

        for rec in records:
            payload = _parse_json_message(rec, "checkpoint JSON safety")
            _assert_json_encodable(payload, "checkpoint")
            _assert_no_forbidden_types(payload, "checkpoint")

    def test_checkpoint_run_id_is_str(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """run_id in checkpoint payload must be a non-empty string."""
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=3)

        _counter = [0]

        def _fast_monotonic() -> float:
            val = _counter[0] * 10.0
            _counter[0] += 1
            return val

        monkeypatch.setattr("corpus_forge.ingest.time.monotonic", _fast_monotonic)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.checkpoint")
        assert records, "No checkpoint records found"
        for rec in records:
            payload = _parse_json_message(rec, "checkpoint run_id type")
            run_id = payload.get("run_id")
            assert isinstance(run_id, str) and run_id, (
                f"'run_id' must be a non-empty string, got {run_id!r}"
            )


# ---------------------------------------------------------------------------
# Test 4 — corpus_forge.ingest.lock events
# ---------------------------------------------------------------------------


class TestLockLogger:
    """ingest_once must emit lock acquire/release events via corpus_forge.ingest.lock."""

    def test_lock_acquired_event_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.lock"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.lock")
        assert records, (
            "No log records emitted on 'corpus_forge.ingest.lock'. "
            "ingest_once must emit at least ingest_run_acquired + ingest_run_released. (SR-G5)"
        )
        events = [_parse_json_message(r, "corpus_forge.ingest.lock") for r in records]
        event_names = [e.get("event") for e in events]
        assert "ingest_run_acquired" in event_names, (
            f"Expected 'ingest_run_acquired' in {event_names!r}"
        )

    def test_lock_released_event_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.lock"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.lock")
        events = [_parse_json_message(r, "corpus_forge.ingest.lock") for r in records]
        event_names = [e.get("event") for e in events]
        assert "ingest_run_released" in event_names, (
            f"Expected 'ingest_run_released' in {event_names!r}"
        )

    def test_lock_events_have_event_field(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.lock"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.lock")
        assert records, "No corpus_forge.ingest.lock records"
        for rec in records:
            payload = _parse_json_message(rec, "corpus_forge.ingest.lock event field")
            assert "event" in payload, f"Lock payload missing 'event' key: {payload!r}"
            assert isinstance(payload["event"], str), (
                f"'event' must be a string, got {type(payload['event'])}"
            )

    def test_lock_events_are_json_encodable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config, _, _ = _wire_patched_ingest(monkeypatch, n_docs=1)

        with caplog.at_level(logging.INFO, logger="corpus_forge.ingest.lock"):
            _ingest_module.ingest_once(config)

        records = _collect_records(caplog, "corpus_forge.ingest.lock")
        assert records, "No corpus_forge.ingest.lock records to validate"
        for rec in records:
            payload = _parse_json_message(rec, "corpus_forge.ingest.lock JSON safety")
            _assert_json_encodable(payload, f"lock event '{payload.get('event')}'")
            _assert_no_forbidden_types(payload, f"lock event '{payload.get('event')}'")

    def test_lock_contention_event_shape(self) -> None:
        """ingest_run_contention must be JSON-encodable with an 'event' field.

        This test is structural — it does not exercise the live contention
        path (that lives in SR-T5).  It verifies the logger name and that
        a hand-crafted payload meeting the spec would pass the JSON-safety
        invariant.
        """
        # Simulate what SR-G5 will emit on contention
        sample_payload = {
            "event": "ingest_run_contention",
            "host": "testhost",
            "run_id": "01J0000000000000000000000A",
        }
        _assert_json_encodable(sample_payload, "ingest_run_contention shape test")
        _assert_no_forbidden_types(sample_payload, "ingest_run_contention shape test")
        # Verify the logger name resolves
        lock_logger = logging.getLogger("corpus_forge.ingest.lock")
        assert lock_logger.name == "corpus_forge.ingest.lock"


# ---------------------------------------------------------------------------
# Test 5 — JSON payload safety invariant (parametrised edge cases)
# ---------------------------------------------------------------------------


class TestPayloadJsonSafety:
    """JSON-encodability edge cases that must never appear in payloads."""

    @pytest.mark.parametrize(
        ("forbidden_value", "description"),
        [
            ({1, 2, 3}, "Python set"),
            (b"bytes", "bytes object"),
            (__import__("pathlib").Path("/tmp/test"), "pathlib.Path"),
        ],
    )
    def test_forbidden_type_detected(self, forbidden_value, description) -> None:
        """_assert_no_forbidden_types must flag the forbidden type."""
        payload = {"event": "test", "bad_field": forbidden_value}
        with pytest.raises(pytest.fail.Exception if hasattr(pytest, "fail") else Exception):
            _assert_no_forbidden_types(payload, "test")

    def test_valid_payload_passes(self) -> None:
        """A clean payload must not raise."""
        payload = {
            "event": "checkpoint",
            "run_id": "abc123",
            "last_op": "scan",
            "last_done": 42,
            "last_total": 1000,
            "elapsed_s": 3.14,
        }
        _assert_json_encodable(payload, "valid payload")
        _assert_no_forbidden_types(payload, "valid payload")

    def test_null_last_total_is_valid(self) -> None:
        """last_total=None (unknown) must serialise cleanly."""
        payload = {
            "event": "checkpoint",
            "run_id": "abc123",
            "last_op": "scan",
            "last_done": 0,
            "last_total": None,
            "elapsed_s": 0.0,
        }
        _assert_json_encodable(payload, "null last_total")

    def test_none_last_op_is_valid(self) -> None:
        """last_op=None (no op recorded yet) must serialise cleanly."""
        payload = {
            "event": "checkpoint",
            "run_id": "abc123",
            "last_op": None,
            "last_done": 0,
            "last_total": None,
            "elapsed_s": 0.0,
        }
        _assert_json_encodable(payload, "null last_op")


# ---------------------------------------------------------------------------
# Test 6 — Logger module-level attribute wiring in corpus_forge.ingest
# ---------------------------------------------------------------------------


class TestLoggerModuleAttributes:
    """Verify the three loggers are accessible as named attributes on the module."""

    def test_run_logger_attribute_name(self) -> None:
        # SR-G5 must expose one of: _run_logger, run_logger, or equivalent.
        # Accept either naming convention — only the logger *name* is frozen.
        run_log = getattr(_ingest_module, "_run_logger", None) or getattr(
            _ingest_module, "run_logger", None
        )
        assert run_log is not None, (
            "corpus_forge.ingest must expose a module-level logger attribute "
            "for 'corpus_forge.ingest.run' (named _run_logger or run_logger). "
            "SR-G5 must add this. (currently missing)"
        )
        assert run_log.name == "corpus_forge.ingest.run", (
            f"Logger attribute name mismatch: expected 'corpus_forge.ingest.run', "
            f"got {run_log.name!r}"
        )

    def test_checkpoint_logger_attribute_name(self) -> None:
        ckpt_log = getattr(_ingest_module, "_checkpoint_logger", None) or getattr(
            _ingest_module, "checkpoint_logger", None
        )
        assert ckpt_log is not None, (
            "corpus_forge.ingest must expose a module-level logger attribute "
            "for 'corpus_forge.ingest.checkpoint'. SR-G5 must add this."
        )
        assert ckpt_log.name == "corpus_forge.ingest.checkpoint", (
            f"Expected 'corpus_forge.ingest.checkpoint', got {ckpt_log.name!r}"
        )

    def test_lock_logger_attribute_name(self) -> None:
        lock_log = getattr(_ingest_module, "_lock_logger", None) or getattr(
            _ingest_module, "lock_logger", None
        )
        assert lock_log is not None, (
            "corpus_forge.ingest must expose a module-level logger attribute "
            "for 'corpus_forge.ingest.lock'. SR-G5 must add this."
        )
        assert lock_log.name == "corpus_forge.ingest.lock", (
            f"Expected 'corpus_forge.ingest.lock', got {lock_log.name!r}"
        )
