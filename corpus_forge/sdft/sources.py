"""Phase Q Wave 1 — SDFT source taxonomy.

Defines the SDFTSource enum, which enumerates every signal type that
can generate a supervised demonstration pair for fine-tuning.
"""

from __future__ import annotations

from enum import StrEnum


class SDFTSource(StrEnum):
    """Enumeration of SDFT demonstration sources.

    Each value identifies the subsystem that produced the
    teacher→student demonstration pair.
    """

    CURATION_COMMIT = "curation_commit"
    RATE_SEARCH_RESULT = "rate_search_result"
    RECORD_DEMONSTRATION = "record_demonstration"
    CLI_FEEDBACK = "cli_feedback"
    CLAUDE_CODE = "claude_code"
    GEMINI = "gemini"
    OPENCODE = "opencode"
    CODEX = "codex"
