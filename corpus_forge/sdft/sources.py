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

    @classmethod
    def is_chat_client(cls, source: str) -> bool:
        """Return True if *source* identifies a chat-client SDFT origin.

        Chat-client sources are the four external AI coding assistants that
        can capture demonstrations via the per-client skill packs shipped in
        Phase Q Wave 2.  All other sources (capture-event hooks) return False.

        Accepts both plain strings and ``SDFTSource`` enum members.
        Unknown values return False rather than raising.
        """
        return source in {"claude_code", "gemini", "opencode", "codex"}
