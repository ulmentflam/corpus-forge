"""I2 — Gemini CLI extension context file (`examples/gemini-extension/GEMINI.md`).

Pins that GEMINI.md (the system-instruction analogue for Gemini CLI) mentions
all five read tools exposed by the corpus-forge MCP server and the citation
rules.  Mirrors the spirit of the Claude skill frontmatter / body tests.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GEMINI_MD_PATH = REPO_ROOT / "examples" / "gemini-extension" / "GEMINI.md"

_REQUIRED_TOOLS = (
    "search",
    "get_chunk",
    "list_datasets",
    "render_conversation",
    "list_chat_templates",
)


# ── File exists ───────────────────────────────────────────────────────────


def test_gemini_md_exists() -> None:
    """GEMINI.md lives at the documented path."""
    assert GEMINI_MD_PATH.is_file(), f"missing {GEMINI_MD_PATH}"


# ── All five read tools mentioned ────────────────────────────────────────


def test_gemini_md_mentions_search_tool() -> None:
    """``search`` must be referenced so the model knows to call it."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    assert "search" in body, "GEMINI.md must mention the 'search' tool"


def test_gemini_md_mentions_get_chunk_tool() -> None:
    """``get_chunk`` must be referenced for full-context retrieval."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    assert "get_chunk" in body, "GEMINI.md must mention the 'get_chunk' tool"


def test_gemini_md_mentions_list_datasets_tool() -> None:
    """``list_datasets`` must be referenced for corpus survey step."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    assert "list_datasets" in body, "GEMINI.md must mention the 'list_datasets' tool"


def test_gemini_md_mentions_render_conversation_tool() -> None:
    """``render_conversation`` must be referenced — it's a first-class read tool."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    assert "render_conversation" in body, "GEMINI.md must mention the 'render_conversation' tool"


def test_gemini_md_mentions_list_chat_templates_tool() -> None:
    """``list_chat_templates`` must be referenced — it's a first-class read tool."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    assert "list_chat_templates" in body, "GEMINI.md must mention the 'list_chat_templates' tool"


def test_gemini_md_mentions_all_five_tools() -> None:
    """Consolidated check: all five tools referenced in a single pass."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    missing = [t for t in _REQUIRED_TOOLS if t not in body]
    assert not missing, f"GEMINI.md missing references to tools: {missing}"


# ── Citation rules ───────────────────────────────────────────────────────


def test_gemini_md_mentions_citation_rules() -> None:
    """GEMINI.md must spell out the citation format."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    # The canonical citation template includes source_uri and title.
    assert "source_uri" in body, "GEMINI.md must reference source_uri in the citation format"
    assert "citation" in body.lower(), "GEMINI.md must mention citation rules"


def test_gemini_md_citation_format_includes_title() -> None:
    """Citation format must include {title} so answers are self-descriptive."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8")
    assert "title" in body, "GEMINI.md citation format must reference {title}"


def test_gemini_md_warns_about_reranker_download() -> None:
    """Body must call out the 600 MB cross-encoder download triggered by rerank=true."""
    body = GEMINI_MD_PATH.read_text(encoding="utf-8").lower()
    assert "600" in body, "GEMINI.md must mention the 600 MB reranker download cost"
    assert "rerank" in body, "GEMINI.md must reference the rerank flag"
