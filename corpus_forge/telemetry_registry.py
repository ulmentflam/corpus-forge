"""Fleet telemetry heartbeat — host + model registry upserts (rfc-fleet-1).

This module is the failure-isolated glue between a loaded
:class:`~corpus_forge.config.Config`, the host's hardware probe, and the
``hosts`` / ``models`` registry tables created by alembic revision
0018.  It is called **once per process start** — daemon startup and the
top of an ``embed`` backfill — never on a hot path.

Failure isolation is the contract: a telemetry write must never break
the caller.  Every public function here wraps its body in a broad
``except Exception`` that logs a warning (host/model upsert) or a debug
line (the inherently flaky ``ollama list`` probe) and returns cleanly.
A briefly-unreachable backend at embed-entry time degrades to "no
heartbeat this run", not a failed embed.

What gets registered:

* **Host** — ``host_id`` from :meth:`Config.host_id`, ``hostname`` via
  :func:`socket.gethostname`, ``os`` via :func:`platform.platform`, and
  the :func:`corpus_forge.acceleration.detect_accelerator` probe output
  serialised to a JSON-able dict.
* **Models** — one row per configured embedder
  (``model_key = "<provider>:<model_id>"``, ``kind="embedder"``,
  ``dimension`` from the embedder config) plus a best-effort
  ``ollama list`` enumeration (``kind`` inferred from the model family,
  ``dimension`` left ``None``).  ``ON CONFLICT DO NOTHING`` keeps each
  model's original ``first_seen``.
"""

from __future__ import annotations

import logging
import platform
import socket
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from corpus_forge.acceleration import detect_accelerator
from corpus_forge.admin import ollama

if TYPE_CHECKING:
    from corpus_forge.config import Config

logger = logging.getLogger(__name__)

# Short timeout for the best-effort ``ollama list`` probe — CI and most
# hosts have no Ollama daemon, so we must not block the embed entry.
_OLLAMA_PROBE_TIMEOUT_S: float = 2.0

# Substrings in an Ollama model family/name that mark it as an embedder
# rather than a generative LLM.  Used to fill ``models.kind`` for the
# ``ollama list`` rows; everything else defaults to ``"llm"`` (v1 does
# not distinguish vlm/whisper for Ollama-sourced rows — that's reserved
# for a later fleet task).
_EMBEDDER_FAMILY_MARKERS: tuple[str, ...] = ("embed", "bert", "bge", "nomic", "minilm")


def accelerator_payload() -> dict[str, Any]:
    """Return the accelerator probe output as a JSON-serialisable dict.

    :class:`~corpus_forge.acceleration.AcceleratorInfo` is a frozen
    dataclass whose ``kind`` is a :class:`~enum.StrEnum`; ``asdict``
    plus an explicit ``str(kind)`` yields a dict that ``json.dumps``
    accepts.  ``device_name`` / ``vram_mb`` stay ``None`` for the
    MPS / CPU lanes (the probe only populates them for CUDA).
    """
    info = detect_accelerator()
    payload = asdict(info)
    payload["kind"] = str(info.kind)
    return payload


def _embedder_model_rows(config: Config) -> list[dict]:
    """Build ``models`` rows for every configured embedder."""
    rows: list[dict] = []
    for ec in config.embedders:
        rows.append(
            {
                "model_key": f"{ec.provider}:{ec.model_id}",
                "kind": "embedder",
                "provider": ec.provider,
                "model_id": ec.model_id,
                "dimension": ec.dimension,
            }
        )
    return rows


def _infer_ollama_kind(*signals: str) -> str:
    """Infer a ``models.kind`` from an Ollama model's name/family strings.

    Returns ``"embedder"`` when any signal contains an embedder marker
    (``embed`` / ``bert`` / ``bge`` / ``nomic`` / ``minilm``), else the
    ``"llm"`` default.
    """
    haystack = " ".join(s.lower() for s in signals if s)
    if any(marker in haystack for marker in _EMBEDDER_FAMILY_MARKERS):
        return "embedder"
    return "llm"


def _ollama_model_rows() -> list[dict]:
    """Best-effort ``ollama list`` enumeration → ``models`` rows.

    ANY failure (no daemon, network error, malformed payload) is
    swallowed with a debug log and yields an empty list — the heartbeat
    must never depend on Ollama being up.  ``provider`` is fixed to
    ``"ollama"``; ``model_id`` is the tag; ``dimension`` is left ``None``
    (Ollama's ``/api/tags`` does not expose embedding width).
    """
    try:
        tags = ollama.fetch_tags(timeout=_OLLAMA_PROBE_TIMEOUT_S)
    except Exception as exc:
        logger.debug("telemetry: ollama list probe skipped: %r", exc)
        return []

    rows: list[dict] = []
    for model in tags:
        rows.append(
            {
                "model_key": f"ollama:{model.name}",
                "kind": _infer_ollama_kind(model.name, model.family),
                "provider": "ollama",
                "model_id": model.name,
                "dimension": None,
            }
        )
    return rows


def record_host_heartbeat(backend: Any, config: Config) -> None:
    """Upsert this host's row in the ``hosts`` registry (failure-isolated).

    Any exception — unreachable backend, probe failure, missing
    ``host_id`` file — is logged at WARNING and swallowed so the caller
    (daemon / embed) is never broken by a telemetry write.
    """
    try:
        backend.upsert_host(
            host_id=config.host_id(),
            hostname=socket.gethostname(),
            os=platform.platform(),
            accelerator=accelerator_payload(),
        )
        logger.debug("telemetry: host heartbeat recorded")
    except Exception as exc:
        logger.warning("telemetry: host heartbeat failed (continuing): %r", exc)


def record_model_registry(backend: Any, config: Config) -> None:
    """Upsert configured-embedder + ``ollama list`` rows into ``models``.

    Failure-isolated: the embedder rows and the (independently
    best-effort) Ollama rows are gathered, then a single
    :meth:`backend.upsert_models` call writes them.  Any exception is
    logged at WARNING and swallowed.
    """
    try:
        rows = _embedder_model_rows(config)
        rows.extend(_ollama_model_rows())
        backend.upsert_models(rows)
        logger.debug("telemetry: registered %d model row(s)", len(rows))
    except Exception as exc:
        logger.warning("telemetry: model registry upsert failed (continuing): %r", exc)


def heartbeat(backend: Any, config: Config) -> None:
    """Record the host heartbeat then the model registry (failure-isolated).

    The single entry point wired into daemon startup and the ``embed``
    backfill entry.  Host first (so ``model_benchmarks`` FKs always have
    a host row), models second.  Both legs isolate their own failures;
    a ``None`` backend is tolerated as a no-op so callers don't have to
    guard the unreachable-backend case themselves.
    """
    if backend is None:
        logger.debug("telemetry: no backend handle — skipping heartbeat")
        return
    record_host_heartbeat(backend, config)
    record_model_registry(backend, config)
