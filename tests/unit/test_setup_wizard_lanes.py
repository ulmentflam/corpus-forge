"""Unit tests for the setup wizard's lane-pinning prompt (RFC fleet-2 item 4).

Covers:

- :func:`_parse_lane_csv` lexing.
- :func:`render_config_toml` emits an ``[embed] lanes`` block iff
  ``answers["embed_lanes"]`` is non-empty.
- :func:`maybe_prompt_embed_lanes`:
    * non-interactive honours ``CF_EMBED_LANES`` (the ``--embed-lanes`` flag);
    * non-interactive without the flag writes nothing;
    * interactive prompt fires only when 2+ hosts heartbeated AND the
      backend is reachable; degrades silently on <2 hosts / unreachable;
    * the seeded default follows the accelerator stub.
"""

from __future__ import annotations

import io
import tomllib
from typing import Any

from corpus_forge.acceleration import (
    Accelerator,
    AcceleratorInfo,
    EmbedderPreset,
)
from corpus_forge.setup import render_config_toml
from corpus_forge.setup.wizard import (
    _parse_lane_csv,
    _suggest_lanes_from_accelerator,
    maybe_prompt_embed_lanes,
)


class TestParseLaneCsv:
    def test_empty_string_is_empty(self) -> None:
        assert _parse_lane_csv("") == []

    def test_single_name(self) -> None:
        assert _parse_lane_csv("qwen3_8b") == ["qwen3_8b"]

    def test_splits_and_strips(self) -> None:
        assert _parse_lane_csv("a, b ,c") == ["a", "b", "c"]

    def test_drops_empties(self) -> None:
        assert _parse_lane_csv("a,,b, ") == ["a", "b"]

    def test_dedups_first_wins(self) -> None:
        assert _parse_lane_csv("a,b,a") == ["a", "b"]


class TestRenderEmbedLanesBlock:
    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        return tomllib.loads(text)

    def test_no_embed_lanes_key_no_block(self, tmp_path) -> None:
        answers = {"backend": "sqlite", "embedder": "auto"}
        # detect_accelerator is shelled-out in the auto path; force CPU so
        # the render is deterministic and offline.
        import corpus_forge.setup.wizard as wiz

        info = AcceleratorInfo(kind=Accelerator.CPU)
        orig = wiz.detect_accelerator
        wiz.detect_accelerator = lambda: info  # type: ignore[assignment]
        try:
            parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        finally:
            wiz.detect_accelerator = orig  # type: ignore[assignment]
        assert "embed" not in parsed

    def test_embed_lanes_renders_block(self, tmp_path) -> None:
        answers = {
            "backend": "postgres",
            "postgres_dsn": "postgresql://localhost/db",
            "embedder": "both",
            "embed_lanes": "qwen3_8b,openai_3l",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        assert parsed["embed"]["lanes"] == ["qwen3_8b", "openai_3l"]


class TestSuggestLanesFromAccelerator:
    def _patch_accel(self, monkeypatch: Any, kind: Accelerator, n_gpu_layers: int) -> None:
        import corpus_forge.setup.wizard as wiz

        monkeypatch.setattr(wiz, "detect_accelerator", lambda: AcceleratorInfo(kind=kind))
        preset = EmbedderPreset(
            provider="llama-cpp",
            model_id="x",
            dimension=4096,
            n_gpu_layers=n_gpu_layers,
        )
        monkeypatch.setattr(wiz, "recommend_embedder_preset", lambda _info: preset)

    def test_no_embedders_returns_empty(self, monkeypatch: Any) -> None:
        self._patch_accel(monkeypatch, Accelerator.CUDA, -1)
        assert _suggest_lanes_from_accelerator([]) == []

    def test_big_hardware_suggests_last_lane(self, monkeypatch: Any) -> None:
        # CUDA / GPU-offload preset ⇒ the heavier (last) embedder.
        self._patch_accel(monkeypatch, Accelerator.CUDA, -1)
        assert _suggest_lanes_from_accelerator(["nomic", "qwen3_8b"]) == ["qwen3_8b"]

    def test_cpu_hardware_suggests_first_lane(self, monkeypatch: Any) -> None:
        # CPU preset (n_gpu_layers == 0) ⇒ the lighter (first) embedder.
        self._patch_accel(monkeypatch, Accelerator.CPU, 0)
        assert _suggest_lanes_from_accelerator(["nomic", "qwen3_8b"]) == ["nomic"]


class _FakeBackend:
    def __init__(self, n_hosts: int) -> None:
        self._n = n_hosts
        self.closed = False

    def list_hosts_with_latest_rate(self) -> list[dict]:
        return [{"host_id": f"h{i}"} for i in range(self._n)]

    def close(self) -> None:
        self.closed = True


class TestMaybePromptEmbedLanesNonInteractive:
    def test_flag_writes_lanes(self) -> None:
        answers: dict[str, str] = {}
        maybe_prompt_embed_lanes(
            answers,
            interactive=False,
            env={"CF_EMBED_LANES": "qwen3_8b, nomic"},
        )
        assert answers["embed_lanes"] == "qwen3_8b,nomic"

    def test_no_flag_writes_nothing(self) -> None:
        answers: dict[str, str] = {}
        maybe_prompt_embed_lanes(answers, interactive=False, env={})
        assert "embed_lanes" not in answers


class TestMaybePromptEmbedLanesInteractive:
    def _patch_hosts(self, monkeypatch: Any, host_count: int | None) -> None:
        import corpus_forge.setup.wizard as wiz

        monkeypatch.setattr(wiz, "_count_heartbeated_hosts", lambda _answers, _db: host_count)

    def _patch_accel_cuda(self, monkeypatch: Any) -> None:
        import corpus_forge.setup.wizard as wiz

        monkeypatch.setattr(
            wiz, "detect_accelerator", lambda: AcceleratorInfo(kind=Accelerator.CUDA)
        )
        preset = EmbedderPreset(provider="llama-cpp", model_id="x", dimension=4096, n_gpu_layers=-1)
        monkeypatch.setattr(wiz, "recommend_embedder_preset", lambda _info: preset)

    def test_prompt_fires_with_two_hosts(self, monkeypatch: Any) -> None:
        self._patch_hosts(monkeypatch, 2)
        self._patch_accel_cuda(monkeypatch)
        answers = {"backend": "postgres", "embedder": "both"}
        stream_out = io.StringIO()
        # User accepts the seeded default (blank line).
        stream_in = io.StringIO("\n")
        maybe_prompt_embed_lanes(
            answers,
            interactive=True,
            env={},
            stream_in=stream_in,
            stream_out=stream_out,
        )
        rendered = stream_out.getvalue()
        assert "Fleet detected (2 hosts)" in rendered
        # both ⇒ [qwen3_8b, openai_3l]; CUDA ⇒ last lane suggested.
        assert answers["embed_lanes"] == "openai_3l"

    def test_user_override_wins_over_suggestion(self, monkeypatch: Any) -> None:
        self._patch_hosts(monkeypatch, 3)
        self._patch_accel_cuda(monkeypatch)
        answers = {"backend": "postgres", "embedder": "both"}
        stream_in = io.StringIO("qwen3_8b\n")
        maybe_prompt_embed_lanes(
            answers,
            interactive=True,
            env={},
            stream_in=stream_in,
            stream_out=io.StringIO(),
        )
        assert answers["embed_lanes"] == "qwen3_8b"

    def test_single_host_silent_skip(self, monkeypatch: Any) -> None:
        self._patch_hosts(monkeypatch, 1)
        answers = {"backend": "postgres", "embedder": "both"}
        stream_out = io.StringIO()
        maybe_prompt_embed_lanes(
            answers,
            interactive=True,
            env={},
            stream_in=io.StringIO(""),
            stream_out=stream_out,
        )
        assert "embed_lanes" not in answers
        assert stream_out.getvalue() == ""

    def test_unreachable_backend_silent_skip(self, monkeypatch: Any) -> None:
        # _count_heartbeated_hosts returns None on an unreachable backend.
        self._patch_hosts(monkeypatch, None)
        answers = {"backend": "postgres", "embedder": "both"}
        stream_out = io.StringIO()
        maybe_prompt_embed_lanes(
            answers,
            interactive=True,
            env={},
            stream_in=io.StringIO(""),
            stream_out=stream_out,
        )
        assert "embed_lanes" not in answers
        assert stream_out.getvalue() == ""

    def test_no_active_embedders_silent_skip(self, monkeypatch: Any) -> None:
        self._patch_hosts(monkeypatch, 2)
        # embedder selector that yields no names (only valid selectors emit
        # names; an empty/unknown selector means "no embedders listed").
        answers = {"backend": "postgres", "embedder": "none"}
        stream_out = io.StringIO()
        maybe_prompt_embed_lanes(
            answers,
            interactive=True,
            env={},
            stream_in=io.StringIO(""),
            stream_out=stream_out,
        )
        assert "embed_lanes" not in answers
        assert stream_out.getvalue() == ""


class TestCountHeartbeatedHosts:
    def test_returns_host_count(self, monkeypatch: Any, tmp_path) -> None:
        import corpus_forge.backends.sqlite as sqlite_mod
        from corpus_forge.setup.wizard import _count_heartbeated_hosts

        monkeypatch.setattr(sqlite_mod, "SQLiteBackend", lambda **_k: _FakeBackend(2))
        n = _count_heartbeated_hosts({"backend": "sqlite"}, tmp_path / "x.db")
        assert n == 2

    def test_returns_none_on_error(self, monkeypatch: Any, tmp_path) -> None:
        import corpus_forge.backends.sqlite as sqlite_mod
        from corpus_forge.setup.wizard import _count_heartbeated_hosts

        def _boom(**_k: Any) -> Any:
            raise RuntimeError("backend down")

        monkeypatch.setattr(sqlite_mod, "SQLiteBackend", _boom)
        n = _count_heartbeated_hosts({"backend": "sqlite"}, tmp_path / "x.db")
        assert n is None
