"""I2 — Gemini CLI integration walkthrough (`docs/gemini-integration.md`).

Pins the section structure and load-bearing content references.  Mirrors
the Claude integration doc rot-detector in ``test_claude_integration_doc.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "gemini-integration.md"

_REQUIRED_H2 = (
    r"Prerequisites",
    r"Wire-up",
    r"Verify",
    r"First search",
    r"Subagent",
    r"Troubleshooting",
)


# ── File exists ───────────────────────────────────────────────────────────


def test_gemini_integration_doc_exists() -> None:
    """Walkthrough lives at the canonical path."""
    assert DOC_PATH.is_file(), f"missing {DOC_PATH}"


# ── Section structure ────────────────────────────────────────────────────


def test_gemini_integration_doc_has_all_required_h2_sections() -> None:
    """Every required H2 must be present (regex-checked)."""
    body = DOC_PATH.read_text(encoding="utf-8")
    for heading in _REQUIRED_H2:
        pattern = re.compile(rf"^##\s+{heading}\b", re.MULTILINE)
        assert pattern.search(body), f"docs/gemini-integration.md missing '## {heading}'"


# ── Load-bearing content ─────────────────────────────────────────────────


def test_gemini_integration_doc_references_mcp_config_example() -> None:
    """The Wire-up section must point at the drop-in JSON in ``examples/mcp-config/``."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "examples/mcp-config/" in body, (
        "doc must reference the examples/mcp-config/ drop-in configs"
    )


def test_gemini_integration_doc_references_extension_example() -> None:
    """The Wire-up section must point at the extension template."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "examples/gemini-extension/" in body, (
        "doc must reference the examples/gemini-extension/ template"
    )


def test_gemini_integration_doc_references_mcp_serve_command() -> None:
    """The doc must mention the actual launch command."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "corpus-forge mcp serve" in body, "doc must reference `corpus-forge mcp serve`"


def test_gemini_integration_doc_references_corpus_forge_config_env() -> None:
    """The doc must mention the ``CORPUS_FORGE_CONFIG`` env var."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "CORPUS_FORGE_CONFIG" in body, "doc must reference CORPUS_FORGE_CONFIG"


def test_gemini_integration_doc_references_settings_json() -> None:
    """Wire-up section must name the Gemini CLI settings file."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "settings.json" in body, (
        "doc must reference ~/.gemini/settings.json as the wire-up target"
    )


def test_gemini_integration_doc_references_context_file_name() -> None:
    """Doc must reference GEMINI.md as the contextFileName."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "GEMINI.md" in body, "doc must reference GEMINI.md (the Gemini CLI contextFileName)"


def test_gemini_integration_doc_warns_about_reranker_cost() -> None:
    """Troubleshooting must call out the 600 MB reranker download on first opt-in."""
    body = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "600" in body, "doc must reference the 600 MB reranker download cost"
    assert "rerank" in body, "doc must reference the rerank flag"


def test_gemini_integration_doc_mentions_migrate_and_ingest() -> None:
    """Prerequisites section must walk through ``migrate`` + ``ingest``."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "corpus-forge migrate" in body, "doc must mention 'corpus-forge migrate'"
    assert "corpus-forge ingest" in body, "doc must mention 'corpus-forge ingest'"
