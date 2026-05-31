"""Extension-based chunk routing for multi-embedder setups (PR #81).

Background. Dual-tower (or N-tower) setups want each chunk to land in
exactly one embedder's table — e.g. code chunks go to ``nomic-embed-code``
and everything else goes to ``nomic-embed-text-v1.5``.  We compute the
route deterministically from each embedder's ``extensions`` allow-list:

1. Iterate active embedders in **declaration order**.
2. The first *specialist* (non-empty ``extensions``) whose allow-list
   matches the chunk's URI claims it (case-insensitive ``endswith``).
3. Otherwise the first *catchall* (empty ``extensions``) claims it.
4. If neither matches → ``route_for`` returns ``None``.  The config-load
   gate :func:`validate_routing_invariant` catches the most common
   misconfiguration (specialists with no catchall) before this state is
   reachable in production.

This module is generic — it doesn't bind to any specific embedder
provider.  It duck-types on ``embedder.extensions`` so it works with
both the runtime :class:`~corpus_forge.embedders.base.BaseEmbedder`
instances and the load-time :class:`~corpus_forge.config.EmbedderConfig`
models.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class EmbedderRoutingError(ValueError):
    """Raised at config-load time when the routing invariant is violated.

    Subclasses :class:`ValueError` so Pydantic's
    ``@model_validator(mode="after")`` wrapping picks it up as a clean
    validation failure rather than an unrelated ``RuntimeError``.
    """


def extension_for(source_uri: str) -> str:
    """Return the lowercase single-suffix extension of ``source_uri``.

    Examples:

    - ``"filesystem://vault/foo/bar.py"`` → ``".py"``
    - ``"FILE.PY"`` → ``".py"``
    - ``"foo.tar.gz"`` → ``".gz"`` (single-suffix only; multi-suffix
      matching is done via :func:`claims` with an ``endswith`` check)
    - ``"README"`` → ``""``
    - ``".envrc"`` → ``""`` (leading-dot dotfile, not a suffix)
    - ``""`` → ``""``

    The single-suffix form is a convenience for callers that have an
    extension in hand already (e.g. for logging).  Suffix matching
    against allow-lists is performed by :func:`claims`, which uses
    ``endswith`` so multi-dot allow-list entries like ``".tar.gz"``
    keep working.
    """
    if not source_uri:
        return ""
    # Strip query/fragment? Not needed — source_uri is an internal URI
    # built by the source layer (``filesystem://``, ``zotero://``, etc.)
    # and has no query string.  Path separator detection works on both
    # POSIX (``/``) and Windows (``\``) URIs.
    last_slash = max(source_uri.rfind("/"), source_uri.rfind("\\"))
    basename = source_uri[last_slash + 1 :] if last_slash >= 0 else source_uri
    last_dot = basename.rfind(".")
    # No dot at all OR leading-dot dotfile (``.envrc``) → no suffix.
    if last_dot <= 0:
        return ""
    return basename[last_dot:].lower()


def claims(embedder: Any, source_uri: str) -> bool:
    """Return True iff ``embedder``'s allow-list matches ``source_uri``.

    - Catchall embedder (empty ``extensions``) → always True.
    - Specialist embedder → True iff ``source_uri.lower()`` ends with
      one of the allow-list entries (case-insensitive).

    Note: this answers "does this embedder *want* the chunk?" in
    isolation.  When multiple embedders all answer True, the global
    :func:`route_for` decides who actually gets it (specialists beat
    catchalls; ties broken by declaration order).
    """
    extensions: Sequence[str] = getattr(embedder, "extensions", []) or []
    if not extensions:
        return True  # catchall
    lower = source_uri.lower()
    return any(lower.endswith(ext) for ext in extensions)


def route_for(extension_or_uri: str, active_embedders: Sequence[Any]) -> Any | None:
    """Pick the single embedder that owns ``extension_or_uri``.

    ``extension_or_uri`` accepts either a bare extension (``".py"``) or
    a full URI (``"filesystem://vault/foo.py"``); either is handled.

    Resolution rule (in order):

    1. First specialist whose ``extensions`` contains a matching
       suffix wins.
    2. Else the first catchall (empty ``extensions``) wins.
    3. Else ``None`` (no embedder claims this chunk).

    Declaration order is preserved end-to-end — when two specialists
    both match, the one earlier in ``active_embedders`` wins.
    """
    # Normalise to a URI-shaped string for ``endswith`` matching.
    # A bare extension like ``".py"`` is itself a valid ``endswith`` key
    # (``".py".endswith(".py")`` is True), so we can pass it through.
    target = extension_or_uri.lower()

    catchall: Any | None = None
    for embedder in active_embedders:
        exts: Sequence[str] = getattr(embedder, "extensions", []) or []
        if not exts:
            if catchall is None:
                catchall = embedder
            continue
        if any(target.endswith(ext) for ext in exts):
            return embedder
    return catchall


def validate_routing_invariant(embedder_configs: Sequence[Any]) -> None:
    """Raise :class:`EmbedderRoutingError` when no catchall covers the
    active specialists.

    Called by the ``Config`` model validator at config-load time so
    misconfigurations surface immediately instead of waiting until the
    first ``corpus-forge embed`` run.

    Rule: when at least one *active* embedder is a specialist
    (non-empty ``extensions``), there must also be at least one
    *active* embedder that is a catchall (empty ``extensions``).
    Inactive embedders are ignored — they're not in play, so the rule
    doesn't have to cover the chunks their allow-list would have
    claimed.
    """
    active = [e for e in embedder_configs if getattr(e, "active", True)]
    has_specialist = any(getattr(e, "extensions", []) for e in active)
    has_catchall = any(not getattr(e, "extensions", []) for e in active)
    if has_specialist and not has_catchall:
        specialist_names = [getattr(e, "name", "?") for e in active if getattr(e, "extensions", [])]
        raise EmbedderRoutingError(
            "Embedder routing: at least one specialist is active "
            f"({specialist_names!r}) but no catchall embedder (empty "
            "`extensions`) is configured. Either add a catchall "
            "[[embedders]] entry with active = true and no `extensions` "
            "field, or set `extensions` on every chunk that the specialists "
            "wouldn't otherwise claim."
        )
