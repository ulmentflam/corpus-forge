"""End-to-end integration test for ``corpus-forge agents init`` — T8.

Runs the full CLI verb against a tiny fixture project with the LLM
+ retriever + config layers stubbed. Asserts the on-disk shape matches
the final spec:

1. ``.corpus-agents/{AGENTS.md, shareable.md, citations.json, meta.json}``
   all land in the project root.
2. The root ``AGENTS.md`` is auto-created (because the fixture project
   ships without one) and its body matches the SHAREABLE markdown — not
   the corpus-grounded private one.
3. ``.gitignore`` contains ``.corpus-agents/`` after the run.
4. When the root ``AGENTS.md`` *already* exists (second scenario), the
   CLI must leave it untouched — even with ``--force``.
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
    ChunkRef,
    SynthesisResult,
)
from corpus_forge.cli import app

# ─────────────────────────────────────────────────────────────────────────
# Fixture project + stubs
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def small_project(tmp_path: Path) -> Path:
    """3 .py files + tests/test_foo.py + pyproject.toml + LICENSE."""

    root = tmp_path / "demo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "LICENSE").write_text("MIT License\nCopyright (c) 2026 Example\n", encoding="utf-8")
    src = root / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text("def hello() -> str:\n    return 'hi'\n", encoding="utf-8")
    (src / "util.py").write_text("def util() -> int:\n    return 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_foo.py").write_text("def test_foo():\n    assert True\n", encoding="utf-8")
    return root


PRIVATE_CANNED = dedent(
    """
    ## Project Overview
    Tiny demo project (private, chunk_id=1 file:///x.py).

    ## Languages & Tooling
    python+uv (chunk_id=2).

    ## Project Conventions
    Use `from X import Y`.

    ## Cross-Corpus Patterns
    - pytest fixtures (chunk_id=1).
    - logging.getLogger (chunk_id=2).

    ## Testing
    pytest.
    """
).strip()

SHAREABLE_CANNED = dedent(
    """
    ## Project Overview
    Tiny demo project.

    ## Languages & Tooling
    Python with uv.

    ## Project Conventions
    Use `from X import Y`.

    ## Testing
    pytest, naming `test_<snake_case>`.
    """
).strip()


class _FakeEnricherCfg:
    backend: str = "local"
    local_url: str = "http://localhost:11434"
    local_model: str = "qwen-test"
    remote_url: str = "http://remote/"
    remote_model: str = "qwen-test"
    remote_api_shape: str = "ollama"
    timeout_s: float = 60.0
    temperature: float = 0.1


class _FakeSource:
    def __init__(self, *, root: Path) -> None:
        self.root = root
        self.vault_root = None
        self.projects_root = None
        self.storage_root = None
        self.chats_root = None


class _FakeDataset:
    def __init__(self, *, source_root: Path) -> None:
        self.sources = [_FakeSource(root=source_root)]


class _FakeConfig:
    def __init__(self, *, covered_root: Path) -> None:
        self.datasets = [_FakeDataset(source_root=covered_root)]
        self.embedders: list[Any] = []
        self.code_enricher = _FakeEnricherCfg()

    def resolve_code_enricher_api_key(self) -> str | None:  # pragma: no cover
        return None


def _stub_synth_returns_two(*_args: Any, **_kwargs: Any) -> tuple[SynthesisResult, SynthesisResult]:
    """Return canned private + shareable markdown results."""

    private = SynthesisResult(
        markdown=PRIVATE_CANNED,
        sections=[*AGENTS_MD_REQUIRED_SECTIONS, "## Cross-Corpus Patterns"],
        citations=[
            ChunkRef(chunk_id=1, source_uri="file:///x.py", score=0.9),
            ChunkRef(chunk_id=2, source_uri="file:///y.py", score=0.8),
        ],
    )
    shareable = SynthesisResult(
        markdown=SHAREABLE_CANNED,
        sections=list(AGENTS_MD_REQUIRED_SECTIONS),
        citations=[],
    )
    return private, shareable


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, *, covered_root: Path) -> None:
    config = _FakeConfig(covered_root=covered_root)
    monkeypatch.setattr(cli_agents_mod, "_load_config", lambda: config)
    monkeypatch.setattr(cli_agents_mod, "_build_default_retriever", lambda _cfg: None)
    monkeypatch.setattr(
        cli_agents_mod,
        "_build_llm_callable",
        lambda _cfg: lambda _p: PRIVATE_CANNED,
    )
    monkeypatch.setattr(cli_agents_mod, "synthesize", _stub_synth_returns_two)


# ─────────────────────────────────────────────────────────────────────────
# E2E — fresh project (no root AGENTS.md yet)
# ─────────────────────────────────────────────────────────────────────────


def test_e2e_writes_all_artifacts_on_fresh_project(
    monkeypatch: pytest.MonkeyPatch, small_project: Path
) -> None:
    _patch_pipeline(monkeypatch, covered_root=small_project)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "agents",
            "init",
            "--project-root",
            str(small_project),
            "--no-ingest",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    # 1. All four files landed in .corpus-agents/
    corpus_agents_dir = small_project / ".corpus-agents"
    for name in ("AGENTS.md", "shareable.md", "citations.json", "meta.json"):
        path = corpus_agents_dir / name
        assert path.exists(), f"missing {path}"

    # Private AGENTS.md has the cross-corpus heading; shareable does not.
    private_body = (corpus_agents_dir / "AGENTS.md").read_text(encoding="utf-8")
    shareable_body = (corpus_agents_dir / "shareable.md").read_text(encoding="utf-8")
    assert "## Cross-Corpus Patterns" in private_body
    assert "## Cross-Corpus Patterns" not in shareable_body
    # And no chunk_id leakage in shareable
    assert "chunk_id" not in shareable_body
    assert "file:///" not in shareable_body

    # citations.json carries the 2 ChunkRefs
    citations = json.loads((corpus_agents_dir / "citations.json").read_text(encoding="utf-8"))
    assert len(citations) == 2
    assert {c["chunk_id"] for c in citations} == {1, 2}

    # 2. Root AGENTS.md was auto-created from shareable
    root_agents = small_project / "AGENTS.md"
    assert root_agents.exists()
    assert root_agents.read_text(encoding="utf-8") == SHAREABLE_CANNED

    # 3. .gitignore contains the entry
    gitignore = small_project / ".gitignore"
    assert gitignore.exists()
    assert ".corpus-agents/" in gitignore.read_text(encoding="utf-8")

    # 4. JSON result event lists every written path
    payload = json.loads(result.stdout)
    assert "paths_written" in payload
    paths = payload["paths_written"]
    # All five paths (4 .corpus-agents + 1 root AGENTS.md) appear
    names = {Path(p).name for p in paths}
    assert "AGENTS.md" in names
    assert "shareable.md" in names
    assert "citations.json" in names
    assert "meta.json" in names


# ─────────────────────────────────────────────────────────────────────────
# E2E — existing root AGENTS.md is sacred even with --force
# ─────────────────────────────────────────────────────────────────────────


def test_e2e_existing_root_agents_md_untouched_with_force(
    monkeypatch: pytest.MonkeyPatch, small_project: Path
) -> None:
    _patch_pipeline(monkeypatch, covered_root=small_project)

    # User has already authored a root AGENTS.md
    root_agents = small_project / "AGENTS.md"
    root_agents.write_text("USER-AUTHORED CONTENT\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "agents",
            "init",
            "--project-root",
            str(small_project),
            "--no-ingest",
            "--force",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    # CRITICAL safety: --force did NOT overwrite the root AGENTS.md
    assert root_agents.read_text(encoding="utf-8") == "USER-AUTHORED CONTENT\n"

    # But .corpus-agents/* still got refreshed
    corpus_agents_dir = small_project / ".corpus-agents"
    assert (corpus_agents_dir / "AGENTS.md").exists()
    assert (corpus_agents_dir / "shareable.md").exists()
