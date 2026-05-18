"""Tests for ``corpus-forge service status`` (Phase L Wave 8).

Covers the pure renderer at :func:`corpus_forge.admin.service.render_status`
+ the helpers under it.  No subprocess required — we mock pid liveness
and patch psutil so the suite works on CI runners without psutil
installed.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import platformdirs
import pytest
from rich.console import Console

from corpus_forge.admin import foreground as _fg
from corpus_forge.admin import service as svc
from corpus_forge.ui import theme as _ui_theme


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect platformdirs caches + the CF_LOG_DIR so each test is hermetic."""

    cache = tmp_path / "cache"
    log_dir = tmp_path / "logs"
    cache.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    def _fake_cache_dir(name: str, *args, **kwargs) -> str:
        return str(cache / name)

    monkeypatch.setattr(platformdirs, "user_cache_dir", _fake_cache_dir)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))
    # `corpus_forge.logging_config._LOG_DIR` is a module-level cache that
    # bypasses CF_LOG_DIR once it's been set by a previous `init_logging`
    # call in the same test session (e.g. by `tests/diagnostics/test_bug_report.py`).
    # Clear it so this test's CF_LOG_DIR takes effect.
    import corpus_forge.logging_config as _lc

    monkeypatch.setattr(_lc, "_LOG_DIR", None)


def _render() -> str:
    """Render `service status` to a string for assertion."""

    buffer = io.StringIO()
    # Width tall enough to keep the value column intact even when the
    # log-file path is a long tmp_path under /private/var/folders/...
    console = Console(
        file=buffer,
        width=400,
        force_terminal=False,
        color_system=None,
        theme=_ui_theme.build_theme(),
    )
    svc.render_status(console=console)
    return buffer.getvalue()


# ── render_status: no pid file ──────────────────────────────────────────


def test_render_status_no_pid_file_shows_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    out = _render()
    assert "not running" in out


def test_render_status_no_pid_file_shows_log_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    out = _render()
    # The log-file path row should always render.
    assert "log file" in out
    assert "daemon.log" in out


def test_render_status_no_pid_file_handles_missing_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    out = _render()
    assert "no daemon.log yet" in out


# ── render_status: live pid (mocked) ────────────────────────────────────


def test_render_status_live_pid_shows_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    # Our own pid will always be alive — write it to the pid file.
    import os

    _fg.write_pid("daemon", os.getpid())
    out = _render()
    assert "daemon pid" in out
    assert str(os.getpid()) in out


def test_render_status_live_pid_uptime_via_pid_mtime_when_psutil_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uptime must fall back to the pid-file mtime when psutil is unavailable."""

    _isolate(monkeypatch, tmp_path)

    # Force the psutil probe to fail so the mtime fallback runs.
    monkeypatch.setattr(svc, "_try_psutil", lambda: None)

    import os

    _fg.write_pid("daemon", os.getpid())
    out = _render()
    # When psutil is absent the RSS row should say "n/a"; uptime row
    # should still render off the pid-file mtime (so a non-empty value).
    assert "memory (rss)" in out
    assert "n/a" in out
    assert "uptime" in out


def test_render_status_live_pid_memory_with_psutil(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When psutil is present its RSS / create_time take precedence."""

    _isolate(monkeypatch, tmp_path)

    fake_now = 1_700_000_000.0

    class _FakeMem:
        rss = 87 * 1024 * 1024  # 87 MB

    class _FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return fake_now - 125  # 2m 5s ago

        def memory_info(self):
            return _FakeMem()

    class _FakePsutil:
        Process = _FakeProcess

    monkeypatch.setattr(svc, "_try_psutil", lambda: _FakePsutil)
    monkeypatch.setattr(svc.time, "time", lambda: fake_now)

    import os

    _fg.write_pid("daemon", os.getpid())
    out = _render()
    assert "87.0 MB" in out
    # 125s = 2m bucket.
    assert "2m" in out


# ── render_status: last INFO line + active datasets ────────────────────


def test_render_status_renders_last_info_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    log_dir = tmp_path / "logs"
    (log_dir / "daemon.log").write_text(
        "2026-05-18 12:00:00 [DEBUG  ] x: noise\n"
        "2026-05-18 12:00:01 [INFO   ] daemon: scanning notes\n"
        "2026-05-18 12:00:02 [INFO   ] daemon: embedding batch 7/40\n"
    )
    out = _render()
    assert "embedding batch 7/40" in out


def test_render_status_no_info_line_in_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    log_dir = tmp_path / "logs"
    (log_dir / "daemon.log").write_text(
        "garbage line without a level prefix\nanother non-matching line\n"
    )
    out = _render()
    assert "no INFO lines parsed" in out


def test_render_status_active_datasets_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a Config is loadable, ``service status`` lists dataset names."""

    _isolate(monkeypatch, tmp_path)

    class _FakeDataset:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeConfig:
        datasets: list = [_FakeDataset("alpha"), _FakeDataset("beta")]  # noqa: RUF012

        @classmethod
        def load(cls):
            return cls()

    with patch("corpus_forge.config.Config", _FakeConfig):
        out = _render()
    assert "alpha" in out
    assert "beta" in out


def test_render_status_no_config_shows_none_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    # Force the ``_active_datasets`` helper to return [] (mirrors what
    # happens when ``Config.load`` raises FileNotFoundError on a fresh
    # install). We patch the helper directly rather than relying on
    # environment isolation — the developer's actual config file at
    # ``~/.config/corpus-forge/config.toml`` would otherwise be picked
    # up by ``Config.load`` on the local box.
    monkeypatch.setattr(svc, "_active_datasets", lambda: [])
    out = _render()
    assert "none configured" in out


# ── render_status: embed-worker row ────────────────────────────────────


def test_render_status_embed_worker_visible_when_pid_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    import os

    _fg.write_pid("embed-worker", os.getpid())
    out = _render()
    assert "embed-worker" in out
    assert str(os.getpid()) in out


def test_render_status_embed_worker_hidden_when_no_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    out = _render()
    # Row label still present but value is "not running".
    assert "embed-worker" in out


# ── _format helpers ─────────────────────────────────────────────────────


def test_format_uptime_buckets() -> None:
    assert svc._format_uptime(5) == "5s"
    assert svc._format_uptime(125) == "2m"
    assert svc._format_uptime(3 * 3600 + 5) == "3h"
    assert svc._format_uptime(2 * 86400 + 10) == "2d"


def test_format_bytes_mb_and_gb() -> None:
    assert svc._format_bytes(123 * 1024 * 1024) == "123.0 MB"
    # 2 GiB → 2.00 GB (the unit prefix collapses at the 1024 MiB threshold).
    assert svc._format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"


def test_relative_age_buckets() -> None:
    from datetime import datetime

    now = datetime(2026, 5, 18, 12, 0, 0)

    class _FakeDateTime:
        @classmethod
        def now(cls):
            return now

        @classmethod
        def strptime(cls, *args, **kwargs):
            return datetime.strptime(*args, **kwargs)

    with patch("corpus_forge.admin.service.datetime", _FakeDateTime):
        assert svc._relative_age("2026-05-18 11:59:55") == "5s ago"
        assert svc._relative_age("2026-05-18 11:55:00") == "5m ago"
        assert svc._relative_age("2026-05-18 09:00:00") == "3h ago"
        assert svc._relative_age("2026-05-15 12:00:00") == "3d ago"


def test_last_info_line_returns_most_recent_info(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    log.write_text(
        "2026-05-18 12:00:00 [INFO   ] daemon: first\n"
        "2026-05-18 12:00:01 [WARNING] daemon: warn line\n"
        "2026-05-18 12:00:02 [INFO   ] daemon: most recent info\n"
        "2026-05-18 12:00:03 [DEBUG  ] daemon: debug after\n"
    )
    result = svc._last_info_line(log)
    assert result is not None
    ts, msg = result
    assert ts == "2026-05-18 12:00:02"
    assert msg == "most recent info"


def test_last_info_line_missing_log_returns_none(tmp_path: Path) -> None:
    assert svc._last_info_line(tmp_path / "missing.log") is None
