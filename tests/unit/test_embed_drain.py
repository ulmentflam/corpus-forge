"""Unit tests for the daemon embed-drain loop (RFC fleet-5, item 1).

Covers, with stubbed backend / embedder / config (no real model or DB):

* one lane, one batch — claim → encode → write → release → telemetry;
* release-on-failure (encode failure, write failure) so peers retry;
* a release failure is swallowed (lease expiry is the backstop);
* `drain_once` sums across lanes;
* bounded exponential backoff on empty sweeps + reset on a non-empty sweep;
* `FederationUnsupported` (SQLite) and a generic sweep error are handled;
* lane resolution: `[embed] lanes` filtering, warmup + register;
* constructor validation.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pytest

from corpus_forge import embed_drain
from corpus_forge.backends.base import FederationUnsupported
from corpus_forge.embed_drain import EmbedDrainLoop

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _StubEmbedderConfig:
    def __init__(
        self,
        name: str,
        *,
        provider: str = "sentence_transformers",
        model_id: str = "m",
        base_url: str | None = None,
        active: bool = True,
        batch_size: int = 32,
    ) -> None:
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self.base_url = base_url
        self.active = active
        self.batch_size = batch_size


class _StubEmbedder:
    def __init__(
        self,
        name: str,
        *,
        dim: int = 4,
        fail_encode: bool = False,
        extensions: list[str] | None = None,
    ) -> None:
        self.name = name
        self.model_id = "m"
        self.dimension = dim
        self._dim = dim
        self._fail_encode = fail_encode
        self.extensions: list[str] = extensions or []
        self.warmed = False

    def warmup(self) -> None:
        self.warmed = True

    def encode(self, texts: Any) -> Any:
        if self._fail_encode:
            raise RuntimeError("encode boom")
        return np.array([[0.1] * self._dim for _ in texts], dtype="float32")


class _StubEmbedConfig:
    def __init__(
        self,
        lanes: list[str] | None = None,
        claim_lease_ttl: int = 600,
        claim_batch_size: int = 1024,
        max_inflight_batches: int = 1,
    ) -> None:
        self.lanes = lanes or []
        self.claim_lease_ttl = claim_lease_ttl
        self.claim_batch_size = claim_batch_size
        self.max_inflight_batches = max_inflight_batches


class _StubConfig:
    def __init__(
        self,
        embedders: list[_StubEmbedderConfig],
        *,
        lanes: list[str] | None = None,
        host_id: str = "host-1",
    ) -> None:
        self.embedders = embedders
        self.embed = _StubEmbedConfig(lanes=lanes)
        self._host_id = host_id

    def host_id(self) -> str:
        return self._host_id


class _StubBackend:
    """Backend double: scripted claim batches + recorded release/write/telemetry."""

    def __init__(
        self,
        claim_batches: list[list[tuple[int, str, str]]] | None = None,
        *,
        claim_raises: BaseException | None = None,
        write_raises: bool = False,
        release_raises: bool = False,
    ) -> None:
        self._claim_batches = list(claim_batches or [])
        self._claim_raises = claim_raises
        self._write_raises = write_raises
        self._release_raises = release_raises
        self.claims_made: list[dict[str, Any]] = []
        self.released: list[tuple[int, str, list[int]]] = []
        self.written: list[tuple[int, list]] = []
        self.benchmark_rows: list[dict[str, Any]] = []
        self._next_embedder_id = 1

    def register_embedder(self, embedder: Any) -> int:
        eid = self._next_embedder_id
        self._next_embedder_id += 1
        return eid

    def claim_chunks_for_embedding(
        self,
        embedder_id: int,
        host_id: str,
        *,
        batch: int = 1024,
        lease_ttl: int = 600,
        extensions: list[str] | None = None,
    ) -> list[tuple[int, str, str]]:
        self.claims_made.append(
            {
                "embedder_id": embedder_id,
                "host_id": host_id,
                "batch": batch,
                "lease_ttl": lease_ttl,
                "extensions": extensions,
            }
        )
        if self._claim_raises is not None:
            raise self._claim_raises
        if self._claim_batches:
            return self._claim_batches.pop(0)
        return []

    def release_claims(self, embedder_id: int, host_id: str, chunk_ids: list[int]) -> int:
        if self._release_raises:
            raise RuntimeError("release boom")
        self.released.append((embedder_id, host_id, list(chunk_ids)))
        return len(chunk_ids)

    def write_embeddings(self, embedder_id: int, pairs: list) -> None:
        if self._write_raises:
            raise RuntimeError("write boom")
        self.written.append((embedder_id, list(pairs)))

    def insert_model_benchmark(self, **kwargs: Any) -> None:
        self.benchmark_rows.append(kwargs)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, embedders: list[_StubEmbedder]) -> None:
    by_name = {e.name: e for e in embedders}
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda registry, cfg: by_name[cfg.name],
    )


def _lane(cfg: _StubEmbedderConfig, emb: _StubEmbedder, embedder_id: int = 1) -> Any:
    return embed_drain._Lane(embedder_config=cfg, embedder=emb, embedder_id=embedder_id)


# ---------------------------------------------------------------------------
# drain_lane_once
# ---------------------------------------------------------------------------


def test_drain_lane_once_happy_path() -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[[(1, "t1", ""), (2, "t2", ""), (3, "t3", "")]])
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), host_id="host-1")

    n = loop.drain_lane_once(_lane(cfg, emb, 7))

    assert n == 3
    # claim → write → release, all for embedder_id 7 / this host
    assert backend.claims_made[0]["embedder_id"] == 7
    assert len(backend.written) == 1
    written_embedder_id, written_pairs = backend.written[0]
    assert written_embedder_id == 7
    assert [chunk_id for chunk_id, _vec in written_pairs] == [1, 2, 3]
    assert backend.released == [(7, "host-1", [1, 2, 3])]
    # passive embed-run telemetry recorded for the batch
    assert len(backend.benchmark_rows) == 1
    assert backend.benchmark_rows[0]["source"] == "embed-run"
    assert backend.benchmark_rows[0]["sample_chunks"] == 3


def test_drain_lane_once_empty_returns_zero() -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[])  # nothing to claim
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]))
    assert loop.drain_lane_once(_lane(cfg, emb)) == 0
    assert backend.written == []
    assert backend.released == []


def test_encode_failure_releases_and_returns_zero() -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1", fail_encode=True)
    backend = _StubBackend(claim_batches=[[(1, "t1", ""), (2, "t2", "")]])
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), host_id="h")

    assert loop.drain_lane_once(_lane(cfg, emb, 3)) == 0
    # claims released for a peer to retry; nothing written
    assert backend.released == [(3, "h", [1, 2])]
    assert backend.written == []
    assert backend.benchmark_rows == []


def test_write_failure_releases_and_returns_zero() -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[[(1, "t1", "")]], write_raises=True)
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), host_id="h")

    assert loop.drain_lane_once(_lane(cfg, emb, 5)) == 0
    assert backend.released == [(5, "h", [1])]
    assert backend.benchmark_rows == []


def test_release_failure_is_swallowed() -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[[(1, "t1", ""), (2, "t2", "")]], release_raises=True)
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), host_id="h")

    # Write succeeded; the release blew up but is swallowed (lease expiry backstop),
    # so the batch still counts and telemetry is still recorded.
    assert loop.drain_lane_once(_lane(cfg, emb, 1)) == 2
    assert len(backend.written) == 1
    assert backend.benchmark_rows[0]["source"] == "embed-run"


def test_telemetry_failure_never_breaks_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[[(1, "t1", "")]])
    # Force the telemetry writer to blow up; the drain must swallow it and
    # still count the batch + persist the vectors.
    monkeypatch.setattr(
        "corpus_forge.embed._write_embed_run_telemetry",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telemetry boom")),
    )
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), host_id="h")

    assert loop.drain_lane_once(_lane(cfg, emb, 1)) == 1
    assert len(backend.written) == 1
    assert backend.released == [(1, "h", [1])]


def test_claim_passes_lease_ttl_and_extensions() -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1", extensions=[".py"])
    backend = _StubBackend(claim_batches=[[]])
    config = _StubConfig([cfg])
    config.embed.claim_lease_ttl = 123
    loop = EmbedDrainLoop(backend, config, host_id="h", batch=64)

    loop.drain_lane_once(_lane(cfg, emb, 2))
    call = backend.claims_made[0]
    assert call["batch"] == 64
    assert call["lease_ttl"] == 123
    assert call["extensions"] == [".py"]


def test_claim_batch_size_resolves_from_config() -> None:
    """Issue #125 item 4: with no explicit ``batch`` arg, the loop claims
    ``[embed] claim_batch_size`` chunks per sweep."""
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[[]])
    config = _StubConfig([cfg])
    config.embed.claim_batch_size = 50
    loop = EmbedDrainLoop(backend, config, host_id="h")  # no batch= override

    loop.drain_lane_once(_lane(cfg, emb, 2))
    assert backend.claims_made[0]["batch"] == 50


def test_explicit_batch_arg_overrides_config() -> None:
    """An explicit ``batch=`` still wins over the config knob."""
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    backend = _StubBackend(claim_batches=[[]])
    config = _StubConfig([cfg])
    config.embed.claim_batch_size = 50
    loop = EmbedDrainLoop(backend, config, host_id="h", batch=200)

    loop.drain_lane_once(_lane(cfg, emb, 2))
    assert backend.claims_made[0]["batch"] == 200


def test_invalid_claim_batch_size_rejected() -> None:
    """A non-positive resolved batch is a construction error, not a silent
    no-op sweep."""
    cfg = _StubEmbedderConfig("e1")
    config = _StubConfig([cfg])
    config.embed.claim_batch_size = 0
    with pytest.raises(ValueError, match="batch must be > 0"):
        EmbedDrainLoop(_StubBackend(claim_batches=[]), config, host_id="h")


def test_max_inflight_batches_resolved_and_floored() -> None:
    """The forward-guard cap is read from config and floored at 1."""
    cfg = _StubEmbedderConfig("e1")
    config = _StubConfig([cfg])
    config.embed.max_inflight_batches = 4
    loop = EmbedDrainLoop(_StubBackend(claim_batches=[]), config, host_id="h")
    assert loop._max_inflight == 4


# ---------------------------------------------------------------------------
# drain_once across lanes
# ---------------------------------------------------------------------------


def test_drain_once_sums_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_a = _StubEmbedderConfig("a")
    cfg_b = _StubEmbedderConfig("b")
    emb_a = _StubEmbedder("a")
    emb_b = _StubEmbedder("b")
    _patch_registry(monkeypatch, [emb_a, emb_b])
    # lane a claims 2, lane b claims 1 (claim called once per lane, in order)
    backend = _StubBackend(claim_batches=[[(1, "x", ""), (2, "y", "")], [(3, "z", "")]])
    loop = EmbedDrainLoop(backend, _StubConfig([cfg_a, cfg_b]))

    assert loop.drain_once() == 3
    assert len(backend.written) == 2  # one write per non-empty lane


# ---------------------------------------------------------------------------
# run() — backoff + termination
# ---------------------------------------------------------------------------


def _run_with_fake_sleep(loop: EmbedDrainLoop, *, stop_after: int) -> list[float]:
    """Drive run() with a fake interruptible sleep; stop after N sleeps."""

    sleeps: list[float] = []
    ev = threading.Event()

    def fake_sleep(delay: float) -> bool:
        sleeps.append(delay)
        if len(sleeps) >= stop_after:
            ev.set()
        return ev.is_set()

    loop.run(ev, sleep=fake_sleep)
    return sleeps


def test_backoff_doubles_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    _patch_registry(monkeypatch, [emb])
    backend = _StubBackend(claim_batches=[])  # always empty → always back off
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), idle_min=5, idle_max=40)

    sleeps = _run_with_fake_sleep(loop, stop_after=5)
    assert sleeps == [5, 10, 20, 40, 40]  # exponential, capped at idle_max


def test_nonempty_sweep_resets_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    _patch_registry(monkeypatch, [emb])
    # empty, empty, NON-empty, empty, ...
    backend = _StubBackend(claim_batches=[[], [], [(1, "t", "")], []])
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), idle_min=5, idle_max=100)

    sleeps = _run_with_fake_sleep(loop, stop_after=3)
    # 5, 10 (backing off), then a non-empty sweep resets → next empty sleeps 5 again
    assert sleeps == [5, 10, 5]
    assert len(backend.written) == 1


def test_federation_unsupported_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    _patch_registry(monkeypatch, [emb])
    backend = _StubBackend(claim_raises=FederationUnsupported("sqlite"))
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]))

    sleeps: list[float] = []
    ev = threading.Event()  # never set — loop must exit on its own
    loop.run(ev, sleep=lambda d: sleeps.append(d) or ev.is_set())
    assert sleeps == []  # exited before any backoff


def test_generic_sweep_error_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    _patch_registry(monkeypatch, [emb])
    backend = _StubBackend(claim_raises=RuntimeError("transient db blip"))
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]), idle_min=2, idle_max=8)

    sleeps = _run_with_fake_sleep(loop, stop_after=2)
    # generic error treated as an empty sweep → bounded backoff, no crash
    assert sleeps == [2, 4]


def test_run_with_no_lanes_exits_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    # lanes filter selects nothing → run exits immediately
    cfg = _StubEmbedderConfig("a")
    emb = _StubEmbedder("a")
    _patch_registry(monkeypatch, [emb])
    loop = EmbedDrainLoop(backend=_StubBackend(), config=_StubConfig([cfg], lanes=["nonexistent"]))

    slept = []
    ev = threading.Event()
    loop.run(ev, sleep=lambda d: slept.append(d) or True)
    assert slept == []


# ---------------------------------------------------------------------------
# resolve_lanes
# ---------------------------------------------------------------------------


def test_resolve_lanes_warms_and_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubEmbedderConfig("e1")
    emb = _StubEmbedder("e1")
    _patch_registry(monkeypatch, [emb])
    backend = _StubBackend()
    loop = EmbedDrainLoop(backend, _StubConfig([cfg]))

    lanes = loop.resolve_lanes()
    assert len(lanes) == 1
    assert lanes[0].embedder_config.name == "e1"
    assert lanes[0].embedder_id == 1
    assert emb.warmed is True


def test_resolve_lanes_respects_lane_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_a = _StubEmbedderConfig("a")
    cfg_b = _StubEmbedderConfig("b")
    emb_a = _StubEmbedder("a")
    emb_b = _StubEmbedder("b")
    _patch_registry(monkeypatch, [emb_a, emb_b])
    loop = EmbedDrainLoop(_StubBackend(), _StubConfig([cfg_a, cfg_b], lanes=["a"]))

    lanes = loop.resolve_lanes()
    assert [lane.embedder_config.name for lane in lanes] == ["a"]
    assert emb_b.warmed is False  # the unpinned lane was never warmed


def test_resolve_lanes_skips_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_a = _StubEmbedderConfig("a", active=True)
    cfg_b = _StubEmbedderConfig("b", active=False)
    emb_a = _StubEmbedder("a")
    emb_b = _StubEmbedder("b")
    _patch_registry(monkeypatch, [emb_a, emb_b])
    loop = EmbedDrainLoop(_StubBackend(), _StubConfig([cfg_a, cfg_b]))

    assert [lane.embedder_config.name for lane in loop.resolve_lanes()] == ["a"]


# ---------------------------------------------------------------------------
# constructor validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"batch": 0}, "batch"),
        ({"idle_min": 0}, "idle_min"),
        ({"idle_max": 1, "idle_min": 5}, "idle_max"),
    ],
)
def test_constructor_validation(kwargs: dict[str, Any], match: str) -> None:
    cfg = _StubEmbedderConfig("e1")
    with pytest.raises(ValueError, match=match):
        EmbedDrainLoop(_StubBackend(), _StubConfig([cfg]), **kwargs)


def test_lease_ttl_defaults_from_config() -> None:
    cfg = _StubEmbedderConfig("e1")
    config = _StubConfig([cfg])
    config.embed.claim_lease_ttl = 999
    loop = EmbedDrainLoop(_StubBackend(), config)
    assert loop._lease_ttl == 999
