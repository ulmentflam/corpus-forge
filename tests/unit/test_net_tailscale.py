"""RFC fleet-4 item 1 — ``corpus_forge.net.tailscale`` unit tests.

All shellouts are patched at the module's single boundary
(``_invoke_status``) — the real ``tailscale`` binary is never on the
test critical path (house pattern: ``test_mcp_restart_and_doctor.py``,
``acceleration.py``'s probe tests). The two binary-level failures
(missing executable, hang) patch ``subprocess.run`` inside the module
instead, so ``_invoke_status``'s own arguments stay under test.
"""

from __future__ import annotations

import json
import subprocess
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest

from corpus_forge.net import tailscale
from corpus_forge.net.tailscale import Peer, TailscaleUnavailable, peers, resolve

if TYPE_CHECKING:
    from collections.abc import Iterator

# ─── fixtures ───────────────────────────────────────────────────────────

# Mirrors the real `tailscale status --json` schema (trailing-dot
# DNSNames, dual-stack TailscaleIPs, Peer map keyed by node key).
_SUFFIX = "tail1234.ts.net"


def make_status(
    *,
    backend_state: str = "Running",
    magicdns_suffix: str = _SUFFIX,
    magicdns_enabled: bool | None = None,
) -> dict[str, object]:
    status: dict[str, object] = {
        "BackendState": backend_state,
        "MagicDNSSuffix": magicdns_suffix,
        "Self": {
            "DNSName": f"workstation.{_SUFFIX}.",
            "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
            "Online": True,
        },
        "Peer": {
            "nodekey:aaa": {
                "DNSName": f"gb10.{_SUFFIX}.",
                "TailscaleIPs": ["100.124.253.81", "fd7a:115c:a1e0::2"],
                "Online": True,
            },
            "nodekey:bbb": {
                "DNSName": f"macbook.{_SUFFIX}.",
                "TailscaleIPs": ["100.99.98.97"],
                "Online": False,
            },
            "nodekey:ccc": {
                "DNSName": f"v6only.{_SUFFIX}.",
                "TailscaleIPs": ["fd7a:115c:a1e0::3"],
                "Online": True,
            },
        },
    }
    if magicdns_enabled is not None:
        status["CurrentTailnet"] = {"MagicDNSEnabled": magicdns_enabled}
    return status


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(
        args=["tailscale", "status", "--json"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    """Process-lifetime caches must not leak between tests."""
    tailscale.clear_cache()
    yield
    tailscale.clear_cache()


@pytest.fixture
def status_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Patch the shellout boundary; returns the call log.

    Tests mutate ``status_calls[0]``'s ``"response"`` to control the
    JSON; every invocation appends to the log so cache behavior is
    observable.
    """
    calls: list[dict[str, object]] = [{"response": make_status()}]

    def fake_invoke() -> CompletedProcess[str]:
        calls.append({"invoked": True})
        return completed(json.dumps(calls[0]["response"]))

    monkeypatch.setattr(tailscale, "_invoke_status", fake_invoke)
    return calls


def _shellout_count(calls: list[dict[str, object]]) -> int:
    return sum(1 for c in calls if c.get("invoked"))


# ─── resolve: MagicDNS no-op path ───────────────────────────────────────


class TestResolveMagicDNS:
    def test_magicdns_makes_resolution_a_noop_rename(
        self, status_calls: list[dict[str, object]]
    ) -> None:
        assert resolve("gb10") == "gb10"

    def test_explicit_magicdns_enabled_flag_wins(
        self, status_calls: list[dict[str, object]]
    ) -> None:
        # Newer CLIs nest an explicit flag; suffix present but flag
        # false → fall back to IP resolution.
        status_calls[0]["response"] = make_status(magicdns_enabled=False)
        assert resolve("gb10") == "100.124.253.81"

    def test_prefer_magicdns_false_forces_ip(self, status_calls: list[dict[str, object]]) -> None:
        assert resolve("gb10", prefer_magicdns=False) == "100.124.253.81"


# ─── resolve: status-JSON fallback ──────────────────────────────────────


class TestResolveFallback:
    def test_resolves_peer_to_ipv4(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        assert resolve("gb10") == "100.124.253.81"

    def test_prefers_ipv4_over_ipv6(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        # gb10 lists IPv4 first AND IPv6 — IPv4 wins regardless of order.
        assert "." in resolve("gb10")

    def test_ipv6_only_peer_falls_back_to_first_ip(
        self, status_calls: list[dict[str, object]]
    ) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        assert resolve("v6only") == "fd7a:115c:a1e0::3"

    def test_matching_is_case_insensitive(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        assert resolve("GB10") == "100.124.253.81"

    def test_self_resolves_too(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        assert resolve("workstation") == "100.101.102.103"

    def test_unknown_name_raises_name_shape(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        with pytest.raises(TailscaleUnavailable) as excinfo:
            resolve("no-such-host")
        assert excinfo.value.reason == "name"
        assert "no-such-host" in str(excinfo.value)


# ─── resolve: cache ─────────────────────────────────────────────────────


class TestCache:
    def test_second_resolve_does_not_shell_out(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        resolve("gb10")
        first = _shellout_count(status_calls)
        resolve("gb10")
        assert _shellout_count(status_calls) == first

    def test_status_document_is_shared_across_names(
        self, status_calls: list[dict[str, object]]
    ) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        resolve("gb10")
        resolve("macbook")
        assert _shellout_count(status_calls) == 1

    def test_clear_cache_forces_fresh_status(self, status_calls: list[dict[str, object]]) -> None:
        status_calls[0]["response"] = make_status(magicdns_suffix="")
        resolve("gb10")
        tailscale.clear_cache()
        resolve("gb10")
        assert _shellout_count(status_calls) == 2


# ─── daemon-shape failures ──────────────────────────────────────────────


class TestDaemonUnavailable:
    def _assert_daemon(self, excinfo: pytest.ExceptionInfo[TailscaleUnavailable]) -> None:
        assert excinfo.value.reason == "daemon"

    def test_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_fnf(*args: object, **kwargs: object) -> CompletedProcess[str]:
            raise FileNotFoundError("tailscale")

        monkeypatch.setattr(tailscale.subprocess, "run", raise_fnf)
        with pytest.raises(TailscaleUnavailable) as excinfo:
            resolve("gb10")
        self._assert_daemon(excinfo)
        assert "not found" in str(excinfo.value)

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_timeout(*args: object, **kwargs: object) -> CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="tailscale", timeout=5.0)

        monkeypatch.setattr(tailscale.subprocess, "run", raise_timeout)
        with pytest.raises(TailscaleUnavailable) as excinfo:
            peers()
        self._assert_daemon(excinfo)

    def test_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            tailscale,
            "_invoke_status",
            lambda: completed("", returncode=1, stderr="Logged out."),
        )
        with pytest.raises(TailscaleUnavailable) as excinfo:
            resolve("gb10")
        self._assert_daemon(excinfo)
        assert "Logged out." in str(excinfo.value)

    def test_unparseable_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tailscale, "_invoke_status", lambda: completed("not json{"))
        with pytest.raises(TailscaleUnavailable) as excinfo:
            resolve("gb10")
        self._assert_daemon(excinfo)

    @pytest.mark.parametrize("state", ["Stopped", "NeedsLogin", "Starting", None])
    def test_backend_not_running(self, monkeypatch: pytest.MonkeyPatch, state: str | None) -> None:
        status = make_status()
        if state is None:
            del status["BackendState"]
        else:
            status["BackendState"] = state
        monkeypatch.setattr(tailscale, "_invoke_status", lambda: completed(json.dumps(status)))
        with pytest.raises(TailscaleUnavailable) as excinfo:
            peers()
        self._assert_daemon(excinfo)

    def test_failures_are_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A daemon failure must not poison the cache — the operator
        starts tailscaled and the next call should see it."""
        responses: list[CompletedProcess[str]] = [
            completed("", returncode=1, stderr="down"),
            completed(json.dumps(make_status(magicdns_suffix=""))),
        ]
        monkeypatch.setattr(tailscale, "_invoke_status", lambda: responses.pop(0))
        with pytest.raises(TailscaleUnavailable):
            resolve("gb10")
        assert resolve("gb10") == "100.124.253.81"


# ─── peers() ────────────────────────────────────────────────────────────


class TestPeers:
    def test_parses_all_nodes_with_normalised_names(
        self, status_calls: list[dict[str, object]]
    ) -> None:
        result = peers()
        names = [p.name for p in result]
        # Self first, then peers sorted by name; no trailing dots, no
        # tailnet suffix.
        assert names == ["workstation", "gb10", "macbook", "v6only"]

    def test_online_flags_and_self_marker(self, status_calls: list[dict[str, object]]) -> None:
        by_name = {p.name: p for p in peers()}
        assert by_name["workstation"].is_self is True
        assert by_name["gb10"].online is True
        assert by_name["gb10"].is_self is False
        assert by_name["macbook"].online is False

    def test_ips_preserved_in_cli_order(self, status_calls: list[dict[str, object]]) -> None:
        by_name = {p.name: p for p in peers()}
        assert by_name["gb10"].ips == ("100.124.253.81", "fd7a:115c:a1e0::2")

    def test_nodes_without_dnsname_are_skipped(self, status_calls: list[dict[str, object]]) -> None:
        response = make_status()
        peer_map = response["Peer"]
        assert isinstance(peer_map, dict)
        peer_map["nodekey:broken"] = {"TailscaleIPs": ["100.1.2.3"], "Online": True}
        status_calls[0]["response"] = response
        assert "100.1.2.3" not in {ip for p in peers() for ip in p.ips}


# ─── package surface ────────────────────────────────────────────────────


class TestPackageSurface:
    def test_reexports(self) -> None:
        from corpus_forge import net

        assert net.resolve is resolve
        assert net.peers is peers
        assert net.TailscaleUnavailable is TailscaleUnavailable
        assert net.Peer is Peer
