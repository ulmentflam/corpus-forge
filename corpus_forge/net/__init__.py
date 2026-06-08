"""RFC fleet-4 — tailnet-aware networking helpers.

Read-only Tailscale integration: name resolution and peer discovery
via the ``tailscale`` CLI. corpus-forge never manages the Tailscale
lifecycle (no ``up``/``down``, no keys, no ACLs) — see the RFC's
non-goals.
"""

from corpus_forge.net.endpoint import (
    EndpointResolutionError,
    resolve_endpoint,
    resolve_endpoint_for,
)
from corpus_forge.net.tailscale import Peer, TailscaleUnavailable, peers, resolve

__all__ = [
    "EndpointResolutionError",
    "Peer",
    "TailscaleUnavailable",
    "peers",
    "resolve",
    "resolve_endpoint",
    "resolve_endpoint_for",
]
