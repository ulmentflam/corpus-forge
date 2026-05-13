"""CS-02 — Claude Code skill (`.claude/skills/corpus-forge-search/SKILL.md`).

The skill file is what Claude Code reads to decide *when* to invoke the
corpus-forge MCP tools.  It must satisfy two constraints:

1. **Frontmatter** parses as YAML and declares ``name``, ``description``,
   ``allowed-tools`` (the three MCP tools, prefixed
   ``mcp__corpus-forge__``).
2. **Body** contains a fixed set of H2 sections so the model gets a
   consistent invocation playbook (regex-checked).

We deliberately do not assert prose content beyond the headings — the
authoring is allowed to evolve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "corpus-forge-search" / "SKILL.md"

_REQUIRED_H2 = (
    r"What is corpus-forge",
    r"When to invoke",
    r"When NOT to invoke",
    r"Tool playbook",
    r"Response handling",
    r"Citation format",
)

_EXPECTED_TOOLS = {
    "mcp__corpus-forge__search",
    "mcp__corpus-forge__get_chunk",
    "mcp__corpus-forge__list_datasets",
}


def _read_skill() -> tuple[dict, str]:
    """Split SKILL.md into (frontmatter dict, body) — fails loudly on shape drift."""
    raw = SKILL_PATH.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    assert m, "SKILL.md missing YAML frontmatter block delimited by '---'"
    fm = yaml.safe_load(m.group(1))
    assert isinstance(fm, dict), f"frontmatter not a mapping: {fm!r}"
    return fm, m.group(2)


def test_skill_file_exists() -> None:
    """Skill lives at the well-known `.claude/skills/<slug>/SKILL.md` path."""
    assert SKILL_PATH.is_file(), f"missing {SKILL_PATH}"


def test_skill_frontmatter_parses_as_yaml() -> None:
    """Frontmatter must be valid YAML so Claude Code's parser accepts it."""
    fm, _ = _read_skill()
    assert isinstance(fm, dict)


def test_skill_frontmatter_has_required_keys() -> None:
    """`name`, `description`, `allowed-tools` are required by the Claude Code skill schema."""
    fm, _ = _read_skill()
    for key in ("name", "description", "allowed-tools"):
        assert key in fm, f"frontmatter missing required key '{key}' (keys={list(fm)})"


def test_skill_name_matches_directory_slug() -> None:
    """`name:` must equal the directory slug to avoid silent skill-name drift."""
    fm, _ = _read_skill()
    assert fm["name"] == "corpus-forge-search", (
        f"expected name=='corpus-forge-search'; got {fm['name']!r}"
    )


def test_skill_description_is_concise_string() -> None:
    """`description` must be a non-empty string (no list / dict mishaps)."""
    fm, _ = _read_skill()
    desc = fm["description"]
    assert isinstance(desc, str) and desc.strip(), f"description not a non-empty string: {desc!r}"
    # Anthropic's skill loader caps descriptions around ~1024 chars; keep ours under
    # 400 to stay routing-cheap.  Heading off prose creep.
    assert len(desc) <= 400, f"description too long ({len(desc)} chars); keep it tight"


def test_skill_allowed_tools_lists_mcp_corpus_forge_tools() -> None:
    """`allowed-tools` must enumerate the three mcp__corpus-forge__ tools."""
    fm, _ = _read_skill()
    tools = fm["allowed-tools"]
    assert isinstance(tools, list), f"allowed-tools must be a YAML list; got {tools!r}"
    assert _EXPECTED_TOOLS.issubset(set(tools)), (
        f"allowed-tools missing entries; expected superset of {_EXPECTED_TOOLS}, got {tools}"
    )


def test_skill_body_contains_all_required_h2_sections() -> None:
    """Every required H2 heading must be present so the playbook reads consistently."""
    _, body = _read_skill()
    for heading in _REQUIRED_H2:
        pattern = re.compile(rf"^##\s+{heading}\b", re.MULTILINE)
        assert pattern.search(body), f"SKILL.md body missing required H2 '## {heading}'"


def test_skill_body_warns_about_reranker_download() -> None:
    """Body must call out the 600 MB BAAI/bge-reranker download triggered by `rerank=true`."""
    _, body = _read_skill()
    lowered = body.lower()
    assert "600" in lowered, "skill body must mention the 600 MB reranker download cost"
    assert "rerank" in lowered, "skill body must reference the rerank flag"


def test_skill_body_documents_dataset_scoping() -> None:
    """`list_datasets` -> `search(dataset=...)` is the canonical scoping pattern."""
    _, body = _read_skill()
    assert "list_datasets" in body, "skill body must reference list_datasets"
    assert "dataset" in body.lower(), "skill body must reference dataset scoping"
