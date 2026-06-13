"""RFC fleet-5 task 3 — `setup --join` seeds the `[service]` embed-drain block.

Covers the join-seeding ergonomics (the drain loop + config knobs already
exist on main; this is purely the join/setup wiring that turns them on):

- `_render_skeleton_join_config` emits a `[service]` block with the given
  `embed_drain` / `ingest_watch` values (and a local-safe default off/on).
- `_join_default_ingest_watch` is GPU-aware: OFF on a capable GPU box
  (pure-drain), ON otherwise, and degrades to ON if the probe raises.
- `run_join` resolves the toggles: `embed_drain` defaults true for a
  joined host; explicit flags win; `ingest_watch` falls back to the
  GPU-aware default.
- A plain local (non-join) setup is UNCHANGED (no `[service]` block).
"""

from __future__ import annotations

import io
import tomllib
from typing import Any

import pytest

from corpus_forge.config import Config
from corpus_forge.setup import run_join
from corpus_forge.setup import wizard as wizard_mod

_DSN = "postgresql://fleet-host:5432/corpus_forge"


class _FakeBackend:
    def __init__(self, shared: tuple[int, dict] | None) -> None:
        self._shared = shared
        self.upserted: list[dict[str, Any]] = []
        self.closed = False

    def upsert_host(self, **kwargs: Any) -> None:
        self.upserted.append(kwargs)

    def get_shared_config(self) -> tuple[int, dict] | None:
        return self._shared

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def isolate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(tmp_path / "config.toml"))
    return tmp_path


def _wire_backend(monkeypatch, backend: _FakeBackend) -> None:
    monkeypatch.setattr(wizard_mod, "_connect_and_verify_schema", lambda dsn: backend)
    monkeypatch.setattr(
        "corpus_forge.telemetry_registry.accelerator_payload",
        lambda: {"kind": "cpu", "device_name": None, "vram_mb": None},
    )


class _Preset:
    def __init__(self, n_gpu_layers: int) -> None:
        self.n_gpu_layers = n_gpu_layers


def _force_gpu(monkeypatch, *, big: bool) -> None:
    """Make `_join_default_ingest_watch`'s probe see a GPU (big) or CPU box."""
    monkeypatch.setattr(wizard_mod, "detect_accelerator", object)
    monkeypatch.setattr(
        wizard_mod, "recommend_embedder_preset", lambda info: _Preset(99 if big else 0)
    )


def _service_block(text: str) -> dict[str, Any]:
    return tomllib.loads(text).get("service", {})


# ── _render_skeleton_join_config ──────────────────────────────────────────


class TestSkeletonServiceBlock:
    def test_emits_service_block_with_given_values(self) -> None:
        text = wizard_mod._render_skeleton_join_config(_DSN, embed_drain=True, ingest_watch=False)
        svc = _service_block(text)
        assert svc == {"embed_drain": True, "ingest_watch": False}
        # Still a loadable config.
        Config(**tomllib.loads(text))

    def test_local_safe_defaults_off_on(self) -> None:
        text = wizard_mod._render_skeleton_join_config(_DSN)
        assert _service_block(text) == {"embed_drain": False, "ingest_watch": True}


# ── _join_default_ingest_watch ────────────────────────────────────────────


class TestJoinDefaultIngestWatch:
    def test_gpu_box_defaults_watch_off(self, monkeypatch) -> None:
        _force_gpu(monkeypatch, big=True)
        assert wizard_mod._join_default_ingest_watch() is False

    def test_cpu_box_defaults_watch_on(self, monkeypatch) -> None:
        _force_gpu(monkeypatch, big=False)
        assert wizard_mod._join_default_ingest_watch() is True

    def test_probe_failure_degrades_to_watch_on(self, monkeypatch) -> None:
        def _boom() -> None:
            raise RuntimeError("probe blew up")

        monkeypatch.setattr(wizard_mod, "detect_accelerator", _boom)
        assert wizard_mod._join_default_ingest_watch() is True


# ── run_join resolution ───────────────────────────────────────────────────


class TestRunJoinServiceToggles:
    def _join(self, isolate_config, monkeypatch, **toggles) -> dict[str, Any]:
        backend = _FakeBackend(shared=None)
        _wire_backend(monkeypatch, backend)
        config_path, _ = run_join(
            _DSN,
            config_dir=isolate_config,
            interactive=False,
            stream_out=io.StringIO(),
            **toggles,
        )
        return _service_block(config_path.read_text(encoding="utf-8"))

    def test_embed_drain_defaults_on_for_joined_host(self, isolate_config, monkeypatch) -> None:
        _force_gpu(monkeypatch, big=False)
        svc = self._join(isolate_config, monkeypatch)
        assert svc["embed_drain"] is True

    def test_explicit_no_embed_drain_wins(self, isolate_config, monkeypatch) -> None:
        _force_gpu(monkeypatch, big=False)
        svc = self._join(isolate_config, monkeypatch, embed_drain=False)
        assert svc["embed_drain"] is False

    def test_ingest_watch_off_on_gpu_box(self, isolate_config, monkeypatch) -> None:
        _force_gpu(monkeypatch, big=True)
        svc = self._join(isolate_config, monkeypatch)
        assert svc["ingest_watch"] is False

    def test_explicit_ingest_watch_overrides_gpu_default(self, isolate_config, monkeypatch) -> None:
        _force_gpu(monkeypatch, big=True)  # would default to False
        svc = self._join(isolate_config, monkeypatch, ingest_watch=True)
        assert svc["ingest_watch"] is True
