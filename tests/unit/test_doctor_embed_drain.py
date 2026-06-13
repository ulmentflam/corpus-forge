"""Unit tests for the fleet-5 `doctor` embed-drain check (`_check_embed_drain`).

`[service] embed_drain = true` only drains the backlog if the managed daemon
is actually running; the check warns when it's on but the service is down.
Takes an already-loaded Config (never calls Config.load), so these tests only
patch the daemon liveness probe — no config-less-CI trap.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from corpus_forge.doctor.checks import CheckStatus, _check_embed_drain


def _cfg(embed_drain: bool) -> SimpleNamespace:
    return SimpleNamespace(service=SimpleNamespace(embed_drain=embed_drain))


def test_embed_drain_disabled_is_ok() -> None:
    result = _check_embed_drain(_cfg(False))
    assert result.status is CheckStatus.OK
    assert "disabled" in result.detail.lower()


def test_embed_drain_enabled_and_daemon_running_is_ok() -> None:
    with patch("corpus_forge.admin.foreground.read_pid", return_value=4321):
        result = _check_embed_drain(_cfg(True))
    assert result.status is CheckStatus.OK
    assert "running" in result.detail.lower()


def test_embed_drain_enabled_but_daemon_down_warns() -> None:
    with patch("corpus_forge.admin.foreground.read_pid", return_value=None):
        result = _check_embed_drain(_cfg(True))
    assert result.status is CheckStatus.WARN
    assert "service install" in result.detail
    assert "won't drain" in result.detail


def test_embed_drain_missing_service_block_degrades_to_disabled() -> None:
    # A config without a `service` attribute (defensive) → treated as off.
    result = _check_embed_drain(SimpleNamespace())
    assert result.status is CheckStatus.OK
    assert "disabled" in result.detail.lower()
