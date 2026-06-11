"""CLI-surface tests for ``corpus-forge bench embed`` (rfc-fleet-1 item 4).

The verb is a thin shell around :func:`corpus_forge.admin.bench.bench_embedders`
(covered by ``test_bench.py``); these tests pin exit codes, the
``--json`` payload, the Rich-table default path, and the ``all_failed``
non-zero exit — without touching a real model or DB.
"""

from __future__ import annotations

import json as _json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_forge.admin import bench as bench_mod
from corpus_forge.admin.bench import BenchReport, BenchResult
from corpus_forge.cli import app

runner = CliRunner()


class _StubBackend:
    def close(self) -> None:  # exercised by the finally-close branch
        pass


def _ok_result(name: str = "e1") -> BenchResult:
    return BenchResult(
        embedder_name=name,
        model_key=f"openai:{name}",
        transport="local",
        device="cpu",
        sample_chunks=4,
        chunks_per_s=12.5,
        tokens_per_s=None,
        latency_p50_ms=None,
        latency_p95_ms=None,
        source_kind="synthetic",
        persisted=False,
        error=None,
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch config load + backend build so the CLI never hits disk/DB."""

    from corpus_forge.config import Config

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))
    monkeypatch.setattr(bench_mod, "_build_backend", lambda config: _StubBackend())
    captured: dict[str, Any] = {}

    def _fake_bench(backend: Any, config: Any, **kwargs: Any) -> BenchReport:
        captured.update(kwargs)
        return BenchReport(host_id="host-1", results=[_ok_result()])

    monkeypatch.setattr(bench_mod, "bench_embedders", _fake_bench)
    return captured


def test_bench_embed_table_default(
    patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force human (non-agent) mode so the Rich-table branch runs even when
    # the test host has agent env vars set.
    from corpus_forge.ui import agent as agent_mod

    monkeypatch.setattr(agent_mod, "is_agent_mode", lambda detection=None: False)
    result = runner.invoke(app, ["bench", "embed", "--sample", "4"])
    assert result.exit_code == 0
    # The Rich table rendered (box-drawing border + title token); the
    # narrow CliRunner width truncates column text, so assert on stable
    # structure rather than a specific cell value.
    assert "Embedder" in result.output
    assert "└" in result.output
    # JSON object must NOT be on the human path.
    assert '"host_id"' not in result.output
    assert patched["sample"] == 4
    assert patched["all_"] is False


def test_bench_embed_json(patched: dict[str, Any]) -> None:
    result = runner.invoke(app, ["bench", "embed", "--json"])
    assert result.exit_code == 0
    # The JSON object is on stdout; parse the first complete object.
    out = result.output
    start = out.index("{")
    payload = _json.loads(out[start : out.rindex("}") + 1])
    assert payload["host_id"] == "host-1"
    assert payload["results"][0]["chunks_per_s"] == 12.5


def test_bench_embed_json_threads_agent_mode(patched: dict[str, Any]) -> None:
    # --json forces agent mode → bench_embedders gets agent_mode=True so the
    # phase progress degrades to log milestones (never a stdout-polluting bar).
    runner.invoke(app, ["bench", "embed", "--json"])
    assert patched["agent_mode"] is True


def test_bench_embed_human_threads_agent_mode_false(
    patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_forge.ui import agent as agent_mod

    monkeypatch.setattr(agent_mod, "is_agent_mode", lambda detection=None: False)
    runner.invoke(app, ["bench", "embed", "--sample", "4"])
    assert patched["agent_mode"] is False


def test_bench_embed_passes_embedder_and_all(patched: dict[str, Any]) -> None:
    runner.invoke(app, ["bench", "embed", "-e", "a", "-e", "b"])
    assert patched["embedders"] == ["a", "b"]

    runner.invoke(app, ["bench", "embed", "--all"])
    assert patched["all_"] is True


def test_bench_embed_unknown_embedder_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.config import Config

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))
    monkeypatch.setattr(bench_mod, "_build_backend", lambda config: _StubBackend())

    def _raise(backend: Any, config: Any, **kwargs: Any) -> BenchReport:
        raise ValueError("embedder 'x' not found in config")

    monkeypatch.setattr(bench_mod, "bench_embedders", _raise)
    result = runner.invoke(app, ["bench", "embed", "-e", "x"])
    assert result.exit_code == 2


def test_bench_embed_all_failed_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.config import Config

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))
    monkeypatch.setattr(bench_mod, "_build_backend", lambda config: _StubBackend())

    errored = BenchResult(
        embedder_name="e1",
        model_key="openai:e1",
        transport="local",
        device="cpu",
        sample_chunks=0,
        chunks_per_s=None,
        tokens_per_s=None,
        latency_p50_ms=None,
        latency_p95_ms=None,
        source_kind="none",
        persisted=False,
        error="load failed: boom",
    )
    monkeypatch.setattr(
        bench_mod,
        "bench_embedders",
        lambda backend, config, **kwargs: BenchReport(host_id="h", results=[errored]),
    )
    result = runner.invoke(app, ["bench", "embed"])
    assert result.exit_code == 1


def test_bench_embed_no_config_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.config import Config

    def _raise(cls: Any) -> Any:
        raise FileNotFoundError("no config")

    monkeypatch.setattr(Config, "load", classmethod(_raise))
    result = runner.invoke(app, ["bench", "embed"])
    assert result.exit_code == 2


def test_bench_embed_backend_unreachable_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.config import Config

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))

    def _raise(config: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(bench_mod, "_build_backend", _raise)
    result = runner.invoke(app, ["bench", "embed"])
    assert result.exit_code == 1
