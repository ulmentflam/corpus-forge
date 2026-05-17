"""Phase J / J4 — Data-curation chat surface.

Exposes the ranker / selector that drives the "what entry most needs my
help right now?" loop, plus the shared chat-loop prompt template that
the four skill assets (Claude / OpenCode / Gemini / AGENTS.md generic
recipe) keep in sync.

Importing this package is intentionally cheap and side-effect-free —
the selector instantiates no models, opens no HTTP sessions, and never
imports a backend implementation. Heavy work happens on first call to
:func:`next_curation_target` / :func:`next_curation_batch`.
"""

from __future__ import annotations

from corpus_forge.curation.prompts import CURATION_CHAT_TEMPLATE
from corpus_forge.curation.selector import (
    MISSING_METADATA_FIELDS,
    SCORE_WEIGHTS,
    CurationBatch,
    CurationTarget,
    ScoreBreakdown,
    next_curation_batch,
    next_curation_target,
)

__all__ = [
    "CURATION_CHAT_TEMPLATE",
    "MISSING_METADATA_FIELDS",
    "SCORE_WEIGHTS",
    "CurationBatch",
    "CurationTarget",
    "ScoreBreakdown",
    "next_curation_batch",
    "next_curation_target",
]
