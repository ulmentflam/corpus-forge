"""Unit tests for ``corpus-forge prune`` CLI verb.

First step of ``rfc-corpus-growth-controls``. The verb is a thin shell
around :func:`corpus_forge.admin.prune.prune_dataset` (covered by its own
unit tests); these tests pin the CLI surface — exit codes, flag
behaviour, output shape, and agent-mode emission pairs — without going
near a real backend.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_forge import cli as cli_mod
from corpus_forge.admin import prune as prune_mod
from corpus_forge.admin.prune import PruneCandidate, PruneReport
from corpus_forge.cli import app
from corpus_forge.ui import agent as agent_mod

# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


class _StubBackend:
    """Minimal stand-in for the Postgres/SQLite backend.

    The prune verb's body never touches the backend directly (it hands
    the object to ``prune_dataset``, which we patch out), so a bare
    ``close()`` is enough.
    """

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StubConfig:
    """Stand-in :class:`corpus_forge.config.Config` — only used as the
    backend factory's input, which we also patch."""


@pytest.fixture
def patched_backend(monkeypatch: pytest.MonkeyPatch) -> _StubBackend:
    """Patch ``Config.load`` and ``_build_backend_from_config`` so the CLI
    body never tries to read a real config file or open a real DB."""

    from corpus_forge.config import Config

    backend = _StubBackend()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: _StubConfig()))
    monkeypatch.setattr(
        cli_mod,
        "_build_backend_from_config",
        lambda _config: backend,
    )
    return backend


def _sample_report(*, selected_count: int = 3, applied: bool = False) -> PruneReport:
    """Build a small :class:`PruneReport` for output-shape assertions."""

    candidates = [
        PruneCandidate(
            chunk_id=100 + i,
            document_id=10 + i,
            source_uri=f"file:///tmp/example_{i}.md",
            prune_score=0.9 - 0.1 * i,
            sub_scores={
                "confidence_deficit": 0.5,
                "missing_metadata": 0.5,
                "freshness_inverted": 0.0,
                "duplicate_density": 0.0,
                "feedback_drag": 0.0,
            },
            reason="low / missing classifier confidence",
        )
        for i in range(selected_count)
    ]
    return PruneReport(
        dataset="demo",
        percentile=10,
        considered=30,
        selected=candidates,
        applied=applied,
        deleted=selected_count if applied else 0,
        summary_by_source={f"example_{i}": 1 for i in range(selected_count)},
        duplicate_density_available=True,
    )


# ─────────────────────────────────────────────────────────────────────────
# Dry-run / apply / json-out / argument validation
# ─────────────────────────────────────────────────────────────────────────


def test_dry_run_prints_table_no_apply(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``--apply`` -> ``prune_dataset(apply=False)``, table is printed, exit 0."""

    captured: dict[str, Any] = {}

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:
        captured.update(kw)
        captured["backend"] = backend
        return _sample_report(applied=False)

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    runner = CliRunner()
    result = runner.invoke(app, ["prune", "--dataset", "demo"])
    assert result.exit_code == 0, result.output
    assert captured["apply"] is False
    assert captured["dataset"] == "demo"
    # Table header markers (Rich uses unicode borders, so match on the
    # column labels — those are pure text).
    assert "chunk_id" in result.output
    assert "prune_score" in result.output
    assert "reason" in result.output
    # Summary footer.
    assert "considered=30" in result.output
    assert "selected=3" in result.output
    assert "deleted=0" in result.output
    assert "duplicate_density_available=true" in result.output


def test_apply_flag_calls_delete_path(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--apply`` -> ``prune_dataset(apply=True)``."""

    captured: dict[str, Any] = {}

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:
        captured.update(kw)
        return _sample_report(applied=True)

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    runner = CliRunner()
    result = runner.invoke(app, ["prune", "--dataset", "demo", "--apply"])
    assert result.exit_code == 0, result.output
    assert captured["apply"] is True
    assert "deleted=3" in result.output


def test_dry_run_json_writes_file(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``--dry-run-json PATH`` writes JSON, suppresses table."""

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:
        return _sample_report()

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    out_path = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["prune", "--dataset", "demo", "--dry-run-json", str(out_path)],
    )
    assert result.exit_code == 0, result.output
    # Table NOT emitted — only the one-line receipt.
    assert "chunk_id" not in result.output
    assert f"wrote 3 candidates to {out_path}" in result.output

    parsed = _json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["dataset"] == "demo"
    assert parsed["percentile"] == 10
    assert parsed["considered"] == 30
    assert len(parsed["selected"]) == 3
    assert parsed["selected"][0]["chunk_id"] == 100
    assert parsed["duplicate_density_available"] is True


def test_percentile_out_of_range_exits_2(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--percentile 0`` and ``--percentile 101`` both raise BadParameter (exit 2)."""

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:  # pragma: no cover
        raise AssertionError("prune_dataset should not be called for out-of-range percentile")

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    runner = CliRunner()
    for bad in ("0", "101", "-1"):
        result = runner.invoke(app, ["prune", "--dataset", "demo", "--percentile", bad])
        assert result.exit_code == 2, f"{bad!r} -> exit={result.exit_code}, out={result.output}"


def test_dataset_not_found_exits_3(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ValueError("dataset 'x' not found")`` from prune_dataset -> exit 3."""

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:
        raise ValueError("dataset 'nope' not found")

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    runner = CliRunner()
    result = runner.invoke(app, ["prune", "--dataset", "nope"])
    assert result.exit_code == 3, result.output


def test_internal_error_exits_1(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic exception from prune_dataset -> exit 1."""

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:
        raise RuntimeError("backend connection refused")

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    runner = CliRunner()
    result = runner.invoke(app, ["prune", "--dataset", "demo"])
    assert result.exit_code == 1, result.output


# ─────────────────────────────────────────────────────────────────────────
# Agent mode
# ─────────────────────────────────────────────────────────────────────────


def test_agent_mode_emits_command_pair(
    patched_backend: _StubBackend,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Under agent mode, the verb emits command.start + command.end and no
    human table."""

    def _fake_prune(backend: Any, **kw: Any) -> PruneReport:
        return _sample_report()

    monkeypatch.setattr(prune_mod, "prune_dataset", _fake_prune)

    original = agent_mod.current_detection()
    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.CLAUDE_CODE,
            signal="test",
            raw_value="x",
        )
    )
    try:
        # CliRunner intercepts stdout for the *Click* invocation, but
        # ui_agent.emit writes directly to ``sys.stdout`` via a Rich
        # handle the CliRunner can't always intercept cleanly. Drive
        # the typer callback directly to keep the emit stream visible
        # under ``capsys``. The agent-mode branch returns normally on
        # success (no typer.Exit), so we just call it and let any
        # unexpected exception fail the test loudly.
        cli_mod.prune(
            dataset="demo",
            percentile=10,
            apply=False,
            dry_run_json=None,
        )
    finally:
        agent_mod.set_current(original)

    out = capsys.readouterr().out
    # No Rich table rendered — the table title would surface as plain
    # text outside the JSON events.
    assert "prune candidates (dataset=" not in out
    assert "considered=30 selected=3" not in out

    # One JSON event per line. command.start + command.end pair (the
    # global agent wrapper isn't active in this direct-call path, so we
    # only see the verb body's own emissions — that's what we want to
    # pin).
    events = [_json.loads(line) for line in out.splitlines() if line.strip().startswith("{")]
    kinds = [e.get("event") for e in events]
    assert "command.start" in kinds
    assert "command.end" in kinds
    # command.end carries the report payload.
    end_evt = next(e for e in events if e.get("event") == "command.end")
    assert end_evt["cmd"] == "prune"
    assert end_evt["status"] == "ok"
    assert end_evt["data"]["dataset"] == "demo"
    assert len(end_evt["data"]["selected"]) == 3
