"""Coverage targeting :mod:`corpus_forge.admin.service` — the ``install``
and ``uninstall`` apply paths (``--apply`` branch + each platform helper).

The other tests in ``tests/admin/test_service_install.py`` cover the unit
*generators*.  This file exercises the side-effect helpers
(``_apply_systemd``, ``_apply_launchd``, ``_apply_schtasks``) plus the
matching uninstall branches.  We mock :mod:`subprocess` so no real
service binary is touched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from corpus_forge.admin import service as svc
from corpus_forge.admin import service_install as _install

# ── _resolve_kind ───────────────────────────────────────────────────────


def test_resolve_kind_explicit_systemd() -> None:
    assert svc._resolve_kind(systemd=True, launchd=False, schtasks=False, auto=False) == "systemd"


def test_resolve_kind_explicit_launchd() -> None:
    assert svc._resolve_kind(systemd=False, launchd=True, schtasks=False, auto=False) == "launchd"


def test_resolve_kind_explicit_schtasks() -> None:
    assert svc._resolve_kind(systemd=False, launchd=False, schtasks=True, auto=False) == "schtasks"


def test_resolve_kind_auto_falls_through_to_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_detect_platform", lambda: "systemd")
    assert svc._resolve_kind(False, False, False, True) == "systemd"


def test_resolve_kind_no_flag_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_detect_platform", lambda: "launchd")
    assert svc._resolve_kind(False, False, False, False) == "launchd"


def test_resolve_kind_multiple_flags_raises() -> None:
    import typer as _typer

    with pytest.raises(_typer.BadParameter):
        svc._resolve_kind(systemd=True, launchd=True, schtasks=False, auto=False)


# ── _detect_platform ────────────────────────────────────────────────────


def test_detect_platform_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.platform, "system", lambda: "Darwin")
    assert svc._detect_platform() == "launchd"


def test_detect_platform_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.platform, "system", lambda: "Windows")
    assert svc._detect_platform() == "schtasks"


def test_detect_platform_linux_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.platform, "system", lambda: "Linux")
    assert svc._detect_platform() == "systemd"


# ── _apply_systemd ──────────────────────────────────────────────────────


def test_apply_systemd_writes_unit_and_enables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "corpus-forge.service"
    monkeypatch.setattr(_install, "SYSTEMD_USER_UNIT_PATH", target)

    runs: list[list[str]] = []

    class _FakeResult:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **_kw):
        runs.append(list(cmd))
        return _FakeResult()

    monkeypatch.setattr(svc.subprocess, "run", _fake_run)
    svc._apply_systemd("[Unit]\nDescription=fake\n")

    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("[Unit]")
    # daemon-reload + enable were called.
    flat = [" ".join(r) for r in runs]
    assert any("daemon-reload" in c for c in flat)
    assert any("enable" in c for c in flat)


def test_apply_systemd_nonzero_warns_but_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.service"
    monkeypatch.setattr(_install, "SYSTEMD_USER_UNIT_PATH", target)

    class _FakeResult:
        returncode = 1
        stderr = "no systemd"

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    svc._apply_systemd("unit text\n")
    assert target.exists()


def test_apply_systemd_missing_systemctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "y.service"
    monkeypatch.setattr(_install, "SYSTEMD_USER_UNIT_PATH", target)

    def _missing(*_a, **_k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(svc.subprocess, "run", _missing)
    # Should swallow the FileNotFoundError and still write the unit.
    svc._apply_systemd("unit text\n")
    assert target.exists()


# ── _apply_launchd ──────────────────────────────────────────────────────


def test_apply_launchd_writes_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "com.corpus-forge.plist"
    monkeypatch.setattr(_install, "LAUNCHD_PLIST_PATH", target)

    class _FakeResult:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    svc._apply_launchd("<plist></plist>\n")
    assert target.exists()


def test_apply_launchd_nonzero_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "z.plist"
    monkeypatch.setattr(_install, "LAUNCHD_PLIST_PATH", target)

    class _FakeResult:
        returncode = 1
        stderr = ""

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    svc._apply_launchd("<plist/>\n")
    assert target.exists()


def test_apply_launchd_missing_launchctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "w.plist"
    monkeypatch.setattr(_install, "LAUNCHD_PLIST_PATH", target)

    def _missing(*_a, **_k):
        raise FileNotFoundError("launchctl")

    monkeypatch.setattr(svc.subprocess, "run", _missing)
    svc._apply_launchd("<plist/>\n")
    assert target.exists()


# ── _apply_schtasks ─────────────────────────────────────────────────────


def test_apply_schtasks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    svc._apply_schtasks(["schtasks", "/create", "/SC", "ONLOGON", "/TN", "x"])


def test_apply_schtasks_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 1
        stderr = "denied"

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    svc._apply_schtasks(["schtasks", "/create"])


def test_apply_schtasks_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*_a, **_k):
        raise FileNotFoundError("schtasks.exe")

    monkeypatch.setattr(svc.subprocess, "run", _missing)
    svc._apply_schtasks(["schtasks"])


# ── service_install_cmd CLI ─────────────────────────────────────────────


def test_install_systemd_prints_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "systemd")
    monkeypatch.setattr(_install, "generate_systemd_unit", lambda: "UNIT TEXT\n")
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0
    assert "UNIT TEXT" in result.stdout


def test_install_launchd_prints_plist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "launchd")
    monkeypatch.setattr(_install, "generate_launchd_plist", lambda: "<plist/>\n")
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0
    assert "<plist/>" in result.stdout


def test_install_schtasks_prints_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "schtasks")
    monkeypatch.setattr(_install, "generate_schtasks_command", lambda: ["schtasks", "/create"])
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0
    assert "schtasks" in result.stdout


def test_install_apply_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "systemd")
    monkeypatch.setattr(_install, "generate_systemd_unit", lambda: "UNIT")
    applied: list[str] = []

    def _record_apply(text: str) -> None:
        applied.append(text)

    monkeypatch.setattr(svc, "_apply_systemd", _record_apply)
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--apply"])
    assert result.exit_code == 0
    assert applied == ["UNIT"]


def test_install_apply_launchd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "launchd")
    monkeypatch.setattr(_install, "generate_launchd_plist", lambda: "<plist/>")
    applied: list[str] = []

    def _record_apply(text: str) -> None:
        applied.append(text)

    monkeypatch.setattr(svc, "_apply_launchd", _record_apply)
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--apply"])
    assert result.exit_code == 0
    assert applied == ["<plist/>"]


def test_install_apply_schtasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "schtasks")
    monkeypatch.setattr(_install, "generate_schtasks_command", lambda: ["schtasks", "/c"])
    applied: list[list[str]] = []

    def _record_apply(argv: list[str]) -> None:
        applied.append(argv)

    monkeypatch.setattr(svc, "_apply_schtasks", _record_apply)
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--apply"])
    assert result.exit_code == 0
    assert applied == [["schtasks", "/c"]]


def test_install_system_flag_refuses() -> None:
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--system"])
    assert result.exit_code == 2


# ── service_uninstall_cmd CLI ───────────────────────────────────────────


def test_uninstall_systemd_removes_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "corpus-forge.service"
    target.write_text("UNIT", encoding="utf-8")
    monkeypatch.setattr(_install, "SYSTEMD_USER_UNIT_PATH", target)
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "systemd")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: None)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0
    assert not target.exists()


def test_uninstall_systemd_no_unit_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "missing.service"
    monkeypatch.setattr(_install, "SYSTEMD_USER_UNIT_PATH", target)
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "systemd")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: None)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0


def test_uninstall_launchd_removes_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "com.corpus-forge.plist"
    target.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(_install, "LAUNCHD_PLIST_PATH", target)
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "launchd")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: None)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0
    assert not target.exists()


def test_uninstall_launchd_no_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "missing.plist"
    monkeypatch.setattr(_install, "LAUNCHD_PLIST_PATH", target)
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "launchd")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: None)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0


def test_uninstall_schtasks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "schtasks")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)

    class _FakeResult:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0


def test_uninstall_schtasks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "schtasks")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)

    class _FakeResult:
        returncode = 1
        stderr = "denied"

    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _FakeResult())
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0


def test_uninstall_schtasks_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_resolve_kind", lambda *a, **k: "schtasks")
    monkeypatch.setattr(svc, "stop_daemon", lambda: 0)

    def _missing(*_a, **_k):
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(svc.subprocess, "run", _missing)
    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0


# ── render_status: live + no-config (combined paths) ────────────────────


def test_uptime_seconds_psutil_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``_uptime_seconds`` prefers psutil over the mtime fallback."""

    class _FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return svc.time.time() - 30  # 30s ago

    class _FakePsutil:
        Process = _FakeProcess

    monkeypatch.setattr(svc, "_try_psutil", lambda: _FakePsutil)
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("123", encoding="utf-8")
    secs = svc._uptime_seconds(123, pid_file)
    assert secs is not None
    assert 0.0 <= secs <= 60.0


def test_uptime_seconds_mtime_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When psutil is missing, the helper falls back to the pid-file mtime."""

    monkeypatch.setattr(svc, "_try_psutil", lambda: None)
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("123", encoding="utf-8")
    secs = svc._uptime_seconds(123, pid_file)
    assert secs is not None
    assert secs >= 0.0


def test_uptime_seconds_returns_none_when_pid_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(svc, "_try_psutil", lambda: None)
    assert svc._uptime_seconds(123, tmp_path / "absent.pid") is None


def test_rss_bytes_returns_none_without_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_try_psutil", lambda: None)
    assert svc._rss_bytes(99999) is None


def test_rss_bytes_handles_psutil_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProcess:
        def __init__(self, pid: int) -> None:
            raise RuntimeError("no such process")

    class _FakePsutil:
        Process = _FakeProcess

    monkeypatch.setattr(svc, "_try_psutil", lambda: _FakePsutil)
    assert svc._rss_bytes(99999) is None


def test_try_psutil_returns_module_or_none() -> None:
    """When the real psutil is installed, ``_try_psutil`` returns it."""

    result = svc._try_psutil()
    # Either it's a module, or psutil is genuinely missing and we got None.
    assert result is None or hasattr(result, "Process")


# ── _read_mode edge: corrupted file ─────────────────────────────────────


def test_read_mode_corrupted_returns_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda *a, **k: str(tmp_path))
    mode = svc._mode_state_path()
    mode.parent.mkdir(parents=True, exist_ok=True)
    mode.write_text("gibberish\n", encoding="utf-8")
    assert svc._read_mode() == "background"


def test_read_mode_no_file_returns_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platformdirs

    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda *a, **k: str(tmp_path / "nope"))
    assert svc._read_mode() == "background"


def test_relative_age_negative_returns_raw_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the parsed timestamp is in the future, return it as-is."""

    from datetime import datetime

    past_now = datetime(2020, 1, 1)

    class _FakeDT:
        @classmethod
        def now(cls):
            return past_now

        @classmethod
        def strptime(cls, *args, **kwargs):
            return datetime.strptime(*args, **kwargs)

    with patch("corpus_forge.admin.service.datetime", _FakeDT):
        # Future timestamp → negative delta → returns raw string.
        assert svc._relative_age("2030-01-01 00:00:00") == "2030-01-01 00:00:00"


def test_relative_age_invalid_format_returns_raw() -> None:
    assert svc._relative_age("not a date") == "not a date"


def test_last_info_line_with_no_info_levels(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    log.write_text(
        "2026-05-18 12:00:00 [DEBUG  ] daemon: only debug\n"
        "2026-05-18 12:00:01 [WARNING] daemon: warn\n"
    )
    # No INFO lines → returns None.
    assert svc._last_info_line(log) is None


def test_active_datasets_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing config → ``_active_datasets`` swallows and returns []."""

    def _explode(*a, **k):
        raise FileNotFoundError("no config")

    from corpus_forge import config as _cfg

    monkeypatch.setattr(_cfg.Config, "load", classmethod(lambda cls, **kw: _explode()))
    assert svc._active_datasets() == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
