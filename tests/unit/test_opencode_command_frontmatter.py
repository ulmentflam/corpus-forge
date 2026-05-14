"""I1 — Command file (`.opencode/command/corpus-forge-search.md`).

The command file is what OpenCode reads to decide *when* to invoke the
corpus-forge MCP tools (OpenCode's equivalent of a Claude skill). Tests pin:

1. **Frontmatter** parses as YAML and declares ``name``, ``description``,
   ``allowed-tools`` (the five MCP read tools, prefixed
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
COMMAND_PATH = REPO_ROOT / ".opencode" / "command" / "corpus-forge-search.md"

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
    "mcp__corpus-forge__render_conversation",
    "mcp__corpus-forge__list_chat_templates",
}


def _read_command() -> tuple[dict, str]:
    """Split command file into (frontmatter dict, body) — fails loudly on shape drift."""
    raw = COMMAND_PATH.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    assert m, "command file missing YAML frontmatter block delimited by '---'"
    fm = yaml.safe_load(m.group(1))
    assert isinstance(fm, dict), f"frontmatter not a mapping: {fm!r}"
    return fm, m.group(2)


def test_command_file_exists() -> None:
    """Command lives at the well-known `.opencode/command/<slug>.md` path."""
    assert COMMAND_PATH.is_file(), f"missing {COMMAND_PATH}"


def test_command_frontmatter_parses_as_yaml() -> None:
    """Frontmatter must be valid YAML so OpenCode's parser accepts it."""
    fm, _ = _read_command()
    assert isinstance(fm, dict)


def test_command_frontmatter_has_required_keys() -> None:
    """`name`, `description`, `allowed-tools` are required by the OpenCode command schema."""
    fm, _ = _read_command()
    for key in ("name", "description", "allowed-tools"):
        assert key in fm, f"frontmatter missing required key '{key}' (keys={list(fm)})"


def test_command_name_matches_filename_slug() -> None:
    """`name:` must equal the filename slug to avoid silent command-name drift."""
    fm, _ = _read_command()
    assert fm["name"] == "corpus-forge-search", (
        f"expected name=='corpus-forge-search'; got {fm['name']!r}"
    )


def test_command_description_is_concise_string() -> None:
    """`description` must be a non-empty string (no list / dict mishaps)."""
    fm, _ = _read_command()
    desc = fm["description"]
    assert isinstance(desc, str) and desc.strip(), f"description not a non-empty string: {desc!r}"
    # Keep descriptions routing-cheap.
    assert len(desc) <= 400, f"description too long ({len(desc)} chars); keep it tight"


def test_command_allowed_tools_lists_five_mcp_corpus_forge_tools() -> None:
    """`allowed-tools` must enumerate all five mcp__corpus-forge__ read tools."""
    fm, _ = _read_command()
    tools = fm["allowed-tools"]
    assert isinstance(tools, list), f"allowed-tools must be a YAML list; got {tools!r}"
    assert _EXPECTED_TOOLS.issubset(set(tools)), (
        f"allowed-tools missing entries; expected superset of {_EXPECTED_TOOLS}, got {tools}"
    )


def test_command_body_contains_all_required_h2_sections() -> None:
    """Every required H2 heading must be present so the playbook reads consistently."""
    _, body = _read_command()
    for heading in _REQUIRED_H2:
        pattern = re.compile(rf"^##\s+{heading}\b", re.MULTILINE)
        assert pattern.search(body), f"command body missing required H2 '## {heading}'"


def test_command_body_warns_about_reranker_download() -> None:
    """Body must call out the 600 MB BAAI/bge-reranker download triggered by `rerank=true`."""
    _, body = _read_command()
    lowered = body.lower()
    assert "600" in lowered, "command body must mention the 600 MB reranker download cost"
    assert "rerank" in lowered, "command body must reference the rerank flag"


def test_command_body_documents_dataset_scoping() -> None:
    """`list_datasets` -> `search(dataset=...)` is the canonical scoping pattern."""
    _, body = _read_command()
    assert "list_datasets" in body, "command body must reference list_datasets"
    assert "dataset" in body.lower(), "command body must reference dataset scoping"
