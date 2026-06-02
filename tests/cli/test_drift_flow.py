"""Phase L Wave 5 — end-to-end embedder-drift flow through the CLI.

Tests the hook points + dispatcher wired into:
- ``corpus-forge ingest`` (foreground / background)
- ``corpus-forge embed`` (same dispatcher)
- ``corpus-forge setup`` (post-wizard hook)
- ``corpus-forge daemon`` (WARNING log, no prompt)
- ``corpus-forge sync status`` (background worker row)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner


def _runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Redirect platformdirs cache / state files under ``tmp_path``."""

    cache = tmp_path / "cf-cache"
    cache.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "platformdirs.user_cache_dir",
        lambda *args, **kwargs: str(cache),
    )
    # Make sure both _marker and any cli helper using the same function
    # pick up the override.
    import corpus_forge.embedders._marker as marker_mod

    state_dir = (cache / "state").resolve()

    def _redirected_state_dir():
        return state_dir

    monkeypatch.setattr(marker_mod, "_state_dir", _redirected_state_dir)
    (cache / "state").mkdir(parents=True, exist_ok=True)
    return cache


@pytest.fixture
def interactive(monkeypatch):
    """Force ``_is_noninteractive_runtime`` to return False.

    CliRunner runs without a real TTY, so the default detection would
    classify the test environment as non-interactive and the prompt
    helper would return "later" without consulting ``Prompt.ask``.
    """

    from corpus_forge import cli as cli_mod

    monkeypatch.delenv("CF_NON_INTERACTIVE", raising=False)
    monkeypatch.setattr(cli_mod, "_is_non_interactive_runtime", lambda: False)


def _make_drift(name="qwen3_8b"):
    from corpus_forge.embedders.fingerprint import EmbedderDrift

    return EmbedderDrift(
        name=name,
        was_model_id="BAAI/bge-m3",
        was_dimension=1024,
        now_model_id="Qwen/Qwen3-Embedding-8B",
        now_dimension=1024,
        chunks_to_rerun=120,
        est_seconds=120 * 0.034,
        fingerprint_was="abc123def4567890",
        fingerprint_now="def456abc7890123",
    )


@pytest.fixture
def patched_handle_drift_imports(monkeypatch):
    """Patch out heavy dependencies (Config.load, backend, embed loop)."""

    from corpus_forge import cli as cli_mod

    fake_config = MagicMock()
    fake_config.backend = MagicMock()
    fake_config.backend.kind = "sqlite"
    fake_config.backend.dsn = ":memory:"
    fake_config.backend.schema = "corpus"

    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda cls: fake_config),
    )

    fake_backend = MagicMock()
    monkeypatch.setattr(cli_mod, "_get_backend", lambda config: fake_backend)
    return fake_config, fake_backend


def test_no_drift_no_panel(patched_handle_drift_imports):
    """``compare_active`` returns []. No panel render, ingest proceeds normally."""

    from corpus_forge import cli as cli_mod

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[]),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["ingest", "--once"])

    assert result.exit_code == 0, result.output
    # No "Embedder changed" panel content in stderr OR stdout.
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Embedder changed" not in combined


def test_ingest_drift_now_runs_foreground(patched_handle_drift_imports, interactive, monkeypatch):
    """``Prompt.ask -> "now"`` runs the embed backfill in-process."""

    from corpus_forge import cli as cli_mod

    drift = _make_drift()
    backfill_called: list = []
    save_called: list = []

    def _fake_backfill(name, dataset=None, limit=None):
        backfill_called.append(name)

    def _fake_save(config, backend):
        save_called.append(True)

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        patch("corpus_forge.embedders.fingerprint.save_active_fingerprint", side_effect=_fake_save),
        patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="now"),
        patch("corpus_forge.embed.backfill_embedder", side_effect=_fake_backfill),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["ingest", "--once"])

    assert result.exit_code == 0, result.output
    assert backfill_called == ["qwen3_8b"]
    assert save_called == [True]


def test_ingest_drift_later_writes_marker(patched_handle_drift_imports, interactive):
    """``Prompt.ask -> "later"`` records a pending marker."""

    from corpus_forge import cli as cli_mod
    from corpus_forge.embedders._marker import check_pending_or_skipped

    drift = _make_drift()

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="later"),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["ingest", "--once"])

    assert result.exit_code == 0, result.output
    assert check_pending_or_skipped("qwen3_8b", drift.fingerprint_now) == "pending"


def test_ingest_drift_skip_writes_suppression(patched_handle_drift_imports, interactive):
    """``Prompt.ask -> "skip"`` records a skipped marker with TTL."""

    from corpus_forge import cli as cli_mod
    from corpus_forge.embedders._marker import check_pending_or_skipped

    drift = _make_drift()

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="skip"),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["ingest", "--once"])

    assert result.exit_code == 0, result.output
    assert check_pending_or_skipped("qwen3_8b", drift.fingerprint_now) == "skipped"


def test_skipped_marker_suppresses_reprompt(patched_handle_drift_imports):
    """A pre-existing skipped marker prevents re-prompting on the next run."""

    from corpus_forge import cli as cli_mod
    from corpus_forge.embedders._marker import mark_skipped

    drift = _make_drift()

    # Pre-seed the marker (the user said "skip" on a previous run).
    mark_skipped(drift.name, fp_was=drift.fingerprint_was, fp_now=drift.fingerprint_now)

    ask_called: list = []

    def _ask(*args, **kwargs):
        ask_called.append(args)
        return "now"

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        patch("corpus_forge.embedders.drift_prompt.Prompt.ask", side_effect=_ask),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["ingest", "--once"])

    assert result.exit_code == 0, result.output
    # Prompt was NOT invoked — suppression respected.
    assert ask_called == []


def test_ingest_background_flag_spawns_subprocess(patched_handle_drift_imports):
    """``--background`` + ``now`` → ``subprocess.Popen`` (no foreground embed)."""

    from corpus_forge import cli as cli_mod

    drift = _make_drift()
    popen_calls: list = []

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            popen_calls.append((args, kwargs))
            self.pid = 42424

    backfill_called: list = []

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="now"),
        patch("subprocess.Popen", _FakePopen),
        patch(
            "corpus_forge.embed.backfill_embedder",
            side_effect=lambda *a, **k: backfill_called.append(a),
        ),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["--background", "ingest", "--once"])

    assert result.exit_code == 0, result.output
    assert popen_calls, "subprocess.Popen was not invoked"
    # Foreground backfill should NOT have been called.
    assert backfill_called == []

    # Popen args: python -m corpus_forge embed -e <name>
    args, kwargs = popen_calls[0]
    invocation = args[0]
    assert "corpus_forge" in invocation
    assert "embed" in invocation
    assert "-e" in invocation
    assert "qwen3_8b" in invocation
    # Detached stdio.
    import subprocess as _sp

    assert kwargs.get("stdin") == _sp.DEVNULL
    assert kwargs.get("stdout") == _sp.DEVNULL
    assert kwargs.get("stderr") == _sp.DEVNULL
    assert kwargs.get("start_new_session") is True


def test_background_subprocess_writes_pid_file(patched_handle_drift_imports, isolate_state):
    """Background spawn writes the worker pid under ``<cache>/state/embed-worker.pid``."""

    from corpus_forge import cli as cli_mod

    drift = _make_drift()

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 99001

    with (
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="now"),
        patch("subprocess.Popen", _FakePopen),
        patch("corpus_forge.ingest.main"),
    ):
        result = _runner().invoke(cli_mod.app, ["--background", "ingest", "--once"])

    assert result.exit_code == 0, result.output
    pid_path = Path(isolate_state) / "state" / "embed-worker.pid"
    assert pid_path.exists()
    assert pid_path.read_text(encoding="utf-8").strip() == "99001"


def test_daemon_emits_warning_log_on_drift(caplog, monkeypatch):
    """Daemon path emits WARNING on drift and does NOT prompt."""

    from corpus_forge import daemon as daemon_mod

    drift = _make_drift()
    fake_config = MagicMock()
    fake_config.backend = MagicMock(kind="sqlite", dsn=":memory:", schema="corpus")
    fake_backend = MagicMock()

    monkeypatch.setattr("corpus_forge.config.Config.load", classmethod(lambda cls: fake_config))

    # Don't enter the real sync engines, block in the sleep loop, or
    # tear down ``caplog``'s handlers by re-initialising logging.
    monkeypatch.setattr(daemon_mod, "run_daemon", lambda config: None)
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda _seconds: (_ for _ in ()).throw(SystemExit(0)))
    monkeypatch.setattr("corpus_forge.logging_config.init_logging", lambda *a, **k: None)

    def _bail():
        raise SystemExit(0)

    with (
        patch("corpus_forge.cli._get_backend", return_value=fake_backend),
        patch("corpus_forge.embedders.fingerprint.compare_active", return_value=[drift]),
        # Catastrophic failure if the daemon hits Prompt.ask.
        patch(
            "corpus_forge.embedders.drift_prompt.Prompt.ask",
            side_effect=AssertionError("daemon must not prompt"),
        ),
        caplog.at_level(logging.WARNING, logger="corpus_forge.embedders.fingerprint"),
        pytest.raises(SystemExit),
    ):
        daemon_mod.main()

    messages = [r.message for r in caplog.records]
    assert any("Embedder drift" in m for m in messages), messages


def test_sync_status_reports_running_worker(patched_handle_drift_imports, isolate_state):
    """``sync status`` reports the worker pid when the process is alive."""

    from corpus_forge import cli as cli_mod

    fake_config, _fake_backend = patched_handle_drift_imports
    # Make sync status's per-dataset loop a no-op.
    fake_config.datasets = []

    pid_path = Path(isolate_state) / "state" / "embed-worker.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")  # current process is alive

    result = _runner().invoke(cli_mod.app, ["sync", "status"])

    assert result.exit_code == 0, result.output
    output = (result.stdout or "") + (result.stderr or "")
    assert "Background embed-worker" in output
    assert f"pid={os.getpid()}" in output


def test_sync_status_reports_none_when_no_pid(patched_handle_drift_imports):
    """``sync status`` reports "none" when no pid file exists."""

    from corpus_forge import cli as cli_mod

    fake_config, _fake_backend = patched_handle_drift_imports
    fake_config.datasets = []

    result = _runner().invoke(cli_mod.app, ["sync", "status"])

    assert result.exit_code == 0, result.output
    output = (result.stdout or "") + (result.stderr or "")
    assert "Background embed-worker" in output
    assert "none" in output


def test_sync_status_reports_none_when_pid_dead(patched_handle_drift_imports, isolate_state):
    """``sync status`` reports "none" when the pid file points to a dead process."""

    from corpus_forge import cli as cli_mod

    fake_config, _fake_backend = patched_handle_drift_imports
    fake_config.datasets = []

    pid_path = Path(isolate_state) / "state" / "embed-worker.pid"
    pid_path.write_text("99999999", encoding="utf-8")  # unlikely to exist

    result = _runner().invoke(cli_mod.app, ["sync", "status"])

    assert result.exit_code == 0, result.output
    output = (result.stdout or "") + (result.stderr or "")
    assert "Background embed-worker" in output
    assert "none" in output
