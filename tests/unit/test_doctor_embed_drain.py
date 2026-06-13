"""Doctor check for the fleet-5 embed-drain config vs. service (RFC fleet-5).

``embed_drain`` on but the managed daemon not running → WARN (the backlog
won't drain); off → OK; on + daemon running → OK. The config is a tiny
stand-in and ``read_pid`` is patched so no real daemon/DB is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from corpus_forge.doctor.checks import CheckStatus, _check_embed_drain


def _cfg(embed_drain: bool) -> SimpleNamespace:
    return SimpleNamespace(service=SimpleNamespace(embed_drain=embed_drain))


class TestCheckEmbedDrain:
    def test_disabled_is_ok(self) -> None:
        result = _check_embed_drain(_cfg(False))
        assert result.status is CheckStatus.OK
        assert "disabled" in result.detail

    def test_enabled_and_daemon_running_is_ok(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=4321):
            result = _check_embed_drain(_cfg(True))
        assert result.status is CheckStatus.OK
        assert "running" in result.detail

    def test_enabled_but_daemon_down_warns_with_fix(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=None):
            result = _check_embed_drain(_cfg(True))
        assert result.status is CheckStatus.WARN
        assert "service install" in result.detail

    def test_missing_service_block_is_ok(self) -> None:
        # A config object with no `service` attribute degrades to "disabled".
        result = _check_embed_drain(SimpleNamespace())
        assert result.status is CheckStatus.OK
