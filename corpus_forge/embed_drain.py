"""The daemon's continuous embed-drain loop (RFC fleet-5, item 1).

A machine joined to a fleet purely to *drain an existing backlog* (the
canonical reason to add a GPU box) ingests nothing, so today's
ingest-time embedding never fires for it and the backlog just sits there.
This module is the missing wiring: a long-lived caller of fleet-2's
claim/release primitives that, per lane, claims a batch of pending
chunks, embeds them, writes the vectors, releases the claims, and records
passive ``source="embed-run"`` telemetry.

The loop is deliberately *not* a scheduler and adds no DB schema — it is
fleet-2's greedy claim loop hosted inside a supervised process:

* **Greedy while there's work.** Any non-empty sweep immediately sweeps
  again — drain as fast as the backlog allows.
* **Bounded backoff when idle.** When *every* lane returns an empty batch
  (backlog drained, or every remaining chunk live-claimed by peers) the
  loop sleeps with bounded exponential delay (``idle_min``..``idle_max``,
  default 5 s → 5 min) instead of hot-spinning; the next non-empty sweep
  resets the delay to ``idle_min``.
* **Crash-safe, no double work.** Claims are released after a successful
  write. On encode/write failure the claims are released so a peer
  retries. A crash *between* write and release is harmless: the chunk now
  has an embedding row, so it drops out of the pending set and
  :meth:`claim_chunks_for_embedding` never returns it again — fleet-2's
  ``FOR UPDATE SKIP LOCKED`` + lease expiry guarantee zero duplicate
  compute across hosts regardless.

Lifecycle wiring into the daemon supervisor and the ``[service]``
config knobs (``embed_drain`` / ``ingest_watch``) are fleet-5 item 2;
this module is the isolated, unit-tested loop mechanism that item 2
runs on a thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from corpus_forge.backends.base import FederationUnsupported

logger = logging.getLogger(__name__)

#: Claim batch size per lane per sweep — matches the fleet-2 claim default.
_DEFAULT_BATCH: int = 1024
#: Backoff floor / ceiling (seconds) when every lane comes back empty.
_DEFAULT_IDLE_MIN: float = 5.0
_DEFAULT_IDLE_MAX: float = 300.0


@dataclass
class _Lane:
    """One drain target: an embedder config, its warmed instance, and its row id."""

    embedder_config: Any
    embedder: Any
    embedder_id: int


class EmbedDrainLoop:
    """Continuous, claim-based embed-backlog drain for the managed daemon.

    Construct with a backend + config; call :meth:`run` with a
    ``threading.Event`` to drive the loop until the event is set (the
    daemon's shutdown signal). :meth:`drain_once` and
    :meth:`drain_lane_once` are the testable units beneath it.
    """

    def __init__(
        self,
        backend: Any,
        config: Any,
        *,
        host_id: str | None = None,
        batch: int | None = None,
        lease_ttl: int | None = None,
        idle_min: float = _DEFAULT_IDLE_MIN,
        idle_max: float = _DEFAULT_IDLE_MAX,
    ) -> None:
        if idle_min <= 0:
            raise ValueError("idle_min must be > 0")
        if idle_max < idle_min:
            raise ValueError("idle_max must be >= idle_min")
        self._backend = backend
        self._config = config
        self._host_id = host_id if host_id is not None else config.host_id()
        embed_cfg = getattr(config, "embed", None)
        # Claim batch size: explicit arg wins, else ``[embed]
        # claim_batch_size`` (issue #125), else the historical default.
        resolved_batch = (
            batch
            if batch is not None
            else int(getattr(embed_cfg, "claim_batch_size", _DEFAULT_BATCH))
        )
        if resolved_batch <= 0:
            raise ValueError("batch must be > 0")
        self._batch = resolved_batch
        self._lease_ttl = (
            lease_ttl if lease_ttl is not None else int(getattr(embed_cfg, "claim_lease_ttl", 600))
        )
        # Forward-guard cap on concurrent in-flight claim/embed/release
        # cycles (issue #125). The loop is single-threaded today, so this is
        # 1 by construction; pinned here so multi-threaded drain can't
        # silently multiply per-host DB load past the configured bound.
        self._max_inflight = max(1, int(getattr(embed_cfg, "max_inflight_batches", 1)))
        # Postgres-saturation backpressure threshold (issue #125): back off
        # claiming when server connections reach this fraction of
        # max_connections. 0.0 disables. Clamped to [0, 1].
        self._backpressure_max_load = min(
            1.0, max(0.0, float(getattr(embed_cfg, "backpressure_max_load", 0.9)))
        )
        self._idle_min = float(idle_min)
        self._idle_max = float(idle_max)
        self._lanes: list[_Lane] | None = None

    # ── Lane resolution ────────────────────────────────────────────────────

    def resolve_lanes(self) -> list[_Lane]:
        """Register + warm the embedders this host drains, filtered by ``[embed] lanes``.

        Mirrors the backfill path: every *active* configured embedder,
        intersected with the host's ``[embed] lanes`` (empty lanes → all
        active, the backcompat bar). Each kept embedder is registered,
        warmed once, and resolved to its backend row id for the claim
        calls. Cached on the instance so :meth:`run` resolves once.
        """

        from corpus_forge.embed import filter_embedders_by_lanes  # noqa: PLC0415
        from corpus_forge.embedders.registry import (  # noqa: PLC0415
            register_from_config,
            registry,
        )

        active = [
            ec for ec in getattr(self._config, "embedders", []) if getattr(ec, "active", True)
        ]
        embed_cfg = getattr(self._config, "embed", None)
        lanes_cfg = list(getattr(embed_cfg, "lanes", []) or [])
        kept_names = set(filter_embedders_by_lanes([ec.name for ec in active], lanes_cfg))
        kept = [ec for ec in active if ec.name in kept_names]

        lanes: list[_Lane] = []
        for ec in kept:
            embedder = register_from_config(registry, ec)
            embedder.warmup()
            embedder_id = self._backend.register_embedder(embedder)
            lanes.append(_Lane(embedder_config=ec, embedder=embedder, embedder_id=embedder_id))
        self._lanes = lanes
        logger.info(
            "drain: resolved %d lane(s) for host %s: %s",
            len(lanes),
            self._host_id,
            ", ".join(lane.embedder_config.name for lane in lanes) or "(none)",
        )
        return lanes

    # ── One lane, one batch ────────────────────────────────────────────────

    def drain_lane_once(self, lane: _Lane) -> int:
        """Claim + embed + write + release a single batch for one lane.

        Returns the number of chunks embedded (0 when the lane had no
        claimable work, or when encode/write failed and the claims were
        released for a peer to retry).
        """

        extensions = (
            list(lane.embedder.extensions) if getattr(lane.embedder, "extensions", None) else None
        )
        claimed = self._backend.claim_chunks_for_embedding(
            lane.embedder_id,
            self._host_id,
            batch=self._batch,
            lease_ttl=self._lease_ttl,
            extensions=extensions,
        )
        if not claimed:
            return 0

        chunk_ids = [row[0] for row in claimed]
        texts = [row[1] for row in claimed]

        t0 = time.perf_counter()
        try:
            embeddings = lane.embedder.encode(texts)
        except Exception as exc:
            logger.warning(
                "drain: encode failed for lane %s (%r); releasing %d claim(s)",
                lane.embedder_config.name,
                exc,
                len(chunk_ids),
            )
            self._release(lane, chunk_ids)
            return 0
        elapsed = time.perf_counter() - t0

        try:
            self._backend.write_embeddings(
                lane.embedder_id, list(zip(chunk_ids, embeddings, strict=True))
            )
        except Exception as exc:
            logger.warning(
                "drain: write_embeddings failed for lane %s (%r); releasing %d claim(s)",
                lane.embedder_config.name,
                exc,
                len(chunk_ids),
            )
            self._release(lane, chunk_ids)
            return 0

        # Success: the vectors are persisted, so the claim row is no longer
        # needed — release promptly rather than waiting for lease expiry.
        self._release(lane, chunk_ids)
        self._record_telemetry(lane, len(chunk_ids), elapsed)
        logger.debug(
            "drain: embedded %d chunk(s) on lane %s in %.2fs",
            len(chunk_ids),
            lane.embedder_config.name,
            elapsed,
        )
        return len(chunk_ids)

    def _release(self, lane: _Lane, chunk_ids: list[int]) -> None:
        """Release this host's claims for ``chunk_ids``; never fatal."""

        try:
            self._backend.release_claims(lane.embedder_id, self._host_id, chunk_ids)
        except Exception as exc:  # a stuck release must not break the loop
            logger.warning(
                "drain: release_claims failed for lane %s (%r); lease expiry will reclaim",
                lane.embedder_config.name,
                exc,
            )

    def _record_telemetry(self, lane: _Lane, processed: int, elapsed_s: float) -> None:
        """Best-effort passive ``embed-run`` telemetry for this batch.

        Reuses the canonical, failure-isolated writer from the backfill
        path and the same lazy ``admin.bench`` transport/device probe
        (kept in-function to avoid importing typer at module load).
        """

        try:
            from corpus_forge.admin.bench import (  # noqa: PLC0415
                resolve_device,
                resolve_transport,
            )
            from corpus_forge.embed import _write_embed_run_telemetry  # noqa: PLC0415

            transport = resolve_transport(lane.embedder_config)
            device = resolve_device(transport)
            _write_embed_run_telemetry(
                self._backend,
                self._config,
                lane.embedder_config,
                transport=transport,
                device=device,
                processed=processed,
                elapsed_s=elapsed_s,
            )
        except Exception as exc:  # telemetry must never break the drain
            logger.debug("drain: telemetry write skipped (%r)", exc)

    # ── One full sweep across all lanes ────────────────────────────────────

    def drain_once(self) -> int:
        """One claim batch per lane; returns total chunks embedded this sweep."""

        if self._lanes is None:
            self.resolve_lanes()
        assert self._lanes is not None
        return sum(self.drain_lane_once(lane) for lane in self._lanes)

    # ── Postgres backpressure (issue #125) ─────────────────────────────────

    def _server_overloaded(self) -> bool:
        """True when the Postgres host is too busy to claim against right now.

        Reads the backend's ``server_load()`` snapshot (``pg_stat_activity``
        count vs ``max_connections``) and compares against
        ``[embed] backpressure_max_load``. Best-effort and graceful:

        - disabled (``backpressure_max_load <= 0``) → never overloaded;
        - a backend without ``server_load`` (SQLite, or a build predating
          that method) → never overloaded (the guard simply doesn't engage);
        - any read error or malformed payload → not overloaded (an optional
          throttle must never crash the drain or wedge it shut).

        So the only path that returns ``True`` is a live, parseable load
        snapshot at/above the configured fraction.
        """
        if self._backpressure_max_load <= 0.0:
            return False
        load_fn = getattr(self._backend, "server_load", None)
        if not callable(load_fn):
            return False
        try:
            load = load_fn()
        except Exception:
            return False
        if not isinstance(load, dict):
            return False
        backends = load.get("backends")
        max_conn = load.get("max_connections")
        if not isinstance(backends, int) or not isinstance(max_conn, int) or max_conn <= 0:
            return False
        return backends / max_conn >= self._backpressure_max_load

    # ── The loop ───────────────────────────────────────────────────────────

    def run(
        self,
        stop_event: threading.Event,
        *,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        """Drain until ``stop_event`` is set.

        ``sleep`` defaults to ``stop_event.wait`` (an interruptible sleep
        that returns ``True`` when the event fires mid-wait, so shutdown
        is responsive even during a long backoff); tests inject a fake to
        assert the backoff schedule without real waiting.
        """

        if self._lanes is None:
            self.resolve_lanes()
        if not self._lanes:
            logger.info("drain: no lanes for host %s; drain loop exiting", self._host_id)
            return

        wait = sleep if sleep is not None else stop_event.wait
        delay = self._idle_min
        while not stop_event.is_set():
            # Postgres backpressure (issue #125): when the server is near
            # connection saturation, back off (exponential, interruptible)
            # WITHOUT claiming, so a fleet of drain loops can't drive the
            # shared Postgres host to exhaustion. Reuses the idle-backoff
            # ``delay`` schedule; a later healthy sweep resets it.
            if self._server_overloaded():
                logger.warning(
                    "drain: Postgres backpressure (connections >= %.0f%% of "
                    "max_connections); backing off %.1fs without claiming",
                    self._backpressure_max_load * 100,
                    delay,
                )
                if wait(delay):
                    break  # stop_event fired during the backoff
                delay = min(self._idle_max, delay * 2.0)
                continue

            try:
                embedded = self.drain_once()
            except FederationUnsupported:
                logger.warning(
                    "drain: federation unsupported on this backend (SQLite); drain loop exiting"
                )
                return
            except Exception:
                logger.exception("drain: unexpected error in drain sweep; backing off")
                embedded = 0

            if embedded > 0:
                # Backlog still draining — reset backoff and sweep again now.
                delay = self._idle_min
                continue

            logger.debug("drain: empty sweep; backing off %.1fs", delay)
            if wait(delay):
                break  # stop_event fired during the wait
            delay = min(self._idle_max, delay * 2.0)


__all__ = ["EmbedDrainLoop"]
