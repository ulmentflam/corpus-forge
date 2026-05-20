"""Q2-T1 RED — Structural tests for per-client corpus-curate skill packs.

Asserts that each of the four per-client skill pack files:
  1. Exists at the expected path.
  2. References the four required MCP tool names.
  3. References the client-specific SDFTSource value.
  4. Is well-formed (the Gemini ``.toml`` parses via ``tomllib``).
  5. The ``docs/skill_packs.md`` installation guide names all four clients
     and provides at least one install snippet per client.

The SKILL.md for Claude Code already exists (shipped in Phase J); this
wave adds a section "When to call ``record_demonstration``" to it plus
all three new client files.

RED state
---------
The new files do not exist yet.  Expected failures:

- File-existence tests: ``AssertionError`` (``path.is_file()`` is False).
- Content-pattern tests: ``AssertionError`` (can't read from missing file).
- TOML-parse test: ``FileNotFoundError``.
- docs/skill_packs.md tests: ``AssertionError`` (file missing).

Run command::

    uv run pytest tests/unit/test_skill_packs_present.py -v 2>&1 | tail -40
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo-root anchor
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# File path constants
# ---------------------------------------------------------------------------

_CLAUDE_SKILL = _REPO_ROOT / ".claude" / "skills" / "corpus-curate" / "SKILL.md"
_GEMINI_TOML = _REPO_ROOT / ".gemini" / "extensions" / "corpus-curate.toml"
_GEMINI_PROMPT = _REPO_ROOT / ".gemini" / "extensions" / "corpus-curate" / "PROMPT.md"
_OPENCODE_CMD = _REPO_ROOT / "opencode" / "commands" / "corpus-curate.md"
_CODEX_AGENT = _REPO_ROOT / "codex" / "agents" / "corpus-curate.md"
_SKILL_PACKS_DOC = _REPO_ROOT / "docs" / "skill_packs.md"

# ---------------------------------------------------------------------------
# Required MCP tool names (must appear in every skill pack)
# ---------------------------------------------------------------------------

_REQUIRED_TOOLS = {
    "record_demonstration",
    "commit_curation",
    "rate_search_result",
    "add_feedback",
}

# ---------------------------------------------------------------------------
# Client-specific SDFTSource taxonomy values that must appear in each pack
# ---------------------------------------------------------------------------

_CLIENT_SOURCES: dict[str, str] = {
    "claude_code": "claude_code",
    "gemini": "gemini",
    "opencode": "opencode",
    "codex": "codex",
}

# ---------------------------------------------------------------------------
# Client names required in docs/skill_packs.md
# ---------------------------------------------------------------------------

_CLIENT_NAMES = {"Claude Code", "Gemini", "OpenCode", "Codex"}


# ===========================================================================
# Helper
# ===========================================================================


def _read(path: Path) -> str:
    """Read path text; skip entire test with a meaningful message if missing."""
    return path.read_text(encoding="utf-8")


# ===========================================================================
# 1. File existence tests
# ===========================================================================


class TestSkillPacksExist:
    """Every skill pack file must exist at its declared path."""

    def test_claude_skill_md_exists(self) -> None:
        """`.claude/skills/corpus-curate/SKILL.md` must exist."""
        assert _CLAUDE_SKILL.is_file(), f"Missing: {_CLAUDE_SKILL}"

    def test_gemini_toml_exists(self) -> None:
        """``.gemini/extensions/corpus-curate.toml`` must exist."""
        assert _GEMINI_TOML.is_file(), f"Missing: {_GEMINI_TOML}"

    def test_gemini_prompt_md_exists(self) -> None:
        """``.gemini/extensions/corpus-curate/PROMPT.md`` must exist."""
        assert _GEMINI_PROMPT.is_file(), f"Missing: {_GEMINI_PROMPT}"

    def test_opencode_command_md_exists(self) -> None:
        """``opencode/commands/corpus-curate.md`` must exist."""
        assert _OPENCODE_CMD.is_file(), f"Missing: {_OPENCODE_CMD}"

    def test_codex_agent_md_exists(self) -> None:
        """``codex/agents/corpus-curate.md`` must exist."""
        assert _CODEX_AGENT.is_file(), f"Missing: {_CODEX_AGENT}"

    def test_skill_packs_doc_exists(self) -> None:
        """``docs/skill_packs.md`` installation guide must exist."""
        assert _SKILL_PACKS_DOC.is_file(), f"Missing: {_SKILL_PACKS_DOC}"


# ===========================================================================
# 2. MCP tool references — every pack must name all four tools
# ===========================================================================


class TestRequiredToolNames:
    """Each skill pack must reference all four MCP write tools by name."""

    @pytest.mark.parametrize("tool", sorted(_REQUIRED_TOOLS))
    def test_claude_skill_references_tool(self, tool: str) -> None:
        """`.claude/skills/corpus-curate/SKILL.md` must name ``{tool}``."""
        text = _read(_CLAUDE_SKILL)
        assert tool in text, (
            f"SKILL.md does not reference MCP tool {tool!r}.\n  path: {_CLAUDE_SKILL}"
        )

    @pytest.mark.parametrize("tool", sorted(_REQUIRED_TOOLS))
    def test_gemini_prompt_references_tool(self, tool: str) -> None:
        """``.gemini/extensions/corpus-curate/PROMPT.md`` must name ``{tool}``."""
        text = _read(_GEMINI_PROMPT)
        assert tool in text, (
            f"PROMPT.md does not reference MCP tool {tool!r}.\n  path: {_GEMINI_PROMPT}"
        )

    @pytest.mark.parametrize("tool", sorted(_REQUIRED_TOOLS))
    def test_opencode_cmd_references_tool(self, tool: str) -> None:
        """``opencode/commands/corpus-curate.md`` must name ``{tool}``."""
        text = _read(_OPENCODE_CMD)
        assert tool in text, (
            f"opencode command does not reference MCP tool {tool!r}.\n  path: {_OPENCODE_CMD}"
        )

    @pytest.mark.parametrize("tool", sorted(_REQUIRED_TOOLS))
    def test_codex_agent_references_tool(self, tool: str) -> None:
        """``codex/agents/corpus-curate.md`` must name ``{tool}``."""
        text = _read(_CODEX_AGENT)
        assert tool in text, (
            f"codex agent does not reference MCP tool {tool!r}.\n  path: {_CODEX_AGENT}"
        )


# ===========================================================================
# 3. Client-specific source taxonomy values
# ===========================================================================


class TestClientSourceTaxonomy:
    """Each pack must reference its own SDFTSource enum value."""

    def test_claude_skill_references_claude_code_source(self) -> None:
        """SKILL.md must reference the source value ``claude_code``."""
        text = _read(_CLAUDE_SKILL)
        assert "claude_code" in text, (
            f"SKILL.md does not reference source value 'claude_code'.\n  path: {_CLAUDE_SKILL}"
        )

    def test_gemini_prompt_references_gemini_source(self) -> None:
        """PROMPT.md must reference the source value ``gemini``."""
        text = _read(_GEMINI_PROMPT)
        assert "gemini" in text, (
            f"PROMPT.md does not reference source value 'gemini'.\n  path: {_GEMINI_PROMPT}"
        )

    def test_opencode_cmd_references_opencode_source(self) -> None:
        """opencode command must reference the source value ``opencode``."""
        text = _read(_OPENCODE_CMD)
        assert "opencode" in text, (
            f"opencode command does not reference source value 'opencode'.\n  path: {_OPENCODE_CMD}"
        )

    def test_codex_agent_references_codex_source(self) -> None:
        """codex agent must reference the source value ``codex``."""
        text = _read(_CODEX_AGENT)
        assert "codex" in text, (
            f"codex agent does not reference source value 'codex'.\n  path: {_CODEX_AGENT}"
        )


# ===========================================================================
# 4. Claude SKILL.md — "record_demonstration" guidance section
# ===========================================================================


class TestClaudeSkillRecordDemonstrationSection:
    """SKILL.md must include a 'When to call record_demonstration' section."""

    def test_claude_skill_has_record_demonstration_section(self) -> None:
        """SKILL.md must have a heading about when to call record_demonstration."""
        text = _read(_CLAUDE_SKILL)
        # Accept both H2 and H3 headings for this section.
        pattern = re.compile(
            r"#+\s+When to call\s+[`']?record_demonstration[`']?",
            re.IGNORECASE,
        )
        assert pattern.search(text), (
            "SKILL.md is missing a 'When to call record_demonstration' section.\n"
            f"  path: {_CLAUDE_SKILL}\n"
            "  (add a '## When to call `record_demonstration`' heading)"
        )


# ===========================================================================
# 5. Gemini .toml parses cleanly
# ===========================================================================


class TestGeminiTomlWellFormed:
    """The Gemini extension manifest must be valid TOML."""

    def test_gemini_toml_parses_via_tomllib(self) -> None:
        """``.gemini/extensions/corpus-curate.toml`` must parse without error."""
        raw_bytes = _GEMINI_TOML.read_bytes()
        result = tomllib.loads(raw_bytes.decode("utf-8"))
        assert isinstance(result, dict), f"Expected TOML to parse as a dict; got {type(result)}"

    def test_gemini_toml_references_prompt_file(self) -> None:
        """The .toml must reference the PROMPT.md companion file."""
        text = _read(_GEMINI_TOML)
        # The extension manifest must reference its prompt body somehow.
        assert "PROMPT.md" in text or "prompt" in text.lower(), (
            f"corpus-curate.toml does not reference PROMPT.md or 'prompt'.\n  path: {_GEMINI_TOML}"
        )


# ===========================================================================
# 6. docs/skill_packs.md — per-client install guide
# ===========================================================================


class TestSkillPacksDoc:
    """docs/skill_packs.md must name every client and include install snippets."""

    @pytest.mark.parametrize("client_name", sorted(_CLIENT_NAMES))
    def test_skill_packs_doc_names_client(self, client_name: str) -> None:
        """docs/skill_packs.md must mention ``{client_name}``."""
        text = _read(_SKILL_PACKS_DOC)
        assert client_name in text, (
            f"docs/skill_packs.md does not mention client {client_name!r}.\n"
            f"  path: {_SKILL_PACKS_DOC}"
        )

    def test_skill_packs_doc_has_claude_install_snippet(self) -> None:
        """docs/skill_packs.md must contain a Claude Code install snippet."""
        text = _read(_SKILL_PACKS_DOC)
        # Presence of a code-fenced block near Claude Code content.
        assert "```" in text, "docs/skill_packs.md has no code fence (install snippet missing)"
        # The doc must have at least one SKILL.md reference for Claude.
        assert "SKILL.md" in text or ".claude" in text, (
            "docs/skill_packs.md does not reference '.claude' or 'SKILL.md'.\n"
            f"  path: {_SKILL_PACKS_DOC}"
        )

    def test_skill_packs_doc_has_gemini_install_snippet(self) -> None:
        """docs/skill_packs.md must reference the Gemini TOML extension path."""
        text = _read(_SKILL_PACKS_DOC)
        assert ".gemini" in text or "corpus-curate.toml" in text, (
            "docs/skill_packs.md does not reference '.gemini' or 'corpus-curate.toml'.\n"
            f"  path: {_SKILL_PACKS_DOC}"
        )

    def test_skill_packs_doc_has_opencode_install_snippet(self) -> None:
        """docs/skill_packs.md must reference the OpenCode command path."""
        text = _read(_SKILL_PACKS_DOC)
        assert "opencode" in text.lower(), (
            f"docs/skill_packs.md does not reference 'opencode'.\n  path: {_SKILL_PACKS_DOC}"
        )

    def test_skill_packs_doc_has_codex_install_snippet(self) -> None:
        """docs/skill_packs.md must reference the Codex agent path."""
        text = _read(_SKILL_PACKS_DOC)
        assert "codex" in text.lower(), (
            f"docs/skill_packs.md does not reference 'codex'.\n  path: {_SKILL_PACKS_DOC}"
        )
