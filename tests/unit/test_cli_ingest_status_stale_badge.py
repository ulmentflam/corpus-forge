"""DR-T7 (RED) — ``--status`` STALE badge + JSON ``"stale": true`` inference.

RED condition
-------------
``corpus_forge.ingest._render_status`` does not accept a ``stale_threshold``
keyword argument and does not emit a STALE marker.  ``print_ingest_status``
has no ``stale_threshold`` kwarg.  Every test in this file MUST fail until
DR-G6 wires those additions.

Contracts tested (from tasks.md DR-T7 + design contract §C7 + §C8):

1.  Running + recent last_progress_at → no STALE badge in human output.
2.  Running + stale last_progress_at → output contains ``STALE — last progress N min ago``.
3.  Completed + ancient last_progress_at → no STALE badge.
4.  Failed + ancient last_progress_at → no STALE badge.
5.  Interrupted + ancient last_progress_at → no STALE badge.
6.  JSON variant: stale predicate fires → ``"stale": true`` on the ``run`` object.
7.  JSON variant: predicate doesn't fire → ``"stale"`` key is ABSENT (not false).
8.  Threshold sourced from config.scan.stale_run_threshold when stale_threshold=None.
9.  stale_threshold=0.0 → STALE never reported (disabled).
10. Mark-stale MUST NOT be called — read-only invariant (per SR-Q1 / §C7).
11. Explicit kwarg ``stale_threshold`` overrides config value.
12. Multi-machine display: stale inference works when run row's host ≠ socket.gethostname().
13. Boundary: ``now() - last_progress_at`` exactly equal to threshold → NOT stale (> not >=).
14. ``print_ingest_status`` new kwarg signature accepted without TypeError.
15. ``_render_status`` new kwarg ``stale_threshold`` accepted without TypeError.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_RUN_ID = "DR-T7-RUN-0000000000000001"
_HOST = "alpha-laptop"
_PID = 1234
_STARTED_ISO = "2026-05-28T10:00:00+00:00"


def _utcnow() -> datetime:
    """Return a fixed UTC 'now' for deterministic tests."""
    return datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _make_run(
    *,
    status: str = "running",
    run_id: str = _RUN_ID,
    host: str = _HOST,
    pid: int = _PID,
    started_at: str = _STARTED_ISO,
    ended_at: str | None = None,
    last_progress_at: datetime | str | None = None,
    last_op: str | None = "scan",
    last_done: int = 50,
    last_total: int | None = 200,
    error: str | None = None,
    config_digest: str = "abc123",
) -> dict[str, Any]:
    """Build a minimal ingest_runs-shaped dict for tests."""
    if last_progress_at is None:
        last_progress_at = _utcnow() - timedelta(seconds=60)
    return {
        "run_id": run_id,
        "status": status,
        "host": host,
        "pid": pid,
        "started_at": started_at,
        "ended_at": ended_at,
        "last_progress_at": last_progress_at,
        "last_op": last_op,
        "last_done": last_done,
        "last_total": last_total,
        "error": error,
        "config_digest": config_digest,
    }


def _make_mock_backend(
    *,
    latest_run: dict[str, Any] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Return a MagicMock backend that satisfies the read-only call pattern."""
    backend = MagicMock()
    backend.latest_ingest_run.return_value = latest_run
    backend.list_ingest_run_sources.return_value = source_rows or []
    backend.get_ingest_run_sources.return_value = source_rows or []
    # Poison mark_stale_runs: if called, it will raise so we detect the violation.
    backend.mark_stale_runs.side_effect = AssertionError(
        "mark_stale_runs MUST NOT be called from print_ingest_status — read-only invariant"
    )
    return backend


def _get_render_status():
    """Import _render_status; fail clearly if not yet present."""
    try:
        from corpus_forge.ingest import _render_status

        return _render_status
    except ImportError:
        pytest.fail(
            "_render_status not importable from corpus_forge.ingest. "
            "DR-G6 must add stale_threshold kwarg support."
        )


def _get_print_ingest_status():
    """Import print_ingest_status; fail clearly if signature is wrong."""
    try:
        from corpus_forge.ingest import print_ingest_status

        return print_ingest_status
    except ImportError:
        pytest.fail(
            "print_ingest_status not importable from corpus_forge.ingest. "
            "DR-G6 must add stale_threshold kwarg."
        )


# ---------------------------------------------------------------------------
# §C8 — print_ingest_status signature extension
# ---------------------------------------------------------------------------


class TestPrintIngestStatusSignature:
    """print_ingest_status must accept stale_threshold as a keyword-only arg."""

    def test_signature_has_stale_threshold_kwarg(self) -> None:
        """print_ingest_status must have a stale_threshold keyword parameter."""
        import inspect

        fn = _get_print_ingest_status()
        sig = inspect.signature(fn)
        assert "stale_threshold" in sig.parameters, (
            "print_ingest_status must gain a 'stale_threshold' keyword parameter "
            "(DR-G6 §C8). Currently missing — this is the RED."
        )

    def test_stale_threshold_default_is_none(self) -> None:
        """stale_threshold kwarg must default to None (so config wins by default)."""
        import inspect

        fn = _get_print_ingest_status()
        sig = inspect.signature(fn)
        param = sig.parameters.get("stale_threshold")
        assert param is not None, "stale_threshold param not found"
        assert param.default is None, f"stale_threshold default must be None, got {param.default!r}"

    def test_stale_threshold_kwarg_accepted_without_typeerror(self, tmp_path) -> None:
        """Passing stale_threshold=900.0 to print_ingest_status must not raise TypeError."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "sig_check.db"
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)
        backend = _make_mock_backend(latest_run=None)
        backend.mark_stale_runs.side_effect = None  # don't poison for this test

        with patch.object(ingest_module, "_build_backend_for_status", return_value=backend):
            # Must not raise TypeError for unknown kwarg — signature test only
            try:
                fn(config, stale_threshold=900.0)
            except TypeError as exc:
                pytest.fail(
                    f"print_ingest_status raised TypeError with stale_threshold=900.0: {exc}"
                )


# ---------------------------------------------------------------------------
# §C7 — _render_status stale_threshold kwarg
# ---------------------------------------------------------------------------


class TestRenderStatusSignature:
    """_render_status must accept a stale_threshold keyword-only argument."""

    def test_render_status_has_stale_threshold_kwarg(self) -> None:
        """_render_status must accept stale_threshold as a keyword argument."""
        import inspect

        fn = _get_render_status()
        sig = inspect.signature(fn)
        assert "stale_threshold" in sig.parameters, (
            "_render_status must gain a 'stale_threshold' keyword parameter "
            "(DR-G6 §C7). Currently missing — this is the RED."
        )

    def test_render_status_stale_threshold_kwarg_accepted(self) -> None:
        """Calling _render_status(run, sources, stale_threshold=900.0) must not raise TypeError."""
        fn = _get_render_status()
        run = _make_run(
            status="running",
            last_progress_at=_utcnow() - timedelta(seconds=60),
        )
        try:
            result = fn(run, [], stale_threshold=900.0)
        except TypeError as exc:
            pytest.fail(f"_render_status raised TypeError with stale_threshold=900.0: {exc}")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# §C7 / DR-T7 acceptance 1 & 2 — Human output STALE badge
# ---------------------------------------------------------------------------


class TestRenderStatusStaleBadgeHuman:
    """_render_status STALE badge in human-readable output."""

    @pytest.fixture
    def frozen_now(self):
        """Patch datetime.now(UTC) to return a fixed time for determinism."""
        fixed = _utcnow()
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = datetime
            yield fixed

    def _call_render(self, run: dict, *, stale_threshold: float) -> str:
        fn = _get_render_status()
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = _utcnow()
            # Allow datetime(...) construction to work normally
            mock_dt.side_effect = datetime
            return fn(run, [], stale_threshold=stale_threshold)

    def test_running_recent_no_stale_badge(self) -> None:
        """Running + last_progress_at = now - 60s + threshold 900 → no STALE badge."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=60),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" not in output, (
            f"Expected no STALE badge for recent running run (60s old, threshold 900s):\n{output!r}"
        )

    def test_running_stale_has_stale_badge(self) -> None:
        """Running + last_progress_at = now - 1200s + threshold 900 → STALE badge present."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" in output, (
            f"Expected STALE badge for stale running run (1200s old, threshold 900s):\n{output!r}"
        )

    def test_running_stale_badge_format_contains_last_progress(self) -> None:
        """Stale badge must contain 'STALE — last progress N min ago' (C7 format)."""
        fn = _get_render_status()
        now = _utcnow()
        # 1200 seconds = 20 minutes
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        # C7 specifies: format "status: RUNNING (STALE — last progress N min ago)"
        assert "STALE" in output, f"'STALE' token not found:\n{output!r}"
        assert "last progress" in output.lower(), (
            f"'last progress' phrase not in stale output:\n{output!r}"
        )
        assert "min" in output.lower() or "ago" in output.lower(), (
            f"Humanized time phrase ('min' or 'ago') not found in stale output:\n{output!r}"
        )
        assert "20" in output, f"Expected '20' min in stale output for 1200s lag:\n{output!r}"

    def test_running_stale_badge_status_still_shows_running(self) -> None:
        """The stale badge must not replace the RUNNING status label — both must appear."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "RUNNING" in output, (
            f"'RUNNING' status label must remain visible even when STALE badge added:\n{output!r}"
        )
        assert "STALE" in output, f"'STALE' marker not present:\n{output!r}"

    def test_completed_ancient_no_stale_badge(self) -> None:
        """Completed + last_progress_at = now - 9999s + threshold 900 → no STALE."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="completed",
            ended_at="2026-05-28T11:00:00+00:00",
            last_progress_at=now - timedelta(seconds=9999),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" not in output, f"Completed run must never show STALE badge:\n{output!r}"
        assert "COMPLETED" in output, f"'COMPLETED' not in output:\n{output!r}"

    def test_failed_ancient_no_stale_badge(self) -> None:
        """Failed + ancient last_progress_at → no STALE badge."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="failed",
            ended_at="2026-05-28T11:00:00+00:00",
            last_progress_at=now - timedelta(seconds=9999),
            error="disk full",
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" not in output, f"Failed run must never show STALE badge:\n{output!r}"
        assert "FAILED" in output, f"'FAILED' not in output:\n{output!r}"

    def test_interrupted_ancient_no_stale_badge(self) -> None:
        """Interrupted + ancient last_progress_at → no STALE badge.

        Per principal decision #6: interrupted is sticky. It should show
        INTERRUPTED and the resume hint, not a STALE badge.
        """
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="interrupted",
            last_progress_at=now - timedelta(seconds=9999),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" not in output, (
            f"Interrupted run must never show STALE badge (sticky terminal state):\n{output!r}"
        )
        # Must still show the interrupted state and resume hint
        assert "INTERRUPTED" in output, f"'INTERRUPTED' not in output:\n{output!r}"
        assert "--resume" in output, f"'--resume' hint not in interrupted output:\n{output!r}"


# ---------------------------------------------------------------------------
# §C7 — JSON output stale key
# ---------------------------------------------------------------------------


class TestPrintIngestStatusJsonStale:
    """JSON output gains ``"stale": true`` on run object when predicate fires.

    Per C7: when false or status!=running, the key is OMITTED (not false).
    """

    def _call_print_json(
        self,
        run: dict,
        *,
        stale_threshold: float,
        capsys,
        tmp_path,
    ) -> dict:
        """Call print_ingest_status(json_output=True) and parse the JSON output."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "stale_json.db"
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)
        backend = _make_mock_backend(latest_run=run)
        backend.mark_stale_runs.side_effect = None  # reset poison for this branch

        with (
            patch.object(ingest_module, "_build_backend_for_status", return_value=backend),
            patch("corpus_forge.ingest.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = _utcnow()
            mock_dt.side_effect = datetime
            fn(config, json_output=True, stale_threshold=stale_threshold)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        lines = [ln for ln in output.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON line in output:\n{output!r}"
        return json.loads(lines[0])

    def test_json_stale_true_when_running_stale(self, capsys, tmp_path) -> None:
        """JSON must contain ``"stale": true`` on the run object when predicate fires."""
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        payload = self._call_print_json(
            run, stale_threshold=900.0, capsys=capsys, tmp_path=tmp_path
        )
        run_obj = payload.get("run")
        assert run_obj is not None, f"'run' key missing from JSON:\n{payload!r}"
        assert run_obj.get("stale") is True, (
            f"Expected 'stale': true in JSON run object for stale running row;\n"
            f"got run object: {run_obj!r}"
        )

    def test_json_stale_key_absent_when_running_recent(self, capsys, tmp_path) -> None:
        """JSON must NOT contain a ``"stale"`` key when predicate does not fire."""
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=60),
        )
        payload = self._call_print_json(
            run, stale_threshold=900.0, capsys=capsys, tmp_path=tmp_path
        )
        run_obj = payload.get("run")
        assert run_obj is not None, f"'run' key missing from JSON:\n{payload!r}"
        assert "stale" not in run_obj, (
            f"Expected 'stale' key ABSENT from JSON run when not stale "
            f"(C7: omit, don't emit false);\ngot run: {run_obj!r}"
        )

    def test_json_stale_key_absent_for_completed(self, capsys, tmp_path) -> None:
        """Completed run → 'stale' key must be absent from JSON regardless of age."""
        now = _utcnow()
        run = _make_run(
            status="completed",
            ended_at="2026-05-28T11:00:00+00:00",
            last_progress_at=now - timedelta(seconds=9999),
        )
        payload = self._call_print_json(
            run, stale_threshold=900.0, capsys=capsys, tmp_path=tmp_path
        )
        run_obj = payload.get("run")
        assert run_obj is not None
        assert "stale" not in run_obj, (
            f"Completed run must not have 'stale' key in JSON;\ngot: {run_obj!r}"
        )

    def test_json_stale_key_absent_for_interrupted(self, capsys, tmp_path) -> None:
        """Interrupted + ancient last_progress_at → 'stale' key absent from JSON."""
        now = _utcnow()
        run = _make_run(
            status="interrupted",
            last_progress_at=now - timedelta(seconds=9999),
        )
        payload = self._call_print_json(
            run, stale_threshold=900.0, capsys=capsys, tmp_path=tmp_path
        )
        run_obj = payload.get("run")
        assert run_obj is not None
        assert "stale" not in run_obj, (
            f"Interrupted run must not have 'stale' key in JSON (sticky terminal state);\n"
            f"got: {run_obj!r}"
        )

    def test_json_stale_key_absent_for_failed(self, capsys, tmp_path) -> None:
        """Failed + ancient last_progress_at → 'stale' key absent from JSON."""
        now = _utcnow()
        run = _make_run(
            status="failed",
            ended_at="2026-05-28T11:00:00+00:00",
            last_progress_at=now - timedelta(seconds=9999),
            error="OOM",
        )
        payload = self._call_print_json(
            run, stale_threshold=900.0, capsys=capsys, tmp_path=tmp_path
        )
        run_obj = payload.get("run")
        assert run_obj is not None
        assert "stale" not in run_obj, (
            f"Failed run must not have 'stale' key in JSON;\ngot: {run_obj!r}"
        )

    def test_json_stale_key_absent_when_threshold_zero(self, capsys, tmp_path) -> None:
        """stale_threshold=0.0 disables the inference → 'stale' key absent even for ancient run."""
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=99999),
        )
        payload = self._call_print_json(run, stale_threshold=0.0, capsys=capsys, tmp_path=tmp_path)
        run_obj = payload.get("run")
        assert run_obj is not None
        assert "stale" not in run_obj, (
            f"stale_threshold=0 must disable inference; 'stale' key must be absent;\n"
            f"got: {run_obj!r}"
        )


# ---------------------------------------------------------------------------
# Threshold boundary — exactly-equal is NOT stale (> not >=)
# ---------------------------------------------------------------------------


class TestStaleBoundaryExactlyEqual:
    """``now() - last_progress_at == threshold_seconds`` → NOT stale."""

    def test_exactly_equal_to_threshold_is_not_stale_human(self) -> None:
        """Elapsed == threshold → no STALE badge (strictly-greater-than rule)."""
        fn = _get_render_status()
        now = _utcnow()
        threshold = 900.0
        # Exactly at threshold: elapsed == threshold
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=threshold),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=threshold)

        assert "STALE" not in output, (
            f"Elapsed exactly equal to threshold must NOT trigger STALE badge "
            f"(predicate is 'elapsed > threshold', not >=):\n{output!r}"
        )

    def test_one_second_over_threshold_is_stale_human(self) -> None:
        """Elapsed == threshold + 1s → IS stale (just over the boundary)."""
        fn = _get_render_status()
        now = _utcnow()
        threshold = 900.0
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=threshold + 1),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=threshold)

        assert "STALE" in output, (
            f"Elapsed exactly 1s over threshold MUST trigger STALE badge:\n{output!r}"
        )


# ---------------------------------------------------------------------------
# Threshold=0.0 disables inference
# ---------------------------------------------------------------------------


class TestStaleThresholdZeroDisables:
    """stale_threshold=0.0 → STALE inference disabled entirely."""

    def test_threshold_zero_running_ancient_no_stale_badge(self) -> None:
        """Even a million-second-old running row is NOT stale when threshold=0."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=999_999),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=0.0)

        assert "STALE" not in output, (
            f"stale_threshold=0.0 must disable the STALE inference entirely:\n{output!r}"
        )


# ---------------------------------------------------------------------------
# Threshold sourced from config.scan.stale_run_threshold
# ---------------------------------------------------------------------------


class TestThresholdSourcedFromConfig:
    """When stale_threshold=None, print_ingest_status reads from config.scan.stale_run_threshold."""

    def test_config_threshold_overrides_default(self, capsys, tmp_path) -> None:
        """Config with stale_run_threshold=60.0: running row 90s old → STALE."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "cfg_threshold.db"
        # Note: stale_run_threshold=60.0 is NOT yet a field in ScanConfig.
        # This test will fail until both DR-G2 (field) and DR-G6 (wiring) are done.
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [scan]
            stale_run_threshold = 60.0
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)

        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=90),
        )
        backend = _make_mock_backend(latest_run=run)
        backend.mark_stale_runs.side_effect = None

        with (
            patch.object(ingest_module, "_build_backend_for_status", return_value=backend),
            patch("corpus_forge.ingest.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            # stale_threshold=None → reads from config.scan.stale_run_threshold (=60.0)
            fn(config, stale_threshold=None)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "STALE" in output, (
            f"Expected STALE badge when config.scan.stale_run_threshold=60.0 "
            f"and run is 90s old (stale_threshold=None lets config win):\n{output!r}"
        )

    def test_explicit_stale_threshold_kwarg_overrides_config(self, capsys, tmp_path) -> None:
        """Explicit stale_threshold kwarg overrides the config value."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "explicit_threshold.db"
        # Config says 60.0 but caller explicitly passes 5000.0 → run at 90s is NOT stale
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [scan]
            stale_run_threshold = 60.0
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)

        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=90),
        )
        backend = _make_mock_backend(latest_run=run)
        backend.mark_stale_runs.side_effect = None

        with (
            patch.object(ingest_module, "_build_backend_for_status", return_value=backend),
            patch("corpus_forge.ingest.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            # Override: explicit 5000.0 wins over config 60.0 → run at 90s is NOT stale
            fn(config, stale_threshold=5000.0)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "STALE" not in output, (
            f"stale_threshold=5000.0 kwarg must override config=60.0; "
            f"run at 90s must NOT be STALE:\n{output!r}"
        )


# ---------------------------------------------------------------------------
# Read-only invariant — mark_stale_runs MUST NOT be called
# ---------------------------------------------------------------------------


class TestReadOnlyInvariant:
    """print_ingest_status MUST NOT call backend.mark_stale_runs (§C7 / SR-Q1)."""

    def test_mark_stale_runs_not_called_for_stale_run(self, capsys, tmp_path) -> None:
        """Even when STALE badge would fire, mark_stale_runs must NOT be called."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "readonly.db"
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        # Use a fresh backend with the poisoned mark_stale_runs (from _make_mock_backend)
        backend = _make_mock_backend(latest_run=run)

        with (
            patch.object(ingest_module, "_build_backend_for_status", return_value=backend),
            patch("corpus_forge.ingest.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            # Must NOT call mark_stale_runs (which would raise via the poison side_effect)
            fn(config, stale_threshold=900.0)

        # If we get here without the AssertionError from poison side_effect, we're good.
        backend.mark_stale_runs.assert_not_called()

    def test_mark_stale_runs_not_called_via_mock_assertion(self, tmp_path) -> None:
        """Explicit mock assertion: mark_stale_runs.called must be False after --status."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "readonly_assert.db"
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        # Clean backend — mark_stale_runs is a MagicMock, not poisoned
        backend = MagicMock()
        backend.latest_ingest_run.return_value = run
        backend.list_ingest_run_sources.return_value = []
        backend.get_ingest_run_sources.return_value = []

        with (
            patch.object(ingest_module, "_build_backend_for_status", return_value=backend),
            patch("corpus_forge.ingest.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            fn(config, stale_threshold=900.0)

        assert backend.mark_stale_runs.called is False, (
            "print_ingest_status must NOT call mark_stale_runs — read-only invariant "
            "(§C7 / SR-Q1 contract). Inference only."
        )


# ---------------------------------------------------------------------------
# Multi-machine display sanity — cross-host stale inference
# ---------------------------------------------------------------------------


class TestMultiMachineStaleBadge:
    """STALE inference must work even when the run's host != current socket.gethostname()."""

    def test_stale_badge_works_for_foreign_host(self) -> None:
        """Run from 'beta-host' is stale; my hostname is 'alpha-host'. Must still show STALE."""
        fn = _get_render_status()
        now = _utcnow()
        current_machine = socket.gethostname()
        foreign_host = f"not-{current_machine}"

        run = _make_run(
            status="running",
            host=foreign_host,
            last_progress_at=now - timedelta(seconds=1200),
        )
        with (
            patch("corpus_forge.ingest.datetime") as mock_dt,
            patch("socket.gethostname", return_value=current_machine),
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" in output, (
            f"STALE inference must work for foreign-host runs. "
            f"host in row={foreign_host!r}, local hostname={current_machine!r}:\n{output!r}"
        )

    def test_no_stale_badge_for_foreign_host_recent_run(self) -> None:
        """Run from a foreign host that is recent must NOT show STALE."""
        fn = _get_render_status()
        now = _utcnow()
        current_machine = socket.gethostname()
        foreign_host = f"not-{current_machine}"

        run = _make_run(
            status="running",
            host=foreign_host,
            last_progress_at=now - timedelta(seconds=60),
        )
        with (
            patch("corpus_forge.ingest.datetime") as mock_dt,
            patch("socket.gethostname", return_value=current_machine),
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        assert "STALE" not in output, (
            f"Recent run from foreign host must not show STALE badge:\n{output!r}"
        )


# ---------------------------------------------------------------------------
# last_progress_at = None edge case
# ---------------------------------------------------------------------------


class TestStaleWhenLastProgressAtNone:
    """When last_progress_at is None, the stale inference must not crash."""

    def test_null_last_progress_at_does_not_raise(self) -> None:
        """_render_status must handle a None last_progress_at gracefully."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=None,
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            # Must not raise
            try:
                output = fn(run, [], stale_threshold=900.0)
            except Exception as exc:
                pytest.fail(f"_render_status raised when last_progress_at=None: {exc!r}")
            assert isinstance(output, str)


# ---------------------------------------------------------------------------
# Regression lock — badge token is "STALE" (uppercase, no brackets)
# ---------------------------------------------------------------------------


class TestStaleBadgeTokenFormat:
    """Locks the exact token used in the output so tooling can grep for it."""

    def test_badge_token_is_uppercase_stale(self) -> None:
        """The badge token must be the uppercase string 'STALE' (no brackets, no lowercase)."""
        fn = _get_render_status()
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        with patch("corpus_forge.ingest.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            output = fn(run, [], stale_threshold=900.0)

        # C7: "status: RUNNING (STALE — last progress N min ago)"
        # The token must be uppercase STALE, not [STALE] or stale or Stale
        assert "STALE" in output, f"'STALE' (uppercase) not found in output:\n{output!r}"
        assert "[STALE]" not in output, (
            f"Badge must NOT use bracket notation '[STALE]'; got:\n{output!r}"
        )
        # Verify it's on the status line (contains RUNNING too)
        status_lines = [line for line in output.splitlines() if "status" in line.lower()]
        assert any("STALE" in line for line in status_lines), (
            "STALE token must appear on the status line. Status lines found:\n"
            + "\n".join(status_lines)
            + f"\nFull output:\n{output!r}"
        )

    def test_json_key_is_stale_lowercase(self, capsys, tmp_path) -> None:
        """The JSON key must be the lowercase string 'stale', not 'STALE' or 'is_stale'."""
        import textwrap

        fn = _get_print_ingest_status()
        db_path = tmp_path / "key_format.db"
        cfg_text = textwrap.dedent(
            f"""
            [backend]
            kind = "sqlite"
            dsn  = "{db_path.as_posix()}"
            [daemon]
            [[datasets]]
            name = "default"
            kind = "text"
            sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]
            """
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text)
        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)
        now = _utcnow()
        run = _make_run(
            status="running",
            last_progress_at=now - timedelta(seconds=1200),
        )
        backend = MagicMock()
        backend.latest_ingest_run.return_value = run
        backend.list_ingest_run_sources.return_value = []
        backend.get_ingest_run_sources.return_value = []

        with (
            patch.object(ingest_module, "_build_backend_for_status", return_value=backend),
            patch("corpus_forge.ingest.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = datetime
            fn(config, json_output=True, stale_threshold=900.0)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        lines = [ln for ln in output.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON in output:\n{output!r}"
        payload = json.loads(lines[0])
        run_obj = payload.get("run") or {}

        # Must use lowercase "stale", not "STALE" or "is_stale"
        assert "stale" in run_obj, (
            f"JSON key must be lowercase 'stale'; run object keys: {list(run_obj.keys())!r}"
        )
        assert "STALE" not in run_obj, (
            f"JSON key must NOT be uppercase 'STALE'; got: {list(run_obj.keys())!r}"
        )
        assert "is_stale" not in run_obj, (
            f"JSON key must NOT be 'is_stale'; got: {list(run_obj.keys())!r}"
        )
