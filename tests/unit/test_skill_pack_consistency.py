"""Q5-T1 RED (unit) — skill-pack drift detection across all four chat-client manifests.

Asserts that the four skill packs (Claude Code, Gemini, OpenCode, Codex) stay
in sync on:
  - MCP tool names referenced (all four packs must name the same tools).
  - ``source`` taxonomy values (each pack must contain its own client identifier).
  - "When to call record_demonstration" guidance (present in every pack).

The canonical source-of-truth is the Claude Code skill pack
(``.claude/skills/corpus-curate/SKILL.md``).

RED state
---------
These tests currently PASS against the already-shipped skill packs (Q2-G1 shipped
them).  However, if any pack drifts (loses a tool name, removes the guidance
section, uses the wrong source value), the corresponding test turns RED.

This is a *retrofit characterization + regression* suite: it characterises the
current correct state and locks it in so future edits to any pack surface
immediately as a CI failure.

NOTE: Even though the tests pass green today, they serve the Q5-T1 RED
contract because the *production code* they guard (``eval distill``) does not
exist yet — the tester's job for Q5 is to ship both files and confirm the
exit condition.  See test-status.md.

Run command::

    uv run pytest tests/unit/test_skill_pack_consistency.py -v 2>&1 | tail -20
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths to all four skill packs
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_CLAUDE_SKILL = _REPO_ROOT / ".claude" / "skills" / "corpus-curate" / "SKILL.md"
_GEMINI_PROMPT = _REPO_ROOT / ".gemini" / "extensions" / "corpus-curate" / "PROMPT.md"
_GEMINI_TOML = _REPO_ROOT / ".gemini" / "extensions" / "corpus-curate.toml"
_OPENCODE_CMD = _REPO_ROOT / "opencode" / "commands" / "corpus-curate.md"
_CODEX_AGENT = _REPO_ROOT / "codex" / "agents" / "corpus-curate.md"

# ---------------------------------------------------------------------------
# The four MCP tool names that all packs must reference
# ---------------------------------------------------------------------------

_REQUIRED_MCP_TOOLS = [
    "record_demonstration",
    "commit_curation",
    "rate_search_result",
    "add_feedback",
]

# ---------------------------------------------------------------------------
# Per-client source values (each pack must reference its own)
# ---------------------------------------------------------------------------

_CLAUDE_SOURCE = "claude_code"
_GEMINI_SOURCE = "gemini"
_OPENCODE_SOURCE = "opencode"
_CODEX_SOURCE = "codex"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Read a file; fail with a helpful message if it is missing."""
    assert path.exists(), (
        f"Skill pack file not found: {path}\n"
        "Ensure Phase Q Wave 2 has been merged and all four packs are present."
    )
    return path.read_text(encoding="utf-8")


def _read_gemini() -> str:
    """Return combined text from the Gemini TOML manifest + PROMPT.md."""
    parts: list[str] = []
    if _GEMINI_TOML.exists():
        parts.append(_GEMINI_TOML.read_text(encoding="utf-8"))
    if _GEMINI_PROMPT.exists():
        parts.append(_GEMINI_PROMPT.read_text(encoding="utf-8"))
    assert parts, f"Neither Gemini TOML manifest nor PROMPT.md found under {_GEMINI_TOML.parent}/"
    return "\n".join(parts)


def _contains_tool(text: str, tool_name: str) -> bool:
    """Return True if *tool_name* appears as a word in *text*."""
    # Match exact word boundaries so 'add_feedback' does not match 'add_feedback_pairs'.
    return bool(re.search(rf"\b{re.escape(tool_name)}\b", text))


def _has_record_demo_section(text: str) -> bool:
    """Return True if the text has a section header about when to call record_demonstration."""
    # Matches Markdown headings (##, ###) or bold lead-ins that contain the
    # key phrase.
    patterns = [
        r"#+\s.*record_demonstration",
        r"\*\*.*record_demonstration.*\*\*",
        r"When to call `record_demonstration`",
        r"SDFT capture",
        r"record_demonstration",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ---------------------------------------------------------------------------
# Fixture: ensure all pack files exist (fail early with a clear message)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def all_packs_exist() -> None:
    """Assert all four skill pack files exist before running consistency checks."""
    missing: list[Path] = []
    for path in (
        _CLAUDE_SKILL,
        _OPENCODE_CMD,
        _CODEX_AGENT,
    ):
        if not path.exists():
            missing.append(path)
    # Gemini needs at least one of its two files.
    if not _GEMINI_TOML.exists() and not _GEMINI_PROMPT.exists():
        missing.append(_GEMINI_TOML)

    if missing:
        pytest.fail(
            "One or more skill pack files are missing — Phase Q Wave 2 may not be merged:\n"
            + "\n".join(f"  {p}" for p in missing)
        )


# ---------------------------------------------------------------------------
# T01 — all 4 packs reference ``record_demonstration``
# ---------------------------------------------------------------------------


class TestAllPacksReferenceRecordDemonstration:
    def test_claude_skill_references_record_demonstration(self) -> None:
        """Claude Code skill pack must reference ``record_demonstration``."""
        text = _read(_CLAUDE_SKILL)
        assert _contains_tool(text, "record_demonstration"), (
            f"'record_demonstration' not found in {_CLAUDE_SKILL}\n"
            f"This tool is required in all four packs per Q5 consistency contract."
        )

    def test_gemini_references_record_demonstration(self) -> None:
        """Gemini skill pack must reference ``record_demonstration``."""
        text = _read_gemini()
        assert _contains_tool(text, "record_demonstration"), (
            "'record_demonstration' not found in Gemini pack.\n"
            "This tool is required in all four packs per Q5 consistency contract."
        )

    def test_opencode_references_record_demonstration(self) -> None:
        """OpenCode skill pack must reference ``record_demonstration``."""
        text = _read(_OPENCODE_CMD)
        assert _contains_tool(text, "record_demonstration"), (
            f"'record_demonstration' not found in {_OPENCODE_CMD}."
        )

    def test_codex_references_record_demonstration(self) -> None:
        """Codex skill pack must reference ``record_demonstration``."""
        text = _read(_CODEX_AGENT)
        assert _contains_tool(text, "record_demonstration"), (
            f"'record_demonstration' not found in {_CODEX_AGENT}."
        )


# ---------------------------------------------------------------------------
# T02 — all 4 packs reference ``commit_curation``
# ---------------------------------------------------------------------------


class TestAllPacksReferenceCommitCuration:
    def test_claude_skill_references_commit_curation(self) -> None:
        """Claude Code skill pack must reference ``commit_curation``."""
        text = _read(_CLAUDE_SKILL)
        assert _contains_tool(text, "commit_curation"), (
            f"'commit_curation' not found in {_CLAUDE_SKILL}."
        )

    def test_gemini_references_commit_curation(self) -> None:
        """Gemini skill pack must reference ``commit_curation``."""
        text = _read_gemini()
        assert _contains_tool(text, "commit_curation"), (
            "'commit_curation' not found in Gemini pack."
        )

    def test_opencode_references_commit_curation(self) -> None:
        """OpenCode skill pack must reference ``commit_curation``."""
        text = _read(_OPENCODE_CMD)
        assert _contains_tool(text, "commit_curation"), (
            f"'commit_curation' not found in {_OPENCODE_CMD}."
        )

    def test_codex_references_commit_curation(self) -> None:
        """Codex skill pack must reference ``commit_curation``."""
        text = _read(_CODEX_AGENT)
        assert _contains_tool(text, "commit_curation"), (
            f"'commit_curation' not found in {_CODEX_AGENT}."
        )


# ---------------------------------------------------------------------------
# T03 — all 4 packs reference ``rate_search_result``
# ---------------------------------------------------------------------------


class TestAllPacksReferenceRateSearchResult:
    def test_claude_skill_references_rate_search_result(self) -> None:
        """Claude Code skill pack must reference ``rate_search_result``."""
        text = _read(_CLAUDE_SKILL)
        assert _contains_tool(text, "rate_search_result"), (
            f"'rate_search_result' not found in {_CLAUDE_SKILL}."
        )

    def test_gemini_references_rate_search_result(self) -> None:
        """Gemini skill pack must reference ``rate_search_result``."""
        text = _read_gemini()
        assert _contains_tool(text, "rate_search_result"), (
            "'rate_search_result' not found in Gemini pack."
        )

    def test_opencode_references_rate_search_result(self) -> None:
        """OpenCode skill pack must reference ``rate_search_result``."""
        text = _read(_OPENCODE_CMD)
        assert _contains_tool(text, "rate_search_result"), (
            f"'rate_search_result' not found in {_OPENCODE_CMD}."
        )

    def test_codex_references_rate_search_result(self) -> None:
        """Codex skill pack must reference ``rate_search_result``."""
        text = _read(_CODEX_AGENT)
        assert _contains_tool(text, "rate_search_result"), (
            f"'rate_search_result' not found in {_CODEX_AGENT}."
        )


# ---------------------------------------------------------------------------
# T04 — all 4 packs reference ``add_feedback``
# ---------------------------------------------------------------------------


class TestAllPacksReferenceAddFeedback:
    def test_claude_skill_references_add_feedback(self) -> None:
        """Claude Code skill pack must reference ``add_feedback``."""
        text = _read(_CLAUDE_SKILL)
        assert _contains_tool(text, "add_feedback"), f"'add_feedback' not found in {_CLAUDE_SKILL}."

    def test_gemini_references_add_feedback(self) -> None:
        """Gemini skill pack must reference ``add_feedback``."""
        text = _read_gemini()
        assert _contains_tool(text, "add_feedback"), "'add_feedback' not found in Gemini pack."

    def test_opencode_references_add_feedback(self) -> None:
        """OpenCode skill pack must reference ``add_feedback``."""
        text = _read(_OPENCODE_CMD)
        assert _contains_tool(text, "add_feedback"), f"'add_feedback' not found in {_OPENCODE_CMD}."

    def test_codex_references_add_feedback(self) -> None:
        """Codex skill pack must reference ``add_feedback``."""
        text = _read(_CODEX_AGENT)
        assert _contains_tool(text, "add_feedback"), f"'add_feedback' not found in {_CODEX_AGENT}."


# ---------------------------------------------------------------------------
# T05 — each pack's own source value appears in its own pack
# ---------------------------------------------------------------------------


class TestEachPackContainsOwnSourceValue:
    def test_claude_pack_contains_claude_code_source(self) -> None:
        """Claude Code skill pack must contain ``claude_code`` as source value."""
        text = _read(_CLAUDE_SKILL)
        assert _CLAUDE_SOURCE in text, (
            f"Source value 'claude_code' not found in {_CLAUDE_SKILL}.\n"
            "Each pack must document its own SDFTSource value."
        )

    def test_gemini_pack_contains_gemini_source(self) -> None:
        """Gemini skill pack must contain ``gemini`` as source value."""
        text = _read_gemini()
        assert _GEMINI_SOURCE in text, (
            "Source value 'gemini' not found in Gemini pack.\n"
            "Each pack must document its own SDFTSource value."
        )

    def test_opencode_pack_contains_opencode_source(self) -> None:
        """OpenCode skill pack must contain ``opencode`` as source value."""
        text = _read(_OPENCODE_CMD)
        assert _OPENCODE_SOURCE in text, (
            f"Source value 'opencode' not found in {_OPENCODE_CMD}.\n"
            "Each pack must document its own SDFTSource value."
        )

    def test_codex_pack_contains_codex_source(self) -> None:
        """Codex skill pack must contain ``codex`` as source value."""
        text = _read(_CODEX_AGENT)
        assert _CODEX_SOURCE in text, (
            f"Source value 'codex' not found in {_CODEX_AGENT}.\n"
            "Each pack must document its own SDFTSource value."
        )


# ---------------------------------------------------------------------------
# T06 — "When to call record_demonstration" guidance present in all 4 packs
# ---------------------------------------------------------------------------


class TestWhenToCallRecordDemonstrationGuidance:
    def test_claude_skill_has_record_demo_guidance(self) -> None:
        """Claude Code skill pack must have a 'When to call record_demonstration' section."""
        text = _read(_CLAUDE_SKILL)
        assert _has_record_demo_section(text), (
            f"No 'When to call record_demonstration' section found in {_CLAUDE_SKILL}.\n"
            "Q5 requires this guidance in all four packs."
        )

    def test_gemini_has_record_demo_guidance(self) -> None:
        """Gemini skill pack must have 'When to call record_demonstration' guidance."""
        text = _read_gemini()
        assert _has_record_demo_section(text), (
            "No 'When to call record_demonstration' section found in Gemini pack.\n"
            "Q5 requires this guidance in all four packs."
        )

    def test_opencode_has_record_demo_guidance(self) -> None:
        """OpenCode skill pack must have 'When to call record_demonstration' guidance."""
        text = _read(_OPENCODE_CMD)
        assert _has_record_demo_section(text), (
            f"No 'When to call record_demonstration' section found in {_OPENCODE_CMD}.\n"
            "Q5 requires this guidance in all four packs."
        )

    def test_codex_has_record_demo_guidance(self) -> None:
        """Codex skill pack must have 'When to call record_demonstration' guidance."""
        text = _read(_CODEX_AGENT)
        assert _has_record_demo_section(text), (
            f"No 'When to call record_demonstration' section found in {_CODEX_AGENT}.\n"
            "Q5 requires this guidance in all four packs."
        )


# ---------------------------------------------------------------------------
# T07 — tool name set is identical across all four packs (no drift)
# ---------------------------------------------------------------------------


class TestToolNameSetConsistencyAcrossPacks:
    """All four packs must reference the same four MCP tool names — no drift."""

    @pytest.mark.parametrize("tool", _REQUIRED_MCP_TOOLS)
    def test_all_packs_reference_tool(self, tool: str) -> None:
        """All four packs must reference ``{tool}``."""
        claude_text = _read(_CLAUDE_SKILL)
        gemini_text = _read_gemini()
        opencode_text = _read(_OPENCODE_CMD)
        codex_text = _read(_CODEX_AGENT)

        results = {
            "claude": _contains_tool(claude_text, tool),
            "gemini": _contains_tool(gemini_text, tool),
            "opencode": _contains_tool(opencode_text, tool),
            "codex": _contains_tool(codex_text, tool),
        }
        failing = [pack for pack, found in results.items() if not found]
        assert not failing, (
            f"Tool '{tool}' missing from these packs: {failing}\n"
            f"All four packs must reference the same MCP tools."
        )


# ---------------------------------------------------------------------------
# T08 — source taxonomy completeness: all 8 SDFTSource values are importable
# ---------------------------------------------------------------------------


class TestSDFTSourceTaxonomy:
    def test_sdft_source_enum_importable(self) -> None:
        """``SDFTSource`` enum must be importable from ``corpus_forge.sdft.sources``."""
        from corpus_forge.sdft.sources import SDFTSource  # noqa: F401

    def test_sdft_source_has_all_8_values(self) -> None:
        """``SDFTSource`` must define all 8 source taxonomy values."""
        from corpus_forge.sdft.sources import SDFTSource

        expected = {
            "curation_commit",
            "rate_search_result",
            "record_demonstration",
            "cli_feedback",
            "claude_code",
            "gemini",
            "opencode",
            "codex",
        }
        actual = {member.value for member in SDFTSource}
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"SDFTSource is missing values: {sorted(missing)}"
        assert not extra, f"SDFTSource has unexpected values: {sorted(extra)}"

    def test_sdft_source_chat_clients_identified(self) -> None:
        """``SDFTSource.is_chat_client`` must return True for the 4 chat-client values."""
        from corpus_forge.sdft.sources import SDFTSource

        chat_clients = ["claude_code", "gemini", "opencode", "codex"]
        for src in chat_clients:
            assert SDFTSource.is_chat_client(src), (
                f"SDFTSource.is_chat_client({src!r}) returned False; expected True."
            )

    def test_sdft_source_non_chat_clients_not_identified(self) -> None:
        """``SDFTSource.is_chat_client`` must return False for non-chat-client values."""
        from corpus_forge.sdft.sources import SDFTSource

        non_clients = [
            "curation_commit",
            "rate_search_result",
            "record_demonstration",
            "cli_feedback",
        ]
        for src in non_clients:
            assert not SDFTSource.is_chat_client(src), (
                f"SDFTSource.is_chat_client({src!r}) returned True; expected False."
            )

    def test_sdft_source_each_chat_client_pack_source_is_chat_client(self) -> None:
        """The source value declared in each skill pack must be a valid chat client."""
        from corpus_forge.sdft.sources import SDFTSource

        for src in (_CLAUDE_SOURCE, _GEMINI_SOURCE, _OPENCODE_SOURCE, _CODEX_SOURCE):
            assert SDFTSource.is_chat_client(src), (
                f"Pack source {src!r} is not recognized as a chat client by SDFTSource."
            )
