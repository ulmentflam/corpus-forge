"""RFC fleet-4 items 2-3 — lazy ``ts://`` endpoint resolution.

corpus-forge accepts ``ts://<magicdns-name>[:port][/rest]`` anywhere a
host URL or DSN appears (``backend.dsn``, ``EmbedderConfig.base_url``,
``ollama.base_url``, ``classifier.llm_url``, enricher URLs, VLM / Whisper
endpoints). :func:`resolve_endpoint` is the single helper applied *at the
point each URL/DSN is consumed* — config itself stays inert, resolution
is lazy, and errors surface where the connection is actually attempted
with full context (RFC "Approach": resolution is not done at parse time).

ts:// contract
==============

``resolve_endpoint`` rewrites ONLY the *host name* of a ``ts://`` value
and leaves the rest of the string byte-for-byte intact. The name (the
authority's host label) is mapped through
:func:`corpus_forge.net.tailscale.resolve`; the optional ``:port`` and
``/rest`` tail are carried over verbatim. Because the original consumer's
expected scheme is unknown to this helper, the caller passes
``default_scheme`` — the resolved value is re-assembled as
``{default_scheme}://{resolved_host}[:port][/rest]``.

Concretely, with MagicDNS on (``resolve("gb10") == "gb10"``):

==========================  =================  ==============================
input                       default_scheme     output
==========================  =================  ==============================
``ts://gb10``               ``"http"``         ``http://gb10``
``ts://gb10:11434``         ``"http"``         ``http://gb10:11434``
``ts://gb10:5432/corpus``   ``"postgresql"``   ``postgresql://gb10:5432/corpus``
``ts://gb10/v1``            ``"https"``        ``https://gb10/v1``
``http://gb10:11434``       *(any)*            ``http://gb10:11434`` (unchanged)
``postgresql://h/db``       *(any)*            ``postgresql://h/db`` (unchanged)
``dbname=corpus host=h``    *(any)*            ``dbname=corpus host=h`` (unchanged)
==========================  =================  ==============================

With MagicDNS off (``prefer_magicdns=False`` or the tailnet has MagicDNS
disabled), the name resolves to the peer's tailnet IP instead, e.g.
``ts://gb10:5432/corpus`` → ``postgresql://100.124.253.81:5432/corpus``.

Non-``ts://`` values are returned **unchanged with zero side effects** —
no Tailscale import is triggered (the import lives strictly inside the
``ts://`` branch), honouring the RFC's "no hard dependency" bar: a host
without Tailscale installed runs every plain-URL / IP path exactly as
before.

A ``ts://`` value while Tailscale is disabled raises
:class:`EndpointResolutionError` — though in practice the load-time
:meth:`corpus_forge.config.Config._check_tailscale_endpoints` validator
catches a statically-configured ``ts://`` first; this runtime guard
covers dynamically-built endpoints. A name that can't be resolved
re-raises :class:`corpus_forge.net.tailscale.TailscaleUnavailable` (its
message already names the remediation) with a pointer to the doctor
check appended.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config

__all__ = ["EndpointResolutionError", "resolve_endpoint", "resolve_endpoint_for"]

# Same shape as ``config._TS_ENDPOINT_RE`` (kept separate so this module
# imports nothing from config). ``name`` is the host label run, ``port``
# the optional ``:NNNN``, ``rest`` the optional ``/...`` tail.
_TS_RE = re.compile(
    r"^ts://(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?P<port>:\d+)?(?P<rest>/.*)?$"
)


class EndpointResolutionError(ValueError):
    """A ``ts://`` endpoint was used while Tailscale resolution is off.

    Distinct from :class:`corpus_forge.net.tailscale.TailscaleUnavailable`
    (which means Tailscale *is* enabled but the daemon / name lookup
    failed): this is the "you asked for ts:// but never turned it on"
    case. A ``ValueError`` subclass so existing config-error handling
    paths catch it uniformly.
    """


def resolve_endpoint(
    value: str,
    *,
    tailscale_enabled: bool,
    prefer_magicdns: bool = True,
    default_scheme: str = "http",
) -> str:
    """Resolve a ``ts://`` endpoint to a connectable URL; pass others through.

    Args:
        value: The raw config value — an http(s) URL, a DSN, a plain
            host, or a ``ts://<name>[:port][/rest]`` endpoint.
        tailscale_enabled: ``config.tailscale.enabled``. When ``False`` a
            ``ts://`` value raises :class:`EndpointResolutionError`; a
            non-``ts://`` value is still returned unchanged (so callers
            can pass this flag unconditionally).
        prefer_magicdns: ``config.tailscale.prefer_magicdns`` — forwarded
            to :func:`corpus_forge.net.tailscale.resolve`.
        default_scheme: scheme to re-assemble the resolved ``ts://`` host
            under (``"http"`` for OpenAI-compatible model endpoints,
            ``"postgresql"`` for ``backend.dsn``, etc.). Ignored for
            non-``ts://`` values.

    Returns:
        For a ``ts://`` value: ``{default_scheme}://{resolved}[:port][/rest]``.
        For anything else: ``value`` unchanged.

    Raises:
        EndpointResolutionError: ``value`` is ``ts://`` but
            ``tailscale_enabled`` is ``False``.
        corpus_forge.net.tailscale.TailscaleUnavailable: the daemon is
            unusable or the name isn't in the tailnet (message names the
            remediation; ``corpus-forge doctor``'s tailscale check is
            appended as a pointer).
    """
    # The no-Tailscale hard bar: a non-ts:// value never imports the
    # tailscale module and is returned byte-identical.
    if not value.startswith("ts://"):
        return value

    if not tailscale_enabled:
        raise EndpointResolutionError(
            f"ts:// endpoint {value!r} requires [tailscale] enabled = true in "
            "config.toml (corpus-forge never manages the Tailscale lifecycle — "
            "it only resolves ts:// names; see `corpus-forge doctor`'s tailscale "
            "check)."
        )

    match = _TS_RE.match(value)
    if match is None:
        raise EndpointResolutionError(
            f"{value!r} is not a valid ts:// endpoint — expected "
            "ts://<magicdns-name>[:port][/path] (e.g. 'ts://gb10', "
            "'ts://gb10:11434', 'ts://gb10:5432/corpus')."
        )

    # Lazy import: only a real ts:// value pays the tailscale import cost.
    # This is load-bearing — the no-Tailscale hard bar requires that a
    # non-ts:// value never triggers this import (PLC0415 is intentional).
    from corpus_forge.net.tailscale import TailscaleUnavailable, resolve  # noqa: PLC0415

    name = match.group("name")
    port = match.group("port") or ""
    rest = match.group("rest") or ""
    try:
        resolved = resolve(name, prefer_magicdns=prefer_magicdns)
    except TailscaleUnavailable as exc:
        raise TailscaleUnavailable(
            f"{exc} — see `corpus-forge doctor`'s tailscale check",
            reason=exc.reason,
        ) from exc
    return f"{default_scheme}://{resolved}{port}{rest}"


def resolve_endpoint_for(value: str, config: Config, *, default_scheme: str = "http") -> str:
    """:func:`resolve_endpoint` with the tailscale flags read off ``config``.

    Convenience for the consumption points that already hold a full
    :class:`corpus_forge.config.Config` (backend factory, model-client
    registries, the ``ollama`` admin helper). Pulls
    ``config.tailscale.enabled`` and ``config.tailscale.prefer_magicdns``
    so each call site stays a single line:

        resolve_endpoint_for(str(cfg.base_url), config, default_scheme="http")

    A duck-typed config without a ``tailscale`` attribute (legacy
    call-sites, unit-test stubs) is treated as Tailscale-disabled — which
    is a no-op for the common non-``ts://`` value and a clear
    :class:`EndpointResolutionError` for a ``ts://`` one, matching what a
    real default-constructed :class:`~corpus_forge.config.Config` would do.
    """
    tailscale = getattr(config, "tailscale", None)
    return resolve_endpoint(
        value,
        tailscale_enabled=getattr(tailscale, "enabled", False),
        prefer_magicdns=getattr(tailscale, "prefer_magicdns", True),
        default_scheme=default_scheme,
    )
