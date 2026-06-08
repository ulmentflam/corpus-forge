"""Tests for the informational ``tailscale`` doctor check (rfc-fleet-4).

Mirrors ``test_doctor_embed_claims.py``. The check NEVER hard-fails doctor —
every outcome is ``OK`` or ``WARN`` (or ``SKIP`` when config didn't load),
never ``FAIL``. What we pin:

1. Not configured (disabled + no ts://) → ``OK`` "not configured", and
   crucially NO shellout (``_load_status`` / ``_invoke_status`` untouched).
2. Configured but binary absent / daemon down → ``WARN`` (daemon reason).
3. Per-name resolve failure → ``WARN`` naming that name; others still probed.
4. Connect refused → ``WARN`` naming that name + the connect failure.
5. All-good → ``OK`` "n ts:// endpoint(s) reachable".
6. No-port endpoint → resolve-only, NO connect attempted for it.
7. The check NEVER returns ``FAIL`` on any branch.
8. Registered in ``run_doctor`` (loaded + config-missing branches).

The tailscale net layer (``_load_status`` / ``resolve``) and
``socket.create_connection`` are patched — the real binary and real sockets
are never on the test critical path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.doctor.checks import (
    _TS_CONNECT_TIMEOUT_S,
    CheckStatus,
    _check_tailscale,
    run_doctor,
)
from corpus_forge.net.tailscale import TailscaleUnavailable

CHECKS = "corpus_forge.doctor.checks"
TS = "corpus_forge.net.tailscale"


def _cfg(
    *,
    enabled: bool = False,
    prefer_magicdns: bool = True,
    endpoints: list[tuple[str, str]] | None = None,
) -> MagicMock:
    """Build a config double.

    ``endpoints`` is the ``(path, value)`` list ``_tailscale_endpoint_values``
    returns; we patch that scanner to return it directly so the test doesn't
    have to build a full ``Config`` with every sub-block.
    """
    cfg = MagicMock()
    cfg.tailscale.enabled = enabled
    cfg.tailscale.prefer_magicdns = prefer_magicdns
    cfg._endpoints = endpoints or []
    return cfg


def _scanner(cfg: MagicMock):
    """Patch the #112 scanner to return whatever the cfg carries."""
    return patch(
        "corpus_forge.config._tailscale_endpoint_values",
        return_value=cfg._endpoints,
    )


class TestNotConfigured:
    def test_disabled_no_ts_is_ok_no_shellout(self) -> None:
        cfg = _cfg(enabled=False, endpoints=[("backend.dsn", "postgresql://h/db")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status") as load,
            patch(f"{TS}._invoke_status") as invoke,
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.OK
        assert "not configured" in result.detail
        # The whole point of the fast path: no shellout on a box without TS.
        load.assert_not_called()
        invoke.assert_not_called()


class TestBinaryAbsent:
    def test_enabled_binary_absent_warns(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("ollama.base_url", "ts://gb10:11434")])
        with (
            _scanner(cfg),
            patch(
                f"{TS}._load_status",
                side_effect=TailscaleUnavailable("binary not found", reason="daemon"),
            ),
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.WARN
        assert "binary not found" in result.detail

    def test_ts_present_but_disabled_still_runs_configured_path(self) -> None:
        # A ts:// value with enabled=False is still the "configured" path:
        # we must probe the daemon (the load-time validator catches the
        # disabled-config error elsewhere; doctor reports daemon health).
        cfg = _cfg(enabled=False, endpoints=[("ollama.base_url", "ts://gb10:11434")])
        with (
            _scanner(cfg),
            patch(
                f"{TS}._load_status",
                side_effect=TailscaleUnavailable("daemon stopped", reason="daemon"),
            ),
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.WARN
        assert "daemon stopped" in result.detail


class TestPerNameResolve:
    def test_resolve_failure_warns_naming_name(self) -> None:
        cfg = _cfg(
            enabled=True,
            endpoints=[
                ("ollama.base_url", "ts://goodbox:11434"),
                ("classifier.llm_url", "ts://badbox:8000"),
            ],
        )

        def fake_resolve(name: str, *, prefer_magicdns: bool = True) -> str:
            if name == "badbox":
                raise TailscaleUnavailable("no peer named 'badbox'", reason="name")
            return name

        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=fake_resolve),
            patch("socket.create_connection") as conn,
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.WARN
        assert "badbox" in result.detail
        assert "no peer named" in result.detail
        # The good box still got probed (failures are collected, not
        # short-circuited).
        conn.assert_called_once_with(("goodbox", 11434), timeout=_TS_CONNECT_TIMEOUT_S)


class TestConnectRefused:
    def test_connect_refused_warns_naming_name(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("ollama.base_url", "ts://gb10:11434")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=lambda n, **k: n),
            patch("socket.create_connection", side_effect=ConnectionRefusedError("refused")),
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.WARN
        assert "gb10" in result.detail
        assert "11434" in result.detail
        assert "refused" in result.detail

    def test_connect_timeout_warns(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("ollama.base_url", "ts://gb10:11434")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=lambda n, **k: n),
            patch("socket.create_connection", side_effect=TimeoutError("timed out")),
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.WARN
        assert "gb10" in result.detail


class TestAllGood:
    def test_all_resolve_and_connect_ok(self) -> None:
        cfg = _cfg(
            enabled=True,
            endpoints=[
                ("ollama.base_url", "ts://gb10:11434"),
                ("backend.dsn", "ts://pg-host:5432/corpus"),
            ],
        )
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=lambda n, **k: n),
            patch("socket.create_connection", return_value=MagicMock()),
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.OK
        assert "2 ts:// endpoint(s) reachable" in result.detail


class TestNoPortEndpoint:
    def test_no_port_is_resolve_only_no_connect(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("ollama.base_url", "ts://gb10")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=lambda n, **k: n) as res,
            patch("socket.create_connection") as conn,
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.OK
        assert "resolve-only" in result.detail
        res.assert_called_once()
        # No port → never attempt a connect for that name.
        conn.assert_not_called()

    def test_no_port_with_path_still_resolve_only(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("code_enricher.remote_url", "ts://gb10/v1")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=lambda n, **k: n),
            patch("socket.create_connection") as conn,
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.OK
        assert "resolve-only" in result.detail
        conn.assert_not_called()


class TestEnabledNoEndpoints:
    def test_enabled_daemon_running_no_ts_endpoints_ok(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("backend.dsn", "postgresql://h/db")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch("socket.create_connection") as conn,
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.OK
        assert "no ts:// endpoints configured" in result.detail
        conn.assert_not_called()


class TestDistinctDedup:
    def test_duplicate_endpoints_probed_once(self) -> None:
        cfg = _cfg(
            enabled=True,
            endpoints=[
                ("ollama.base_url", "ts://gb10:11434"),
                ("embedders[0].base_url", "ts://gb10:11434"),
            ],
        )
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(f"{TS}.resolve", side_effect=lambda n, **k: n),
            patch("socket.create_connection", return_value=MagicMock()) as conn,
        ):
            result = _check_tailscale(cfg)
        assert result.status == CheckStatus.OK
        assert "1 ts:// endpoint(s) reachable" in result.detail
        conn.assert_called_once()


class TestNeverFails:
    """The single most important property: the check never blocks doctor."""

    def test_resolve_and_connect_failures_never_fail(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("ollama.base_url", "ts://gb10:11434")])
        with (
            _scanner(cfg),
            patch(f"{TS}._load_status", return_value={"BackendState": "Running"}),
            patch(
                f"{TS}.resolve",
                side_effect=TailscaleUnavailable("nope", reason="name"),
            ),
            patch("socket.create_connection", side_effect=OSError("boom")),
        ):
            result = _check_tailscale(cfg)
        assert result.status != CheckStatus.FAIL

    def test_daemon_down_never_fails(self) -> None:
        cfg = _cfg(enabled=True, endpoints=[("ollama.base_url", "ts://gb10:11434")])
        with (
            _scanner(cfg),
            patch(
                f"{TS}._load_status",
                side_effect=TailscaleUnavailable("down", reason="daemon"),
            ),
        ):
            result = _check_tailscale(cfg)
        assert result.status != CheckStatus.FAIL


class TestRegisteredInRunDoctor:
    """``run_doctor`` must include the new check in both branches."""

    def test_tailscale_appears_in_report_names(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        names = {r.name for r in report.results}
        assert "tailscale" in names

    def test_tailscale_skipped_when_config_missing(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        rows = [r for r in report.results if r.name == "tailscale"]
        assert len(rows) == 1
        assert rows[0].status is CheckStatus.SKIP
        assert "config not loaded" in rows[0].detail
