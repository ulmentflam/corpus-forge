"""CS-03 — Agent SDK subagent (`.claude/agents/corpus-forge-researcher.md`).

The subagent file is what a parent Claude session reads when it wants to
spawn a focused "research librarian" against the corpus.  Tests pin:

1. **Frontmatter** parses as YAML, declares ``name``, ``description``,
   ``model``, and a ``tools`` list with the three
   ``mcp__corpus-forge__`` entries.
2. **Body** sets persona / citation discipline / rerank discipline / dataset
   scoping (loose regex hooks — we don't pin prose).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "corpus-forge-researcher.md"

_EXPECTED_TOOLS = {
    "mcp__corpus-forge__search",
    "mcp__corpus-forge__get_chunk",
    "mcp__corpus-forge__list_datasets",
}

_REQUIRED_KEYS = ("name", "description", "model", "tools")


def _read_agent() -> tuple[dict, str]:
    raw = AGENT_PATH.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    assert m, "agent file missing YAML frontmatter block delimited by '---'"
    fm = yaml.safe_load(m.group(1))
    assert isinstance(fm, dict), f"frontmatter not a mapping: {fm!r}"
    return fm, m.group(2)


def test_agent_file_exists() -> None:
    """Agent lives at the well-known `.claude/agents/<slug>.md` path."""
    assert AGENT_PATH.is_file(), f"missing {AGENT_PATH}"


def test_agent_frontmatter_parses_as_yaml() -> None:
    """Frontmatter must be valid YAML so Claude Code's subagent loader accepts it."""
    fm, _ = _read_agent()
    assert isinstance(fm, dict)


def test_agent_frontmatter_has_required_keys() -> None:
    """`name`, `description`, `model`, `tools` are required by the subagent schema."""
    fm, _ = _read_agent()
    for key in _REQUIRED_KEYS:
        assert key in fm, f"frontmatter missing required key '{key}' (keys={list(fm)})"


def test_agent_name_matches_filename_slug() -> None:
    """`name:` must equal the filename slug to avoid silent subagent-name drift."""
    fm, _ = _read_agent()
    assert fm["name"] == "corpus-forge-researcher", (
        f"expected name=='corpus-forge-researcher'; got {fm['name']!r}"
    )


def test_agent_description_is_non_empty_string() -> None:
    """`description` must be a non-empty string (routing-critical for the parent)."""
    fm, _ = _read_agent()
    desc = fm["description"]
    assert isinstance(desc, str) and desc.strip(), f"description not a non-empty string: {desc!r}"


def test_agent_model_is_bare_identifier() -> None:
    """Match existing `.claude/agents/*.md` style — bare model identifier, not quoted."""
    fm, _ = _read_agent()
    model = fm["model"]
    assert isinstance(model, str) and model.strip(), f"model not a non-empty string: {model!r}"
    # Allow short alias ('sonnet', 'opus', 'haiku') or full identifier — but it must be a single
    # token, not a list/dict.
    assert " " not in model, f"model must be a single identifier; got {model!r}"


def test_agent_tools_lists_three_mcp_corpus_forge_tools() -> None:
    """`tools` must enumerate the three mcp__corpus-forge__ tools as a YAML list."""
    fm, _ = _read_agent()
    tools = fm["tools"]
    assert isinstance(tools, list), f"tools must be a YAML list; got {tools!r}"
    assert _EXPECTED_TOOLS.issubset(set(tools)), (
        f"tools missing entries; expected superset of {_EXPECTED_TOOLS}, got {tools}"
    )


def test_agent_body_documents_citation_discipline() -> None:
    """Body must mention citations / source_uri so the parent gets grounded answers."""
    _, body = _read_agent()
    lowered = body.lower()
    assert "citation" in lowered or "cite" in lowered, "agent body must discuss citations"
    assert "source_uri" in lowered or "source uri" in lowered, (
        "agent body must reference the source_uri field"
    )


def test_agent_body_documents_rerank_discipline() -> None:
    """Body must explain when to opt into the cross-encoder rerank (high-stakes only)."""
    _, body = _read_agent()
    lowered = body.lower()
    assert "rerank" in lowered, "agent body must reference the rerank flag"
