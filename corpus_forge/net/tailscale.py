"""RFC fleet-4 item 1 — Tailscale name resolution and peer discovery.

The fleet's machines reach each other over one tailnet; config wants
to *speak names* (``ts://gb10``) instead of hand-pinned 100.x IPs.
This module is the read-only foundation the later RFC slices (the
``resolve_endpoint`` config plumbing, the doctor check, the wizard
peer picker) build on:

- :func:`resolve` — MagicDNS-first name → host mapping with a
  ``tailscale status --json`` fallback.
- :func:`peers` — live tailnet peers for wizard pickers and
  ``hosts list`` online markers.
- :class:`TailscaleUnavailable` — the failure shape, distinguishing
  "the daemon layer is unusable" from "the name isn't in this
  tailnet" (different operator remediations; precedent:
  :class:`corpus_forge.mcp.lifecycle.ProcessDiscoveryUnavailable`).

Everything funnels through one shellout boundary
(:func:`_invoke_status`) patched by tests — the real binary is never
on the test critical path, mirroring ``acceleration.py``'s
``nvidia-smi`` probe and ``mcp/lifecycle.py``'s ``ps`` enumeration.

Results are cached for process lifetime: tailnet membership changes
far slower than a CLI invocation or doctor run, and the cache keeps
``resolve`` calls inside connection hot paths effectively free.
Long-lived daemons that must observe tailnet changes can call
:func:`clear_cache`.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Literal

__all__ = ["Peer", "TailscaleUnavailable", "clear_cache", "peers", "resolve"]

# ``tailscale status --json`` is local-socket IPC with tailscaled —
# normally milliseconds. 5 s (RFC-pinned) tolerates a cold daemon
# without letting a wedged one hang a connection attempt.
_STATUS_TIMEOUT_S = 5.0


class TailscaleUnavailable(RuntimeError):
    """Raised when a tailnet name cannot be turned into an address.

    Two shapes, told apart by :attr:`reason` — the operator
    remediations are different:

    - ``"daemon"`` — the Tailscale layer itself is unusable: binary
      not on PATH, ``tailscaled`` not running (``BackendState`` is
      ``Stopped`` / ``NeedsLogin`` / …), the status call timed out,
      or its output was unparseable. Remediation: install / start /
      log in to Tailscale, then re-run ``corpus-forge doctor``.
    - ``"name"`` — Tailscale is healthy but the requested name
      matches no peer in this tailnet. Remediation: check the
      spelling against ``tailscale status``.
    """

    def __init__(self, message: str, *, reason: Literal["daemon", "name"]) -> None:
        super().__init__(message)
        self.reason: Literal["daemon", "name"] = reason


@dataclass(frozen=True)
class Peer:
    """One tailnet node, normalised for pickers and list views.

    ``name`` is the short MagicDNS name — no trailing dot, no tailnet
    suffix (``gb10.tail1234.ts.net.`` → ``gb10``). ``ips`` preserves
    the CLI's order (IPv4 first in practice). The local node is
    included with ``is_self=True`` so ``hosts list`` can annotate
    every row of ``corpus.hosts``, including the machine running the
    command; pickers that only want *remote* targets filter it out.
    """

    name: str
    ips: tuple[str, ...]
    online: bool
    is_self: bool = False


# ── process-lifetime caches ──────────────────────────────────────────

# Mutated in place (never rebound) so no ``global`` is needed.
_status_cache: dict[str, dict[str, object]] = {}
_resolve_cache: dict[str, str] = {}


def clear_cache() -> None:
    """Drop the cached status document and resolved names.

    For tests and for long-lived daemons that want to re-observe the
    tailnet (e.g. after a doctor run reports a previously-offline
    peer).
    """
    _status_cache.clear()
    _resolve_cache.clear()


# ── shellout boundary ────────────────────────────────────────────────


def _invoke_status() -> subprocess.CompletedProcess[str]:
    """Run ``tailscale status --json`` — the single patchable boundary.

    Tests patch *this* function (never the real binary); the
    error-mapping and JSON/state validation in :func:`_load_status`
    stay under test.
    """
    return subprocess.run(
        ["tailscale", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=_STATUS_TIMEOUT_S,
        check=False,
    )


def _load_status() -> dict[str, object]:
    """Fetch + validate the status document (cached for process life).

    Raises :class:`TailscaleUnavailable` (``reason="daemon"``) for
    every layer-level failure: missing binary, timeout, non-zero
    exit, unparseable JSON, or a backend state other than
    ``Running``.
    """
    cached = _status_cache.get("status")
    if cached is not None:
        return cached
    try:
        completed = _invoke_status()
    except FileNotFoundError as exc:
        raise TailscaleUnavailable(
            "tailscale binary not found on PATH — is Tailscale installed? "
            "(corpus-forge doctor names this check)",
            reason="daemon",
        ) from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise TailscaleUnavailable(
            f"tailscale status did not answer within {_STATUS_TIMEOUT_S:.0f}s — "
            "is tailscaled running?",
            reason="daemon",
        ) from exc
    if completed.returncode != 0:
        raise TailscaleUnavailable(
            f"tailscale status exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
            reason="daemon",
        )
    try:
        status = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TailscaleUnavailable(
            "tailscale status --json produced unparseable output",
            reason="daemon",
        ) from exc
    state = status.get("BackendState")
    if state != "Running":
        raise TailscaleUnavailable(
            f"tailscale backend state is {state!r}, expected 'Running' — "
            "run 'tailscale up' (operator action; corpus-forge never "
            "manages the Tailscale lifecycle)",
            reason="daemon",
        )
    _status_cache["status"] = status
    return status


# ── parsing helpers ──────────────────────────────────────────────────


def _short_name(dns_name: str) -> str:
    """``gb10.tail1234.ts.net.`` → ``gb10``.

    A node's ``DNSName`` is always ``<host>.<tailnet-suffix>.`` and a
    host label cannot contain dots, so the short MagicDNS name is the
    first label — no suffix bookkeeping needed (the status document's
    ``MagicDNSSuffix`` can be absent while DNSNames still carry one).
    """
    return dns_name.rstrip(".").split(".")[0]


def _magicdns_enabled(status: dict[str, object]) -> bool:
    """True when bare MagicDNS names resolve via the OS resolver.

    The stable signal across tailscale CLI versions is a non-empty
    ``MagicDNSSuffix`` (present at top level; newer CLIs also nest it
    under ``CurrentTailnet`` alongside ``MagicDNSEnabled`` — honour
    the explicit flag when present).
    """
    tailnet = status.get("CurrentTailnet")
    if isinstance(tailnet, dict) and "MagicDNSEnabled" in tailnet:
        return bool(tailnet["MagicDNSEnabled"])
    return bool(status.get("MagicDNSSuffix"))


def _iter_nodes(status: dict[str, object]) -> list[tuple[dict[str, object], bool]]:
    """Yield ``(node_dict, is_self)`` for Self + every Peer entry."""
    nodes: list[tuple[dict[str, object], bool]] = []
    self_node = status.get("Self")
    if isinstance(self_node, dict):
        nodes.append((self_node, True))
    peer_map = status.get("Peer")
    if isinstance(peer_map, dict):
        nodes.extend((node, False) for node in peer_map.values() if isinstance(node, dict))
    return nodes


def _node_ips(node: dict[str, object]) -> tuple[str, ...]:
    raw = node.get("TailscaleIPs")
    if not isinstance(raw, list):
        return ()
    return tuple(ip for ip in raw if isinstance(ip, str))


# ── public surface ───────────────────────────────────────────────────


def peers() -> list[Peer]:
    """Every node in the tailnet (self included, flagged) as :class:`Peer`.

    Feeds the setup/join wizard's live-peer picker and ``hosts
    list``'s online markers (later RFC slices). Order: self first,
    then peers sorted by name — stable for rendering.

    Raises :class:`TailscaleUnavailable` (``reason="daemon"``) when
    the status document can't be obtained.
    """
    status = _load_status()
    result: list[Peer] = []
    for node, is_self in _iter_nodes(status):
        dns_name = node.get("DNSName")
        if not isinstance(dns_name, str) or not dns_name:
            continue
        result.append(
            Peer(
                name=_short_name(dns_name),
                ips=_node_ips(node),
                online=bool(node.get("Online")),
                is_self=is_self,
            )
        )
    return [p for p in result if p.is_self] + sorted(
        (p for p in result if not p.is_self), key=lambda p: p.name
    )


def resolve(name: str, *, prefer_magicdns: bool = True) -> str:
    """Map a bare tailnet peer name to a connectable host string.

    MagicDNS-first: when the tailnet has MagicDNS enabled (and
    ``prefer_magicdns`` is left on), a bare peer name *is* a valid
    hostname for the OS resolver, so resolution is a no-op rename —
    ``resolve("gb10") == "gb10"`` — and an unknown name surfaces at
    connect time as an ordinary DNS failure.

    Fallback (MagicDNS off, or ``prefer_magicdns=False``): look the
    name up in ``tailscale status --json`` and return the peer's
    tailnet IP (first IPv4; the CLI lists IPv4 before IPv6).

    Results are cached for process lifetime (see module docstring);
    ``name`` matching is case-insensitive on the short MagicDNS name.

    Raises :class:`TailscaleUnavailable` — ``reason="daemon"`` when
    the status document can't be obtained, ``reason="name"`` when the
    fallback finds no such peer.
    """
    cache_key = f"{name.lower()}|magicdns={prefer_magicdns}"
    cached = _resolve_cache.get(cache_key)
    if cached is not None:
        return cached
    status = _load_status()
    if prefer_magicdns and _magicdns_enabled(status):
        _resolve_cache[cache_key] = name
        return name
    wanted = name.lower()
    for node, _is_self in _iter_nodes(status):
        dns_name = node.get("DNSName")
        if not isinstance(dns_name, str):
            continue
        if _short_name(dns_name).lower() != wanted:
            continue
        ips = _node_ips(node)
        if not ips:
            continue
        # Prefer IPv4 (100.64.0.0/10); fall back to the first address.
        resolved = next((ip for ip in ips if "." in ip), ips[0])
        _resolve_cache[cache_key] = resolved
        return resolved
    raise TailscaleUnavailable(
        f"no peer named {name!r} in this tailnet — check the spelling against 'tailscale status'",
        reason="name",
    )
