"""CS-04 — Walkthrough doc (`docs/claude-integration.md`).

The walkthrough is the human-readable handhold for getting corpus-forge
wired into Claude Code / Claude Desktop and asking the first question.

Tests pin only the section structure — the prose is allowed to evolve.
Each required H2 must be present (regex-checked), and a handful of
load-bearing names (config file, CLI command, env var) must appear at
least once so the doc and the code don't silently desync.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "claude-integration.md"

_REQUIRED_H2 = (
    r"Prerequisites",
    r"Wire-up",
    r"Verify",
    r"First search",
    r"Subagent",
    r"Troubleshooting",
)


def test_doc_exists() -> None:
    """Walkthrough lives at the canonical path."""
    assert DOC_PATH.is_file(), f"missing {DOC_PATH}"


def test_doc_has_all_required_h2_sections() -> None:
    """Every required H2 must be present (regex-checked)."""
    body = DOC_PATH.read_text(encoding="utf-8")
    for heading in _REQUIRED_H2:
        pattern = re.compile(rf"^##\s+{heading}\b", re.MULTILINE)
        assert pattern.search(body), f"docs/claude-integration.md missing '## {heading}'"


def test_doc_references_example_mcp_config() -> None:
    """The Wire-up section must point at the drop-in JSON in `examples/mcp-config/`."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "examples/mcp-config/" in body, (
        "doc must reference the examples/mcp-config/ drop-in configs"
    )


def test_doc_references_mcp_serve_command() -> None:
    """The doc must mention the actual launch command."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "corpus-forge mcp serve" in body, "doc must reference `corpus-forge mcp serve`"


def test_doc_references_corpus_forge_config_env() -> None:
    """The doc must mention the `CORPUS_FORGE_CONFIG` env var."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "CORPUS_FORGE_CONFIG" in body, "doc must reference CORPUS_FORGE_CONFIG"


def test_doc_references_skill_and_subagent_paths() -> None:
    """Subagent + First-search sections must point at the .claude/ artefacts."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert ".claude/skills/corpus-forge-search" in body, "doc must reference the skill path"
    assert ".claude/agents/corpus-forge-researcher" in body, "doc must reference the agent path"


def test_doc_warns_about_reranker_first_run_cost() -> None:
    """Troubleshooting must call out the 600 MB reranker download on first opt-in."""
    body = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "600" in body, "doc must reference the 600 MB reranker download cost"
    assert "rerank" in body, "doc must reference the rerank flag"
