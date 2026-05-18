"""Tests for ``corpus-forge service`` lifecycle verbs (Phase L Wave 8).

Covers start (refuses when already running), stop (SIGTERM semantics),
restart (preserves mode), and the deprecated ``daemon`` alias (warns).
"""

from __future__ import annotations

import signal
from pathlib import Path
from unittest.mock import patch

import platformdirs
import pytest
from typer.testing import CliRunner

from corpus_forge.admin import foreground as _fg
from corpus_forge.admin import service as svc


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hermetic platformdirs cache + log dir per test."""

    cache = tmp_path / "cache"
    log_dir = tmp_path / "logs"
    cache.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    def _fake_cache_dir(name: str, *args, **kwargs) -> str:
        return str(cache / name)

    monkeypatch.setattr(platformdirs, "user_cache_dir", _fake_cache_dir)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))
    # See test_service_status._isolate — the module-level `_LOG_DIR`
    # cache in logging_config would otherwise win over CF_LOG_DIR.
    import corpus_forge.logging_config as _lc

    monkeypatch.setattr(_lc, "_LOG_DIR", None)


# ── start_daemon_foreground / start_daemon_background ───────────────────


def test_start_foreground_refuses_when_pid_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    import os

    _fg.write_pid("daemon", os.getpid())

    rc = svc.start_daemon_foreground()
    assert rc == 1


def test_start_background_refuses_when_pid_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    import os

    _fg.write_pid("daemon", os.getpid())

    rc = svc.start_daemon_background()
    assert rc == 1


def test_start_foreground_invokes_daemon_main_and_clears_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    invoked: dict[str, bool] = {"called": False}

    def _fake_main():
        invoked["called"] = True
        # Pid must be set while daemon runs.
        assert _fg.read_pid("daemon") is not None

    with patch("corpus_forge.daemon.main", _fake_main):
        rc = svc.start_daemon_foreground()

    assert rc == 0
    assert invoked["called"] is True
    # Pid file cleared on exit.
    assert not _fg.pid_path("daemon").exists()


def test_start_foreground_writes_mode_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    with patch("corpus_forge.daemon.main", lambda: None):
        svc.start_daemon_foreground()

    mode_file = _fg.state_dir() / "daemon.mode"
    assert mode_file.exists()
    assert mode_file.read_text(encoding="utf-8").strip() == "foreground"


def test_start_foreground_handles_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    def _raise_kbi():
        raise KeyboardInterrupt

    with patch("corpus_forge.daemon.main", _raise_kbi):
        rc = svc.start_daemon_foreground()
    assert rc == 0
    assert not _fg.pid_path("daemon").exists()


# ── stop_daemon ─────────────────────────────────────────────────────────


def test_stop_daemon_no_pid_file_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    rc = svc.stop_daemon()
    assert rc == 0


def test_stop_daemon_sends_sigterm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    import os

    fake_pid = 99999
    # Bypass read_pid's liveness check by patching it.
    monkeypatch.setattr(
        svc._fg, "read_pid", lambda component: fake_pid if component == "daemon" else None
    )

    sent: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    monkeypatch.setattr(os, "kill", _fake_kill)

    # Make the polling loop see the process as gone after the first
    # SIGTERM probe — we already mocked read_pid to always return the
    # pid, so undo that for the polling phase.  Simplest: count probes
    # and switch behavior.
    probe_count = {"n": 0}

    def _fake_read_pid(component: str) -> int | None:
        if component != "daemon":
            return None
        probe_count["n"] += 1
        if probe_count["n"] == 1:
            return fake_pid  # initial check
        return None  # exit detected

    monkeypatch.setattr(svc._fg, "read_pid", _fake_read_pid)

    rc = svc.stop_daemon()
    assert rc == 0
    # At least one SIGTERM was sent to our fake pid.
    assert (fake_pid, signal.SIGTERM) in sent


def test_stop_daemon_escalates_to_sigkill_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    import os

    fake_pid = 99999

    # Read_pid always reports alive — the polling loop will time out and
    # escalate to SIGKILL.
    monkeypatch.setattr(
        svc._fg,
        "read_pid",
        lambda component: fake_pid if component == "daemon" else None,
    )

    # Shrink the timeout for the test.
    monkeypatch.setattr(svc, "_STOP_TIMEOUT_SECS", 0.05)
    monkeypatch.setattr(svc, "_STOP_POLL_INTERVAL_SECS", 0.01)

    sent: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    monkeypatch.setattr(os, "kill", _fake_kill)

    rc = svc.stop_daemon()
    assert rc == 0
    assert (fake_pid, signal.SIGTERM) in sent
    assert (fake_pid, signal.SIGKILL) in sent


def test_stop_daemon_already_dead_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    import os

    fake_pid = 99999
    monkeypatch.setattr(
        svc._fg,
        "read_pid",
        lambda component: fake_pid if component == "daemon" else None,
    )

    def _raise_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _raise_lookup)

    rc = svc.stop_daemon()
    assert rc == 0


# ── restart ────────────────────────────────────────────────────────────


def test_restart_preserves_background_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    # Stash mode = background.
    (_fg.state_dir() / "daemon.mode").write_text("background", encoding="utf-8")

    stop_called = {"n": 0}
    started_kind = {"kind": None}

    def _fake_stop() -> int:
        stop_called["n"] += 1
        return 0

    def _fake_start_bg() -> int:
        started_kind["kind"] = "background"
        return 0

    def _fake_start_fg() -> int:
        started_kind["kind"] = "foreground"
        return 0

    monkeypatch.setattr(svc, "stop_daemon", _fake_stop)
    monkeypatch.setattr(svc, "start_daemon_background", _fake_start_bg)
    monkeypatch.setattr(svc, "start_daemon_foreground", _fake_start_fg)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "restart"])
    assert result.exit_code == 0, result.output
    assert stop_called["n"] == 1
    assert started_kind["kind"] == "background"


def test_restart_preserves_foreground_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    (_fg.state_dir() / "daemon.mode").write_text("foreground", encoding="utf-8")

    started_kind = {"kind": None}

    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)
    monkeypatch.setattr(
        svc, "start_daemon_foreground", lambda: started_kind.__setitem__("kind", "foreground") or 0
    )
    monkeypatch.setattr(
        svc, "start_daemon_background", lambda: started_kind.__setitem__("kind", "background") or 0
    )

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "restart"])
    assert result.exit_code == 0
    assert started_kind["kind"] == "foreground"


def test_restart_defaults_to_background_when_no_mode_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    started_kind = {"kind": None}

    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)
    monkeypatch.setattr(
        svc, "start_daemon_foreground", lambda: started_kind.__setitem__("kind", "foreground") or 0
    )
    monkeypatch.setattr(
        svc, "start_daemon_background", lambda: started_kind.__setitem__("kind", "background") or 0
    )

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "restart"])
    assert result.exit_code == 0
    assert started_kind["kind"] == "background"


# ── start CLI verb ──────────────────────────────────────────────────────


def test_service_start_default_is_foreground(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    called = {"kind": None}
    monkeypatch.setattr(
        svc, "start_daemon_foreground", lambda: called.__setitem__("kind", "foreground") or 0
    )
    monkeypatch.setattr(
        svc, "start_daemon_background", lambda: called.__setitem__("kind", "background") or 0
    )

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "start"])
    assert result.exit_code == 0, result.output
    assert called["kind"] == "foreground"


def test_service_start_background_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    called = {"kind": None}
    monkeypatch.setattr(
        svc, "start_daemon_foreground", lambda: called.__setitem__("kind", "foreground") or 0
    )
    monkeypatch.setattr(
        svc, "start_daemon_background", lambda: called.__setitem__("kind", "background") or 0
    )

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "start", "--background"])
    assert result.exit_code == 0
    assert called["kind"] == "background"


def test_service_start_short_b_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    called = {"kind": None}
    monkeypatch.setattr(
        svc, "start_daemon_background", lambda: called.__setitem__("kind", "background") or 0
    )

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "start", "-b"])
    assert result.exit_code == 0
    assert called["kind"] == "background"


def test_service_start_rejects_both_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "start", "-b", "--foreground"])
    assert result.exit_code != 0


# ── Deprecated `daemon` alias ───────────────────────────────────────────


def test_deprecated_daemon_command_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    invoked = {"called": False}
    monkeypatch.setattr(
        svc, "start_daemon_foreground", lambda: invoked.__setitem__("called", True) or 0
    )

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["daemon"])
    assert result.exit_code == 0, result.output
    # The deprecation warning should be visible to the user.
    assert "deprecated" in result.output.lower() or "service start" in result.output
    assert invoked["called"] is True


# ── service stop CLI surface ────────────────────────────────────────────


def test_service_stop_no_pid_file_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "stop"])
    assert result.exit_code == 0


# ── service status smoke ───────────────────────────────────────────────


def test_service_status_via_cli_runner_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "status"])
    assert result.exit_code == 0
