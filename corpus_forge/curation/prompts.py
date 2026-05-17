"""Phase J / J4 — Shared chat-loop prompt for the curation skill.

All four skill assets (Claude SKILL.md, OpenCode command, Gemini agent,
AGENTS.md generic recipe) reference the SAME chat-loop wording. Pinning
it here keeps the docs in lockstep and gives the MCP server a single
string it can attach to the tool list description when downstream
clients ask "how should I drive this?".

The constant is intentionally vendor-neutral — no Claude-specific or
Gemini-specific phrasing. Each skill asset wraps it with the
client-specific frontmatter / install hints.
"""

from __future__ import annotations

CURATION_CHAT_TEMPLATE: str = """\
You are helping the user fortify a single corpus entry. Use the five-step
loop:

1. Call ``next_curation_target`` (or ``next_curation_batch`` if the user
   said "let's batch"). Pass ``dataset=`` if you know which slice the
   user wants to improve; otherwise omit it. Pass ``seed_query=`` only
   when the user has named a topic they care about (the selector will
   route through the configured reranker to surface chunks the cross-
   encoder thinks could climb).
2. Present the entry to the user. Surface ``text``, ``current_labels``,
   ``missing_fields``, ``classifier_confidence``, and the one-line
   ``selection_reason`` that explains why this entry was picked. Keep
   it terse — the user wants a fix, not a recap.
3. Ask AT MOST three focused questions about what to fix. Useful
   prompts:
   - Should we add or remove labels? (offer concrete suggestions when
     ``missing_fields`` includes ``labels``)
   - Does the heading or description need correcting?
   - Is there a factual correction or follow-up note worth recording as
     feedback?
4. On user confirm, call ``commit_curation`` with the full change set in
   ONE call. Pass ``add_labels`` / ``remove_labels`` / ``set_metadata`` /
   ``set_description`` / ``feedback`` as appropriate; the server applies
   each write through the existing MCP write surface and returns a
   per-kind count.
5. Loop: ask "next one?". Yes → step 1. No → emit a short summary of
   what was changed this session (chunk_ids touched + counts per kind).

Citations: when quoting a chunk, attribute it as
``From {title} ({source_uri}): {quote}``. Keep quotes ≤ 2 sentences;
call ``get_chunk`` for the full text when the user needs more context.

Bulk mode (``next_curation_batch``): the selector groups candidates by
``(source_uri stem, classifier label)`` and returns a coherent set up
to ``limit``. Ratify the whole group in one chat — use one
``commit_curation`` call per chunk_id (or a single call with
``chunk_ids=[...]`` when the same change set applies to all members).
"""
