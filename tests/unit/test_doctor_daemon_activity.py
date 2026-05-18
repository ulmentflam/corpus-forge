"""Phase L Wave 6 — doctor ``daemon_activity`` check (W6-05).

A new doctor probe reads the tail of ``<log_dir>/daemon.log`` and
reports the most recent INFO line + delta to now.  Missing log file →
``SKIP`` (the daemon may simply not be running yet).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    return log_dir


def test_skip_when_daemon_log_missing(isolated_log_dir: Path) -> None:
    from corpus_forge.doctor.checks import CheckStatus, _check_daemon_activity

    result = _check_daemon_activity()
    assert result.name == "daemon_activity"
    assert result.status == CheckStatus.SKIP
    assert "no daemon log" in result.detail.lower()


def test_reports_last_info_line(isolated_log_dir: Path) -> None:
    from corpus_forge.doctor.checks import CheckStatus, _check_daemon_activity

    # Write a daemon.log with a single INFO line dated "now".
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    log_path = isolated_log_dir / "daemon.log"
    log_path.write_text(f"{ts}.123 [INFO   ] corpus_forge.daemon.lifecycle: heartbeat\n")

    result = _check_daemon_activity()
    assert result.name == "daemon_activity"
    assert result.status == CheckStatus.OK
    # Detail mentions either "Last activity" or "heartbeat" so the
    # rendered report is informative.
    assert "heartbeat" in result.detail.lower() or "last activity" in result.detail.lower()


def test_skip_when_no_info_line(isolated_log_dir: Path) -> None:
    """File exists but has no INFO lines → SKIP (don't fail-spam)."""

    from corpus_forge.doctor.checks import CheckStatus, _check_daemon_activity

    log_path = isolated_log_dir / "daemon.log"
    log_path.write_text("garbage line\n")

    result = _check_daemon_activity()
    assert result.status in (CheckStatus.SKIP, CheckStatus.WARN)


def test_check_registered_in_run_doctor(isolated_log_dir: Path) -> None:
    from corpus_forge.doctor import run_doctor

    report = run_doctor()
    names = [r.name for r in report.results]
    assert "daemon_activity" in names


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
