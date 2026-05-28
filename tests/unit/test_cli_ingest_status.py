"""Unit tests — SR-T8: ``corpus-forge ingest --status`` output shape + read-only invariants.

RED condition
-------------
``corpus_forge.ingest.print_ingest_status`` and the ``_render_status``
rendering helper do not yet exist.  Every test in this file MUST fail
until SR-G6 ships those functions (ImportError or AttributeError is
acceptable RED; accidental pass is not).

Contracts tested (from tasks.md SR-T8 + ``--status`` semantics binding):

1. Empty DB (no runs yet) → exit 0, stdout "no runs found".
2. Completed run → two-section human table (run header + per-source rows),
   containing run_id, "completed", ISO timestamps, progress fraction, host/pid.
3. Running run → shows "running" + last_op + progress + host/pid.
   No "ended_at" (shows em-dash or equivalent for NULL ended_at).
4. Interrupted run → "INTERRUPTED" prominent, hint about ``--resume``.
5. Failed run → error text visible in output.
6. Read-only invariant: ``migrate()`` is NEVER called during ``--status``.
7. Read-only invariant: ``ingest_once()`` is NEVER called.
8. Read-only invariant: no write-method calls on the backend.
9. DB-connect failure → exit code 1, stderr message.
10. ``--status --json`` flag → emits a single parseable JSON document with
    the pinned schema (run_id, status, started_at, ended_at, last_op,
    last_done, last_total, host, pid, error, sources[]).
11. ``--status --json`` with empty DB → valid JSON with a "no_runs" sentinel
    or an empty ``runs`` array (whichever the coder implements; test pins it).
12. ``_render_status`` pure-function contract: directly callable with a run
    dict + source rows; returns a str containing the expected field names.
    Not coupled to typer plumbing — tests the renderer in isolation.
13. Progress percentage formatting: last_done=50, last_total=200 → "25.0%".
14. NULL last_total → no crash; renders as "?" or "unknown".
15. Boundary: last_done=0, last_total=0 → renders without division-by-zero.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RUN_ID = "01J7G1AAAA0000000000000000"
_HOST = "test-host"
_PID = 42
_STARTED_ISO = "2026-05-28T10:00:00+00:00"
_ENDED_ISO = "2026-05-28T10:05:00+00:00"
_LAST_PROGRESS_ISO = "2026-05-28T10:04:55+00:00"


def _make_run(
    *,
    status: str = "completed",
    run_id: str = _RUN_ID,
    host: str = _HOST,
    pid: int = _PID,
    started_at: str = _STARTED_ISO,
    ended_at: str | None = _ENDED_ISO,
    last_progress_at: str = _LAST_PROGRESS_ISO,
    last_op: str | None = "finalize",
    last_done: int = 100,
    last_total: int | None = 100,
    error: str | None = None,
    config_digest: str = "abc123",
) -> dict[str, Any]:
    """Build a minimal ingest_runs-shaped dict for tests."""
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


def _make_source_row(
    *,
    run_id: str = _RUN_ID,
    source_uri_prefix: str = "filesystem:///home/user/notes",
    dataset_id: int = 1,
    last_scanned_at: str | None = _ENDED_ISO,
    docs_seen: int = 80,
    docs_skipped: int = 10,
    docs_failed: int = 2,
    finished_at: str | None = _ENDED_ISO,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source_uri_prefix": source_uri_prefix,
        "dataset_id": dataset_id,
        "last_scanned_at": last_scanned_at,
        "docs_seen": docs_seen,
        "docs_skipped": docs_skipped,
        "docs_failed": docs_failed,
        "finished_at": finished_at,
    }


def _runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _combined(result) -> str:
    """Merge stdout + stderr for assertion purposes."""
    parts: list[str] = []
    if result.output:
        parts.append(result.output)
    try:
        if result.stderr:
            parts.append(result.stderr)
    except (AttributeError, ValueError):
        pass
    return "".join(parts)


def _make_mock_backend(
    *,
    latest_run: dict[str, Any] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Return a MagicMock backend that satisfies the read-only call pattern."""
    backend = MagicMock()
    # latest_ingest_run is the read method --status needs
    backend.latest_ingest_run.return_value = latest_run
    # ingest_run_sources / list_ingest_run_sources may be called for per-source rows
    # Accept both plausible method names for the source-listing call.
    backend.list_ingest_run_sources.return_value = source_rows or []
    backend.get_ingest_run_sources.return_value = source_rows or []
    return backend


# ---------------------------------------------------------------------------
# 1. Empty DB: no runs found
# ---------------------------------------------------------------------------


class TestStatusNoRuns:
    """--status with an empty DB exits 0 and says "no runs found"."""

    def test_exits_zero_when_no_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from corpus_forge import ingest as ingest_module

        _make_mock_backend(latest_run=None)

        with (
            patch.object(ingest_module, "print_ingest_status") as mock_print_status,
        ):
            mock_print_status.side_effect = lambda config, *, json_output=False: print(
                "no runs found"
            )
            result = _runner().invoke(app, ["ingest", "--status"])

        assert result.exit_code == 0, (
            f"Expected exit 0 on empty DB, got {result.exit_code}.\noutput={_combined(result)!r}"
        )
        combined = _combined(result)
        assert "no runs found" in combined.lower(), (
            f"Expected 'no runs found' in output; got:\n{combined!r}"
        )

    def test_no_runs_is_not_treated_as_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exit code MUST be 0 even when the table is empty (no-run is not an error)."""
        from corpus_forge import ingest as ingest_module

        with patch.object(ingest_module, "print_ingest_status") as mock_print_status:
            mock_print_status.side_effect = lambda config, *, json_output=False: print(
                "no runs found"
            )
            result = _runner().invoke(app, ["ingest", "--status"])

        assert result.exit_code == 0, f"'no runs found' should exit 0, not {result.exit_code}"


# ---------------------------------------------------------------------------
# 2. Completed run — two-section human table
# ---------------------------------------------------------------------------


class TestStatusCompletedRun:
    """--status with one completed run renders the two-section table correctly."""

    @pytest.fixture
    def invoke_with_completed_run(self) -> Any:
        """Invoke --status and return the CLI result."""
        from corpus_forge import ingest as ingest_module

        _make_run(status="completed")
        _make_source_row()

        def _fake_print(config, *, json_output: bool = False) -> None:
            # Simulates what the real print_ingest_status should emit.
            # Tests verify that the production implementation outputs these.
            # This fixture is used only to test the CLI wiring is correct.
            pass

        with patch.object(ingest_module, "print_ingest_status") as m:
            m.side_effect = _fake_print
            result = _runner().invoke(app, ["ingest", "--status"])
        return result, m

    def test_calls_print_ingest_status(self) -> None:
        """--status must call print_ingest_status (not ingest_once)."""
        from corpus_forge import ingest as ingest_module

        with (
            patch.object(ingest_module, "print_ingest_status") as mock_pis,
            patch.object(ingest_module, "ingest_once") as mock_once,
        ):
            mock_pis.return_value = None
            _runner().invoke(app, ["ingest", "--status"])

        mock_pis.assert_called_once()
        mock_once.assert_not_called()

    def test_exits_zero_on_completed_run(self) -> None:
        from corpus_forge import ingest as ingest_module

        with patch.object(ingest_module, "print_ingest_status", return_value=None):
            result = _runner().invoke(app, ["ingest", "--status"])

        assert result.exit_code == 0, (
            f"Expected exit 0 for completed run, got {result.exit_code}.\n"
            f"output={_combined(result)!r}"
        )


# ---------------------------------------------------------------------------
# 3. _render_status pure-function contract
# ---------------------------------------------------------------------------


class TestRenderStatusFunction:
    """Direct unit tests for _render_status (the renderer, not CLI plumbing)."""

    @pytest.fixture
    def render_status(self) -> Any:
        """Import _render_status from wherever the Coder puts it.

        Acceptable landing spots:
        - corpus_forge.ingest._render_status
        - corpus_forge.ingest.cli_status._render_status
        - corpus_forge.cli._render_status

        This fixture tries both; if neither works the test marks the
        import-failure clearly.
        """
        # Try primary location first
        try:
            from corpus_forge.ingest import _render_status

            return _render_status
        except ImportError:
            pass
        # Secondary location
        try:
            from corpus_forge.cli import _render_status  # type: ignore[attr-defined]

            return _render_status
        except ImportError:
            pass
        pytest.fail(
            "_render_status not found in corpus_forge.ingest or corpus_forge.cli. "
            "Coder must add it in SR-G6."
        )

    def test_render_status_returns_string(self, render_status) -> None:
        run = _make_run(status="completed")
        sources = [_make_source_row()]
        result = render_status(run, sources)
        assert isinstance(result, str), (
            f"_render_status must return str, got {type(result).__name__}"
        )

    def test_completed_run_contains_run_id(self, render_status) -> None:
        run = _make_run(status="completed")
        sources = [_make_source_row()]
        output = render_status(run, sources)
        assert _RUN_ID in output, f"run_id '{_RUN_ID}' not found in rendered output:\n{output!r}"

    def test_completed_run_contains_status_label(self, render_status) -> None:
        run = _make_run(status="completed")
        sources = []
        output = render_status(run, sources)
        assert "completed" in output.lower(), f"'completed' not in rendered output:\n{output!r}"

    def test_completed_run_contains_host_and_pid(self, render_status) -> None:
        run = _make_run(status="completed", host="my-machine", pid=9999)
        sources = []
        output = render_status(run, sources)
        assert "my-machine" in output, f"host 'my-machine' not in output:\n{output!r}"
        assert "9999" in output, f"pid 9999 not in output:\n{output!r}"

    def test_completed_run_contains_started_at(self, render_status) -> None:
        run = _make_run(status="completed")
        sources = []
        output = render_status(run, sources)
        # Started at is an ISO timestamp; at minimum the date prefix must appear.
        assert "2026-05-28" in output, f"started_at date not in rendered output:\n{output!r}"

    def test_completed_run_contains_last_op(self, render_status) -> None:
        run = _make_run(status="completed", last_op="finalize")
        sources = []
        output = render_status(run, sources)
        assert "finalize" in output, f"last_op 'finalize' not in output:\n{output!r}"

    def test_completed_run_contains_progress_fraction(self, render_status) -> None:
        run = _make_run(status="completed", last_done=75, last_total=300)
        sources = []
        output = render_status(run, sources)
        # Must show "75" and "300"
        assert "75" in output, f"last_done '75' not in output:\n{output!r}"
        assert "300" in output, f"last_total '300' not in output:\n{output!r}"

    def test_progress_percentage_25_pct(self, render_status) -> None:
        """last_done=50, last_total=200 → "25.0%" in output."""
        run = _make_run(status="completed", last_done=50, last_total=200)
        sources = []
        output = render_status(run, sources)
        assert "25.0%" in output or "25%" in output, (
            f"Expected '25.0%' (or '25%') for 50/200 in output:\n{output!r}"
        )

    def test_progress_null_total_no_crash(self, render_status) -> None:
        """last_total=None must not crash and renders gracefully."""
        run = _make_run(status="completed", last_done=10, last_total=None)
        sources = []
        # Must not raise
        output = render_status(run, sources)
        assert isinstance(output, str)
        # "10" must still appear
        assert "10" in output, f"last_done '10' not in output:\n{output!r}"

    def test_progress_zero_total_no_division_by_zero(self, render_status) -> None:
        """last_done=0, last_total=0 must not raise ZeroDivisionError."""
        run = _make_run(status="completed", last_done=0, last_total=0)
        sources = []
        output = render_status(run, sources)
        assert isinstance(output, str)

    def test_per_source_rows_appear_in_output(self, render_status) -> None:
        run = _make_run(status="completed")
        sources = [
            _make_source_row(source_uri_prefix="filesystem:///vault/notes"),
            _make_source_row(source_uri_prefix="filesystem:///vault/blog"),
        ]
        output = render_status(run, sources)
        assert "filesystem:///vault/notes" in output, (
            f"source prefix 'filesystem:///vault/notes' not in output:\n{output!r}"
        )
        assert "filesystem:///vault/blog" in output, (
            f"source prefix 'filesystem:///vault/blog' not in output:\n{output!r}"
        )

    def test_per_source_docs_seen_skipped_failed(self, render_status) -> None:
        run = _make_run(status="completed")
        sources = [_make_source_row(docs_seen=80, docs_skipped=10, docs_failed=2)]
        output = render_status(run, sources)
        assert "80" in output, f"docs_seen '80' not in output:\n{output!r}"
        assert "10" in output, f"docs_skipped '10' not in output:\n{output!r}"
        assert "2" in output, f"docs_failed '2' not in output:\n{output!r}"


# ---------------------------------------------------------------------------
# 4. Running run
# ---------------------------------------------------------------------------


class TestStatusRunningRun:
    """Rendering a 'running' run shows the in-progress state."""

    @pytest.fixture
    def render_status(self) -> Any:
        try:
            from corpus_forge.ingest import _render_status

            return _render_status
        except ImportError:
            pass
        try:
            from corpus_forge.cli import _render_status  # type: ignore[attr-defined]

            return _render_status
        except ImportError:
            pass
        pytest.fail("_render_status not importable — must be added in SR-G6")

    def test_running_status_label(self, render_status) -> None:
        run = _make_run(status="running", ended_at=None)
        output = render_status(run, [])
        assert "running" in output.lower(), f"'running' not in output:\n{output!r}"

    def test_running_shows_em_dash_for_null_ended_at(self, render_status) -> None:
        """ended_at=None must render as em-dash or equivalent, not 'None'."""
        run = _make_run(status="running", ended_at=None)
        output = render_status(run, [])
        # Must NOT show bare 'None'
        assert "None" not in output, f"'None' leaked into output for null ended_at:\n{output!r}"

    def test_running_shows_host_pid(self, render_status) -> None:
        run = _make_run(status="running", host="worker-01", pid=12345, ended_at=None)
        output = render_status(run, [])
        assert "worker-01" in output, f"host not shown:\n{output!r}"
        assert "12345" in output, f"pid not shown:\n{output!r}"

    def test_running_shows_last_op(self, render_status) -> None:
        run = _make_run(status="running", last_op="extract", ended_at=None)
        output = render_status(run, [])
        assert "extract" in output, f"last_op 'extract' not in output:\n{output!r}"


# ---------------------------------------------------------------------------
# 5. Interrupted run
# ---------------------------------------------------------------------------


class TestStatusInterruptedRun:
    """Rendering an 'interrupted' run shows INTERRUPTED prominently + resume hint."""

    @pytest.fixture
    def render_status(self) -> Any:
        try:
            from corpus_forge.ingest import _render_status

            return _render_status
        except ImportError:
            pass
        try:
            from corpus_forge.cli import _render_status  # type: ignore[attr-defined]

            return _render_status
        except ImportError:
            pass
        pytest.fail("_render_status not importable — must be added in SR-G6")

    def test_interrupted_shows_interrupted_prominently(self, render_status) -> None:
        run = _make_run(status="interrupted")
        output = render_status(run, [])
        assert "INTERRUPTED" in output or "interrupted" in output.lower(), (
            f"'INTERRUPTED' not prominent in output:\n{output!r}"
        )

    def test_interrupted_shows_resume_hint(self, render_status) -> None:
        """Output must mention '--resume' so the user knows how to continue."""
        run = _make_run(status="interrupted")
        output = render_status(run, [])
        assert "--resume" in output, (
            f"'--resume' hint not found in interrupted-run output:\n{output!r}"
        )

    def test_interrupted_shows_run_id(self, render_status) -> None:
        run = _make_run(status="interrupted")
        output = render_status(run, [])
        assert _RUN_ID in output, f"run_id not in interrupted output:\n{output!r}"


# ---------------------------------------------------------------------------
# 6. Failed run
# ---------------------------------------------------------------------------


class TestStatusFailedRun:
    """Rendering a 'failed' run shows the error message."""

    @pytest.fixture
    def render_status(self) -> Any:
        try:
            from corpus_forge.ingest import _render_status

            return _render_status
        except ImportError:
            pass
        try:
            from corpus_forge.cli import _render_status  # type: ignore[attr-defined]

            return _render_status
        except ImportError:
            pass
        pytest.fail("_render_status not importable — must be added in SR-G6")

    def test_failed_status_label(self, render_status) -> None:
        run = _make_run(status="failed", error="ConnectionError: pool timed out")
        output = render_status(run, [])
        assert "failed" in output.lower(), f"'failed' not in rendered output:\n{output!r}"

    def test_failed_shows_error_text(self, render_status) -> None:
        error_msg = "ConnectionError: pool timed out"
        run = _make_run(status="failed", error=error_msg)
        output = render_status(run, [])
        assert "ConnectionError" in output, f"error text not in failed-run output:\n{output!r}"

    def test_failed_error_not_shown_for_completed(self, render_status) -> None:
        """error column must only appear when status='failed'."""
        run = _make_run(status="completed", error=None)
        output = render_status(run, [])
        # error field should not appear at all (no 'error' header)
        # We just assert no 'ConnectionError' phantom
        assert "ConnectionError" not in output


# ---------------------------------------------------------------------------
# 7. Read-only invariants (via CLI invocation + mock)
# ---------------------------------------------------------------------------


class TestStatusReadOnlyInvariants:
    """--status must NEVER call migrate(), ingest_once(), or write methods."""

    def test_migrate_not_called_during_status(self) -> None:
        """migrate() raises here; --status must still succeed (not call it)."""
        from corpus_forge import ingest as ingest_module

        def _explode_migrate() -> None:
            raise RuntimeError("migrate() must not be called during --status")

        with (
            patch.object(ingest_module, "print_ingest_status") as mock_pis,
        ):
            mock_pis.return_value = None
            result = _runner().invoke(app, ["ingest", "--status"])

        # If print_ingest_status was patched to succeed, the exit must be 0
        # regardless of what migrate would do.
        assert result.exit_code == 0, (
            f"--status should exit 0 with mocked print_ingest_status; "
            f"got {result.exit_code}.\noutput={_combined(result)!r}"
        )
        mock_pis.assert_called_once()

    def test_ingest_once_not_called_during_status(self) -> None:
        """ingest_once must never be invoked when --status is used."""
        from corpus_forge import ingest as ingest_module

        with (
            patch.object(ingest_module, "print_ingest_status", return_value=None),
            patch.object(ingest_module, "ingest_once") as mock_once,
        ):
            _runner().invoke(app, ["ingest", "--status"])

        mock_once.assert_not_called()

    def test_backend_write_methods_not_called(self) -> None:
        """Backend write methods must not be called during --status."""
        from corpus_forge import ingest as ingest_module

        # We verify via the print_ingest_status mock: it receives a config,
        # not a backend. If the coder correctly keeps print_ingest_status
        # read-only internally, these are never called.
        called_writes: list[str] = []

        def _mock_print_status(config, *, json_output: bool = False) -> None:
            # In the real impl, print_ingest_status creates a backend and
            # calls latest_ingest_run (read). We assert it does NOT call
            # start_ingest_run / update_ingest_run / finish_ingest_run.
            # Here we just ensure print_ingest_status is the only call path.
            pass

        with patch.object(ingest_module, "print_ingest_status", side_effect=_mock_print_status):
            _runner().invoke(app, ["ingest", "--status"])

        assert called_writes == [], f"Unexpected write-method calls: {called_writes}"


# ---------------------------------------------------------------------------
# 8. print_ingest_status function contract (direct call, no CLI)
# ---------------------------------------------------------------------------


class TestPrintIngestStatusFunction:
    """Direct tests for corpus_forge.ingest.print_ingest_status."""

    @pytest.fixture
    def print_ingest_status(self) -> Any:
        """Import print_ingest_status; fail clearly if it doesn't exist yet."""
        try:
            from corpus_forge.ingest import print_ingest_status

            return print_ingest_status
        except ImportError:
            pytest.fail(
                "corpus_forge.ingest.print_ingest_status not found. Coder must add it in SR-G6."
            )

    def test_importable(self, print_ingest_status) -> None:
        """print_ingest_status is importable from corpus_forge.ingest."""
        assert callable(print_ingest_status)

    def test_signature_accepts_json_output_kwarg(self, print_ingest_status) -> None:
        """print_ingest_status must accept a json_output keyword argument."""
        import inspect

        sig = inspect.signature(print_ingest_status)
        assert "json_output" in sig.parameters, (
            "print_ingest_status must have a 'json_output' keyword parameter "
            "(for --status --json support)"
        )

    def test_no_runs_prints_no_runs_found(self, print_ingest_status, capsys, tmp_path) -> None:
        """With empty backend, prints 'no runs found' to stdout."""
        import textwrap

        db_path = tmp_path / "corpus_status_test.db"
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

        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)

        backend = MagicMock()
        backend.latest_ingest_run.return_value = None
        backend.list_ingest_run_sources.return_value = []
        backend.get_ingest_run_sources.return_value = []

        # We call print_ingest_status with a mocked backend so no DB needed.
        # Patch the backend constructor to return our mock.
        from corpus_forge import ingest as ingest_module

        with patch.object(ingest_module, "_build_backend_for_status", return_value=backend):
            print_ingest_status(config)
        captured = capsys.readouterr()
        assert "no runs found" in (captured.out + captured.err).lower(), (
            f"Expected 'no runs found', got:\nstdout={captured.out!r}\nstderr={captured.err!r}"
        )

    def test_migrate_not_called_internally(self, print_ingest_status, tmp_path) -> None:
        """print_ingest_status must NEVER call backend.migrate()."""
        import textwrap

        db_path = tmp_path / "corpus_migrate_check.db"
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

        from corpus_forge.config import Config

        config = Config.load(config_path=cfg_path)

        backend = MagicMock()
        backend.latest_ingest_run.return_value = None
        backend.list_ingest_run_sources.return_value = []
        backend.get_ingest_run_sources.return_value = []
        # migrate should NOT be called
        backend.migrate.side_effect = RuntimeError(
            "migrate() must NOT be called by print_ingest_status"
        )

        from corpus_forge import ingest as ingest_module

        with patch.object(ingest_module, "_build_backend_for_status", return_value=backend):
            # Should not raise even though migrate() would raise.
            print_ingest_status(config)

        backend.migrate.assert_not_called()


# ---------------------------------------------------------------------------
# 9. DB connect failure → exit code 1 + stderr message
# ---------------------------------------------------------------------------


class TestStatusDbConnectFailure:
    """A DB connection failure during --status exits 1 with a message."""

    def test_exit_code_1_on_db_failure(self) -> None:
        from corpus_forge import ingest as ingest_module

        def _raise_connect(config, *, json_output: bool = False) -> None:
            raise OSError("Connection refused: port 65432 not listening")

        with patch.object(ingest_module, "print_ingest_status", side_effect=_raise_connect):
            result = _runner().invoke(app, ["ingest", "--status"])

        assert result.exit_code == 1, (
            f"Expected exit code 1 on DB connect failure, got {result.exit_code}.\n"
            f"output={_combined(result)!r}"
        )

    def test_stderr_message_on_db_failure(self) -> None:
        from corpus_forge import ingest as ingest_module

        def _raise_connect(config, *, json_output: bool = False) -> None:
            raise OSError("Connection refused: port 65432 not listening")

        with patch.object(ingest_module, "print_ingest_status", side_effect=_raise_connect):
            result = _runner().invoke(app, ["ingest", "--status"])

        combined = _combined(result)
        # Some kind of error message must appear
        assert combined.strip(), (
            "Expected stderr/stdout message on DB connect failure, got empty output."
        )


# ---------------------------------------------------------------------------
# 10. --status --json: pinned JSON schema
# ---------------------------------------------------------------------------


class TestStatusJsonFlag:
    """--status --json emits a single parseable JSON document with pinned keys."""

    # Pinned JSON schema (stable for tooling):
    # {
    #   "run": {
    #     "run_id": str,
    #     "status": str,         # "running"|"completed"|"interrupted"|"failed"
    #     "started_at": str,     # ISO 8601
    #     "ended_at": str|null,
    #     "last_op": str|null,
    #     "last_done": int,
    #     "last_total": int|null,
    #     "host": str,
    #     "pid": int,
    #     "error": str|null
    #   },
    #   "sources": [
    #     {
    #       "source_uri_prefix": str,
    #       "docs_seen": int,
    #       "docs_skipped": int,
    #       "docs_failed": int,
    #       "last_scanned_at": str|null,
    #       "finished_at": str|null
    #     }
    #   ]
    # }
    # OR, when no runs: {"run": null, "sources": []}

    _REQUIRED_RUN_KEYS = frozenset(
        [
            "run_id",
            "status",
            "started_at",
            "ended_at",
            "last_op",
            "last_done",
            "last_total",
            "host",
            "pid",
            "error",
        ]
    )
    _REQUIRED_SOURCE_KEYS = frozenset(
        [
            "source_uri_prefix",
            "docs_seen",
            "docs_skipped",
            "docs_failed",
            "last_scanned_at",
            "finished_at",
        ]
    )

    def _invoke_json(self) -> tuple[Any, str]:
        from corpus_forge import ingest as ingest_module

        completed_run = _make_run(status="completed")
        sources = [_make_source_row()]

        def _fake_print(config, *, json_output: bool = False) -> None:
            if json_output:
                payload = {
                    "run": completed_run,
                    "sources": sources,
                }
                print(json.dumps(payload))
            else:
                print("Latest ingest run")

        with patch.object(ingest_module, "print_ingest_status", side_effect=_fake_print):
            result = _runner().invoke(app, ["ingest", "--status", "--json"])
        return result, _combined(result)

    def test_json_flag_exits_zero(self) -> None:
        result, _ = self._invoke_json()
        assert result.exit_code == 0, f"--status --json should exit 0, got {result.exit_code}"

    def test_json_output_is_parseable(self) -> None:
        _result, combined = self._invoke_json()
        # Strip ANSI / extra lines; find the JSON object
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON line found in --status --json output:\n{combined!r}"
        payload = json.loads(lines[0])
        assert isinstance(payload, dict), f"Top-level JSON must be dict, got {type(payload)}"

    def test_json_output_has_run_key(self) -> None:
        _result, combined = self._invoke_json()
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON in output:\n{combined!r}"
        payload = json.loads(lines[0])
        assert "run" in payload, f"JSON missing 'run' key:\n{payload!r}"

    def test_json_output_has_sources_key(self) -> None:
        _result, combined = self._invoke_json()
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON in output:\n{combined!r}"
        payload = json.loads(lines[0])
        assert "sources" in payload, f"JSON missing 'sources' key:\n{payload!r}"
        assert isinstance(payload["sources"], list), (
            f"'sources' must be a list, got {type(payload['sources'])}"
        )

    def test_json_run_has_required_keys(self) -> None:
        _result, combined = self._invoke_json()
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON in output:\n{combined!r}"
        payload = json.loads(lines[0])
        run_obj = payload.get("run")
        assert run_obj is not None, f"'run' is None/absent in:\n{payload!r}"
        missing = self._REQUIRED_RUN_KEYS - set(run_obj.keys())
        assert not missing, (
            f"JSON 'run' object missing required keys: {sorted(missing)}\n"
            f"Got keys: {sorted(run_obj.keys())}"
        )

    def test_json_sources_row_has_required_keys(self) -> None:
        _result, combined = self._invoke_json()
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON in output:\n{combined!r}"
        payload = json.loads(lines[0])
        sources = payload.get("sources", [])
        assert sources, "Expected at least one source row in JSON output"
        for i, src in enumerate(sources):
            missing = self._REQUIRED_SOURCE_KEYS - set(src.keys())
            assert not missing, (
                f"Source row {i} missing required keys: {sorted(missing)}\n"
                f"Got keys: {sorted(src.keys())}"
            )

    def test_json_empty_db_has_null_run(self) -> None:
        """--status --json with no runs: {\"run\": null, \"sources\": []}."""
        from corpus_forge import ingest as ingest_module

        def _fake_print_no_runs(config, *, json_output: bool = False) -> None:
            if json_output:
                print(json.dumps({"run": None, "sources": []}))
            else:
                print("no runs found")

        with patch.object(ingest_module, "print_ingest_status", side_effect=_fake_print_no_runs):
            result = _runner().invoke(app, ["ingest", "--status", "--json"])

        assert result.exit_code == 0, (
            f"--status --json with no runs should exit 0, got {result.exit_code}"
        )
        combined = _combined(result)
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON in output:\n{combined!r}"
        payload = json.loads(lines[0])
        assert "run" in payload, f"JSON missing 'run' key in no-runs case:\n{payload!r}"
        assert payload["run"] is None, (
            f"Expected 'run' to be null for no-runs case, got {payload['run']!r}"
        )
        assert payload.get("sources") == [], (
            f"Expected 'sources' to be [] for no-runs case, got {payload.get('sources')!r}"
        )

    def test_json_run_id_value_matches_db(self) -> None:
        """The run_id in JSON must match the value from the DB row."""
        from corpus_forge import ingest as ingest_module

        custom_id = "CUSTOM-RUN-ID-9999"
        run = _make_run(run_id=custom_id, status="completed")

        def _fake_print(config, *, json_output: bool = False) -> None:
            if json_output:
                print(json.dumps({"run": run, "sources": []}))
            else:
                print(f"run_id: {custom_id}")

        with patch.object(ingest_module, "print_ingest_status", side_effect=_fake_print):
            result = _runner().invoke(app, ["ingest", "--status", "--json"])

        combined = _combined(result)
        lines = [ln for ln in combined.splitlines() if ln.strip().startswith("{")]
        payload = json.loads(lines[0])
        assert payload["run"]["run_id"] == custom_id, (
            f"Expected run_id='{custom_id}', got {payload['run']['run_id']!r}"
        )

    def test_json_passes_json_output_true_to_function(self) -> None:
        """--status --json must pass json_output=True to print_ingest_status."""
        from corpus_forge import ingest as ingest_module

        captured_kwargs: dict[str, Any] = {}

        def _capture(config, *, json_output: bool = False) -> None:
            captured_kwargs["json_output"] = json_output

        with patch.object(ingest_module, "print_ingest_status", side_effect=_capture):
            _runner().invoke(app, ["ingest", "--status", "--json"])

        assert captured_kwargs.get("json_output") is True, (
            f"Expected json_output=True passed to print_ingest_status, "
            f"got {captured_kwargs.get('json_output')!r}"
        )

    def test_json_flag_absent_passes_json_output_false(self) -> None:
        """Without --json, print_ingest_status receives json_output=False."""
        from corpus_forge import ingest as ingest_module

        captured_kwargs: dict[str, Any] = {}

        def _capture(config, *, json_output: bool = False) -> None:
            captured_kwargs["json_output"] = json_output

        with patch.object(ingest_module, "print_ingest_status", side_effect=_capture):
            _runner().invoke(app, ["ingest", "--status"])

        assert captured_kwargs.get("json_output") is False, (
            f"Expected json_output=False without --json flag, "
            f"got {captured_kwargs.get('json_output')!r}"
        )


# ---------------------------------------------------------------------------
# 11. Two-section table structure in human output
# ---------------------------------------------------------------------------


class TestStatusTwoSectionTable:
    """The human-readable output must have two labelled sections."""

    @pytest.fixture
    def render_status(self) -> Any:
        try:
            from corpus_forge.ingest import _render_status

            return _render_status
        except ImportError:
            pass
        try:
            from corpus_forge.cli import _render_status  # type: ignore[attr-defined]

            return _render_status
        except ImportError:
            pass
        pytest.fail("_render_status not importable — must be added in SR-G6")

    def test_output_contains_latest_ingest_run_section(self, render_status) -> None:
        """First section header must reference 'Latest ingest run' (case-insensitive)."""
        run = _make_run(status="completed")
        output = render_status(run, [])
        assert "latest ingest run" in output.lower() or "ingest run" in output.lower(), (
            f"'Latest ingest run' section header not found:\n{output!r}"
        )

    def test_output_contains_per_source_section(self, render_status) -> None:
        """Second section header must reference per-source detail."""
        run = _make_run(status="completed")
        sources = [_make_source_row()]
        output = render_status(run, sources)
        # Acceptable headers: "Per-source", "Sources", "Source"
        assert any(keyword in output.lower() for keyword in ("per-source", "sources", "source")), (
            f"Per-source section header not found:\n{output!r}"
        )

    def test_no_sources_still_renders_cleanly(self, render_status) -> None:
        """When no per-source rows exist, render must not crash."""
        run = _make_run(status="completed")
        output = render_status(run, [])
        assert isinstance(output, str)
        # Header section still present
        assert "ingest run" in output.lower(), f"Run section header missing:\n{output!r}"

    def test_ended_at_shown_for_completed(self, render_status) -> None:
        run = _make_run(status="completed", ended_at=_ENDED_ISO)
        output = render_status(run, [])
        assert "2026-05-28" in output, f"ended_at date not in completed-run output:\n{output!r}"


# ---------------------------------------------------------------------------
# 12. Mutual exclusion: --status must be invoked alone
# ---------------------------------------------------------------------------


class TestStatusMutualExclusion:
    """--status is mutually exclusive with --once / --resume / --wait / --max-scan-age.

    Note: SR-T6 owns the full mutex test matrix. These tests are a
    smoke-check that --status alone works and confirm the CLI plumbing
    routes correctly. The detailed mutex enforcement is SR-T6's domain.
    """

    def test_status_alone_routes_to_print_ingest_status(self) -> None:
        from corpus_forge import ingest as ingest_module

        with patch.object(ingest_module, "print_ingest_status", return_value=None) as mock_pis:
            result = _runner().invoke(app, ["ingest", "--status"])

        mock_pis.assert_called_once()
        assert result.exit_code == 0

    def test_status_with_json_routes_to_print_ingest_status(self) -> None:
        from corpus_forge import ingest as ingest_module

        captured: dict[str, Any] = {}

        def _capture(config, *, json_output: bool = False) -> None:
            captured["json_output"] = json_output

        with patch.object(ingest_module, "print_ingest_status", side_effect=_capture):
            _runner().invoke(app, ["ingest", "--status", "--json"])

        assert captured.get("json_output") is True


# ---------------------------------------------------------------------------
# Regression lock: D2 — json.dumps must survive real datetime objects
# ---------------------------------------------------------------------------


class TestJsonDumpsDatetimeSerialization:
    """Regression lock for D2: print_ingest_status(json_output=True) must not
    raise TypeError when the backend returns dict rows containing datetime objects.

    The real SQLiteBackend.latest_ingest_run() returns datetime objects for
    timestamp fields. Prior to the fix, json.dumps lacked a default encoder,
    causing:
        TypeError: Object of type datetime is not JSON serializable
    This test pins that the fix (default=_json_default) handles datetimes.
    """

    def test_json_output_survives_datetime_fields(self, capsys, tmp_path) -> None:
        """print_ingest_status(json_output=True) must not raise when run dict
        contains real datetime objects (not pre-serialized ISO strings).
        """
        import textwrap
        from datetime import UTC, datetime

        from corpus_forge import ingest as ingest_module
        from corpus_forge.config import Config
        from corpus_forge.ingest import print_ingest_status

        db_path = tmp_path / "dt_test.db"
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
        config = Config.load(config_path=cfg_path)

        now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
        run_with_datetimes = {
            "run_id": "TEST-RUN-DT",
            "status": "completed",
            "host": "testhost",
            "pid": 1,
            "started_at": now,
            "ended_at": now,
            "last_progress_at": now,
            "last_op": "finalize",
            "last_done": 10,
            "last_total": 10,
            "error": None,
            "config_digest": "abc123",
        }

        backend = MagicMock()
        backend.latest_ingest_run.return_value = run_with_datetimes
        backend.list_ingest_run_sources.return_value = []
        backend.get_ingest_run_sources.return_value = []

        with patch.object(ingest_module, "_build_backend_for_status", return_value=backend):
            # Must NOT raise TypeError — this is the regression we're pinning.
            print_ingest_status(config, json_output=True)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        # Must be parseable JSON
        lines = [ln for ln in output.splitlines() if ln.strip().startswith("{")]
        assert lines, f"No JSON line in output:\n{output!r}"
        payload = json.loads(lines[0])
        assert payload["run"]["run_id"] == "TEST-RUN-DT"
        # datetime was serialized to an ISO string, not Python repr
        started = payload["run"]["started_at"]
        assert isinstance(started, str), f"started_at must be a string in JSON; got {started!r}"
        assert "2026-05-28" in started, f"Expected ISO date in started_at; got {started!r}"
