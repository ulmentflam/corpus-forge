"""Unit tests for ``corpus-forge agents init`` — T6 + T7 (redirected).

Covers:
- ``--help`` exits 0 and lists every public flag.
- New flag set: ``--output-dir``, ``--no-root-write``, ``--gitignore`` /
  ``--no-gitignore``.
- Bad ``--project-root`` exits 1.
- Missing LLM (``code_enricher.backend == "none"``) exits 2.
- LLM call raises → exits 3.
- ``--no-ingest`` on uncovered project exits 2 with explanatory message.
- Happy path writes ``.corpus-agents/*`` + root AGENTS.md + appends gitignore.
- Root AGENTS.md is never overwritten — not even with ``--force``.
- ``_project_covered_by_active_dataset`` helper logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_forge import cli_agents as cli_agents_mod
from corpus_forge.agents.synthesizer import (
    AGENTS_MD_REQUIRED_SECTIONS,
    LLMSynthesisError,
    SynthesisResult,
)
from corpus_forge.cli import app

# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    src = root / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text("def hello() -> str:\n    return 'hi'\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text("def test_hello():\n    pass\n", encoding="utf-8")
    return root


class _FakeEnricherCfg:
    """Minimal stand-in for :class:`EnricherConfig`."""

    backend: str = "local"
    local_url: str = "http://localhost:11434"
    local_model: str = "qwen-test"
    remote_url: str = "http://remote/"
    remote_model: str = "qwen-test"
    remote_api_shape: str = "ollama"
    timeout_s: float = 60.0
    temperature: float = 0.1


class _FakeConfig:
    """Stand-in for :class:`corpus_forge.config.Config`."""

    def __init__(self, *, covered_root: Path | None = None, backend: str = "local") -> None:
        self.datasets: list[Any] = []
        self.embedders: list[Any] = []
        cfg = _FakeEnricherCfg()
        cfg.backend = backend
        self.code_enricher = cfg
        if covered_root is not None:
            dataset = _FakeDataset()
            source = _FakeSource(root=covered_root)
            dataset.sources = [source]
            self.datasets = [dataset]

    def resolve_code_enricher_api_key(self) -> str | None:  # pragma: no cover
        return None


class _FakeDataset:
    def __init__(self) -> None:
        self.sources: list[Any] = []


class _FakeSource:
    def __init__(self, *, root: Path | None = None) -> None:
        self.root = root
        self.vault_root = None
        self.projects_root = None
        self.storage_root = None
        self.chats_root = None


def _good_private_md() -> str:
    return dedent(
        """
        ## Project Overview
        demo.

        ## Languages & Tooling
        python+uv.

        ## Project Conventions
        from X import Y.

        ## Cross-Corpus Patterns
        - (no matches)

        ## Testing
        pytest.
        """
    ).strip()


def _good_shareable_md() -> str:
    return dedent(
        """
        ## Project Overview
        demo.

        ## Languages & Tooling
        python+uv.

        ## Project Conventions
        from X import Y.

        ## Testing
        pytest.
        """
    ).strip()


def _stub_synth_ok(*_args: Any, **_kwargs: Any) -> tuple[SynthesisResult, SynthesisResult]:
    private = SynthesisResult(
        markdown=_good_private_md(),
        sections=[*AGENTS_MD_REQUIRED_SECTIONS, "## Cross-Corpus Patterns"],
        citations=[],
    )
    shareable = SynthesisResult(
        markdown=_good_shareable_md(),
        sections=list(AGENTS_MD_REQUIRED_SECTIONS),
        citations=[],
    )
    return private, shareable


def _patch_all(monkeypatch: pytest.MonkeyPatch, config: _FakeConfig) -> None:
    """Stub every external dependency the CLI verb reaches for."""

    monkeypatch.setattr(cli_agents_mod, "_load_config", lambda: config)
    monkeypatch.setattr(cli_agents_mod, "_build_default_retriever", lambda _cfg: None)
    monkeypatch.setattr(
        cli_agents_mod,
        "_build_llm_callable",
        lambda _cfg: lambda _prompt: _good_private_md(),
    )
    monkeypatch.setattr(cli_agents_mod, "synthesize", _stub_synth_ok)


# ─────────────────────────────────────────────────────────────────────────
# Help text + flag surface
# ─────────────────────────────────────────────────────────────────────────


def test_help_exits_zero_and_lists_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["agents", "init", "--help"])
    assert result.exit_code == 0, result.output
    for flag in (
        "--project-root",
        "--output-dir",
        "--no-root-write",
        "--gitignore",
        "--no-gitignore",
        "--no-ingest",
        "--force",
        "--diff",
        "--json",
    ):
        assert flag in result.output, f"missing flag in help: {flag}"


# ─────────────────────────────────────────────────────────────────────────
# Exit code 1 — user input errors
# ─────────────────────────────────────────────────────────────────────────


def test_bad_project_root_exits_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    config = _FakeConfig(covered_root=tmp_path)
    _patch_all(monkeypatch, config)
    bogus = tmp_path / "does-not-exist"
    result = runner.invoke(app, ["agents", "init", "--project-root", str(bogus)])
    assert result.exit_code == 1
    assert "not a directory" in result.output.lower() or "project-root" in result.output.lower()


# ─────────────────────────────────────────────────────────────────────────
# Exit code 2 — corpus not ready / no LLM
# ─────────────────────────────────────────────────────────────────────────


def test_no_llm_endpoint_exits_two(monkeypatch: pytest.MonkeyPatch, python_project: Path) -> None:
    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project, backend="none")
    monkeypatch.setattr(cli_agents_mod, "_load_config", lambda: config)
    monkeypatch.setattr(cli_agents_mod, "_build_default_retriever", lambda _cfg: None)
    # Note: we do NOT stub _build_llm_callable here — it should fail.
    monkeypatch.setattr(cli_agents_mod, "synthesize", _stub_synth_ok)

    result = runner.invoke(
        app, ["agents", "init", "--project-root", str(python_project), "--no-ingest"]
    )
    assert result.exit_code == 2
    assert "code_enricher" in result.output.lower() or "llm" in result.output.lower()


def test_uncovered_project_with_no_ingest_exits_two(
    monkeypatch: pytest.MonkeyPatch, python_project: Path, tmp_path: Path
) -> None:
    """The project is not in the dataset sources → --no-ingest forces exit 2."""

    runner = CliRunner()
    other = tmp_path / "elsewhere"
    other.mkdir()
    config = _FakeConfig(covered_root=other)
    _patch_all(monkeypatch, config)
    result = runner.invoke(
        app, ["agents", "init", "--project-root", str(python_project), "--no-ingest"]
    )
    assert result.exit_code == 2
    assert "not covered" in result.output.lower() or "no-ingest" in result.output.lower()


# ─────────────────────────────────────────────────────────────────────────
# Exit code 3 — LLM synthesis failure
# ─────────────────────────────────────────────────────────────────────────


def test_llm_synthesis_failure_exits_three(
    monkeypatch: pytest.MonkeyPatch, python_project: Path
) -> None:
    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    monkeypatch.setattr(cli_agents_mod, "_load_config", lambda: config)
    monkeypatch.setattr(cli_agents_mod, "_build_default_retriever", lambda _cfg: None)
    monkeypatch.setattr(
        cli_agents_mod,
        "_build_llm_callable",
        lambda _cfg: lambda _p: "",
    )

    def _boom(*_a: Any, **_kw: Any) -> tuple[SynthesisResult, SynthesisResult]:
        raise LLMSynthesisError("empty response")

    monkeypatch.setattr(cli_agents_mod, "synthesize", _boom)
    result = runner.invoke(
        app, ["agents", "init", "--project-root", str(python_project), "--no-ingest"]
    )
    assert result.exit_code == 3
    assert "synthesis" in result.output.lower() or "empty" in result.output.lower()


# ─────────────────────────────────────────────────────────────────────────
# Happy path — writes outputs + emits JSON
# ─────────────────────────────────────────────────────────────────────────


def test_happy_path_writes_corpus_agents_and_root(
    monkeypatch: pytest.MonkeyPatch, python_project: Path
) -> None:
    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    _patch_all(monkeypatch, config)

    result = runner.invoke(
        app,
        ["agents", "init", "--project-root", str(python_project), "--no-ingest", "--json"],
    )
    assert result.exit_code == 0, result.output

    # JSON result was emitted
    payload = json.loads(result.stdout)
    assert "paths_written" in payload
    assert "sections" in payload
    assert "citations" in payload

    # .corpus-agents/{AGENTS.md, shareable.md, citations.json, meta.json}
    corpus_agents_dir = python_project / ".corpus-agents"
    assert (corpus_agents_dir / "AGENTS.md").exists()
    assert (corpus_agents_dir / "shareable.md").exists()
    assert (corpus_agents_dir / "citations.json").exists()
    assert (corpus_agents_dir / "meta.json").exists()

    # Root AGENTS.md was created from shareable (because absent at start)
    root_agents = python_project / "AGENTS.md"
    assert root_agents.exists()

    # .gitignore picked up the entry
    gitignore = python_project / ".gitignore"
    assert gitignore.exists()
    assert ".corpus-agents/" in gitignore.read_text(encoding="utf-8")


def test_existing_root_agents_md_left_untouched(
    monkeypatch: pytest.MonkeyPatch, python_project: Path
) -> None:
    """If the user already has an AGENTS.md, the CLI must NOT overwrite it."""

    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    _patch_all(monkeypatch, config)

    root_agents = python_project / "AGENTS.md"
    root_agents.write_text("USER-AUTHORED\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["agents", "init", "--project-root", str(python_project), "--no-ingest", "--json"],
    )
    assert result.exit_code == 0, result.output
    # Sacred
    assert root_agents.read_text(encoding="utf-8") == "USER-AUTHORED\n"


def test_force_does_not_overwrite_root_agents_md(
    monkeypatch: pytest.MonkeyPatch, python_project: Path
) -> None:
    """CRITICAL safety: --force overwrites .corpus-agents/* only; root AGENTS.md is sacred."""

    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    _patch_all(monkeypatch, config)

    root_agents = python_project / "AGENTS.md"
    root_agents.write_text("SACRED\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "agents",
            "init",
            "--project-root",
            str(python_project),
            "--no-ingest",
            "--force",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert root_agents.read_text(encoding="utf-8") == "SACRED\n"


def test_no_root_write_skips_root_agents_md(
    monkeypatch: pytest.MonkeyPatch, python_project: Path
) -> None:
    """--no-root-write disables auto-create of root AGENTS.md even when absent."""

    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    _patch_all(monkeypatch, config)

    result = runner.invoke(
        app,
        [
            "agents",
            "init",
            "--project-root",
            str(python_project),
            "--no-ingest",
            "--no-root-write",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (python_project / "AGENTS.md").exists()


def test_no_gitignore_skips_append(monkeypatch: pytest.MonkeyPatch, python_project: Path) -> None:
    """--no-gitignore disables the .gitignore append."""

    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    _patch_all(monkeypatch, config)

    result = runner.invoke(
        app,
        [
            "agents",
            "init",
            "--project-root",
            str(python_project),
            "--no-ingest",
            "--no-gitignore",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (python_project / ".gitignore").exists()


def test_output_dir_overrides_default_location(
    monkeypatch: pytest.MonkeyPatch, python_project: Path, tmp_path: Path
) -> None:
    """--output-dir redirects the .corpus-agents/* writes outside the project."""

    runner = CliRunner()
    config = _FakeConfig(covered_root=python_project)
    _patch_all(monkeypatch, config)

    outside = tmp_path / "elsewhere-out"
    result = runner.invoke(
        app,
        [
            "agents",
            "init",
            "--project-root",
            str(python_project),
            "--output-dir",
            str(outside),
            "--no-ingest",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    # Output dir got the four files
    for name in ("AGENTS.md", "shareable.md", "citations.json", "meta.json"):
        assert (outside / name).exists(), f"missing {name}"


# ─────────────────────────────────────────────────────────────────────────
# Coverage helper unit tests
# ─────────────────────────────────────────────────────────────────────────


def test_project_covered_when_source_root_matches(tmp_path: Path) -> None:
    cfg = _FakeConfig(covered_root=tmp_path)
    assert cli_agents_mod._project_covered_by_active_dataset(cfg, tmp_path) is True


def test_project_covered_when_source_root_contains_project(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "sub"
    parent.mkdir()
    child.mkdir()
    cfg = _FakeConfig(covered_root=parent)
    assert cli_agents_mod._project_covered_by_active_dataset(cfg, child) is True


def test_project_not_covered_when_separate_tree(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    cfg = _FakeConfig(covered_root=a)
    assert cli_agents_mod._project_covered_by_active_dataset(cfg, b) is False


def test_auto_ingest_runs_when_uncovered(
    monkeypatch: pytest.MonkeyPatch, python_project: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    other = tmp_path / "elsewhere"
    other.mkdir()
    config = _FakeConfig(covered_root=other)
    monkeypatch.setattr(cli_agents_mod, "_load_config", lambda: config)
    monkeypatch.setattr(cli_agents_mod, "_build_default_retriever", lambda _cfg: None)
    monkeypatch.setattr(
        cli_agents_mod,
        "_build_llm_callable",
        lambda _cfg: lambda _p: _good_private_md(),
    )
    monkeypatch.setattr(cli_agents_mod, "synthesize", _stub_synth_ok)

    called: dict[str, bool] = {"ingest": False}

    def fake_ingest(_config: Any, _root: Path) -> None:
        called["ingest"] = True

    monkeypatch.setattr(cli_agents_mod, "_run_auto_ingest", fake_ingest)

    result = runner.invoke(app, ["agents", "init", "--project-root", str(python_project), "--json"])
    assert result.exit_code == 0, result.output
    assert called["ingest"] is True
