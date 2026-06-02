"""Two-pass LLM-driven AGENTS.md synthesis — T4 (redirected).

The synthesizer produces TWO artifacts in two separate LLM passes so
the prompts can't bleed into each other:

1. **Private** (``.corpus-agents/AGENTS.md``) — corpus-grounded, with
   ``chunk_id`` citations. The full synthesis: language/tool detection
   + local sampling + cross-corpus pattern hits + anti-pattern
   warnings.
2. **Shareable** (``.corpus-agents/shareable.md``) — sanitized subset.
   Citation-free. References ONLY: (a) language/tool defaults this
   project actually uses, (b) conventions inferred from THIS project's
   own local sampling, (c) factual project metadata. The shareable
   prompt is explicit about the sanitization contract.

The LLM interface is the simplest possible: ``Callable[[str], str]``.
This keeps tests pure (no HTTP) and keeps the production wrapper —
which speaks ``code_enricher.local_url`` or ``remote_url`` — out of
this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.agents.cross_corpus import CrossCorpusPatterns
    from corpus_forge.agents.detector import ProjectContext
    from corpus_forge.agents.sampler import LocalPatterns


# ─────────────────────────────────────────────────────────────────────────
# Stable contract
# ─────────────────────────────────────────────────────────────────────────


AGENTS_MD_REQUIRED_SECTIONS: tuple[str, ...] = (
    "## Project Overview",
    "## Languages & Tooling",
    "## Project Conventions",
    "## Testing",
)


# The private prompt: corpus-grounded, includes the cross-corpus block,
# expects chunk_id citations inline.
PRIVATE_PROMPT_TEMPLATE = """\
You are a senior engineer writing an AGENTS.md file for an LLM-coding agent \
that will work in this repository. Use the evidence below verbatim — do not \
invent project history, dependencies, or conventions that are not present \
in either the LOCAL or CROSS-CORPUS blocks. If a section has no evidence, \
say so explicitly rather than guessing.

Required section headings, in order — emit exactly these:
- ## Project Overview
- ## Languages & Tooling
- ## Project Conventions
- ## Cross-Corpus Patterns
- ## Testing

Optional section (include ONLY when the LOCAL block clearly contradicts a \
CROSS-CORPUS pattern):
- ## Anti-patterns to Avoid

LOCAL — detected from this project's own files:
{local_block}

CROSS-CORPUS — patterns from the user's other indexed projects:
{cross_block}

Cite cross-corpus snippets by their source_uri AND chunk_id inline (e.g. \
`(see {sample_uri} chunk_id=N)`). This file lives at \
`.corpus-agents/AGENTS.md` — it is private to the user, gitignored, and \
intended for grounded debugging only.

Write the AGENTS.md body in Markdown. Keep it under 2,000 words. Output \
only the Markdown body — no preamble, no fence.
"""


# The shareable prompt: sanitized for inclusion at the project root.
# IMPORTANT: the explicit sanitization clauses below are part of the
# safety contract — keep them verbatim if updating.
SHAREABLE_PROMPT_TEMPLATE = """\
You are a senior engineer writing an AGENTS.md file for an LLM-coding agent \
that will work in this repository. This output is intended to be \
committed alongside the project (it ships to all of the user's \
collaborators), so it must be sanitized.

Sanitization contract — follow EVERY rule:
- Do NOT cite chunk_ids.
- Do NOT reference any external repository, file path, or source_uri \
from outside THIS project's own tree.
- Do NOT phrase claims as 'based on your past work', 'in the user's \
other projects', or any variant that implies cross-project knowledge.
- Reference ONLY: (a) language/tool defaults this project actually \
uses, (b) conventions inferred from THIS project's own local sampling, \
(c) factual project metadata (license, package manager, test framework).

Required section headings, in order — emit exactly these:
- ## Project Overview
- ## Languages & Tooling
- ## Project Conventions
- ## Testing

LOCAL — detected from this project's own files:
{local_block}

Write the AGENTS.md body in Markdown. Keep it under 2,000 words. Output \
only the Markdown body — no preamble, no fence.
"""


# ─────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChunkRef:
    """Compact citation record carried through ``SynthesisResult``."""

    chunk_id: int
    source_uri: str
    score: float


@dataclass(frozen=True)
class SynthesisResult:
    """Returned by each pass of :func:`synthesize`."""

    markdown: str
    sections: list[str] = field(default_factory=list)
    citations: list[ChunkRef] = field(default_factory=list)


class LLMSynthesisError(RuntimeError):
    """Raised when the LLM call fails or the response cannot be validated."""


# ─────────────────────────────────────────────────────────────────────────
# Prompt building
# ─────────────────────────────────────────────────────────────────────────


def _format_local_block(context: ProjectContext, local: LocalPatterns) -> str:
    lines: list[str] = []
    if context.languages:
        items = sorted(context.languages.items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append("- languages: " + ", ".join(f"{lang}={n}" for (lang, n) in items))
    if context.package_managers:
        lines.append("- package managers: " + ", ".join(context.package_managers))
    if context.build_tool:
        lines.append(f"- build tool: {context.build_tool}")
    if context.test_framework:
        lines.append(f"- test framework: {context.test_framework}")
    if context.license:
        lines.append(f"- license: {context.license}")
    if local.import_style:
        lines.append(f"- import style: {local.import_style}")
    if local.docstring_style:
        lines.append(f"- docstring style: {local.docstring_style}")
    if local.type_hint_density:
        lines.append(f"- type-hint density: {local.type_hint_density:.2f}")
    if local.test_naming_pattern:
        lines.append(f"- test naming: {local.test_naming_pattern}")
    if local.error_handling_examples:
        lines.append("- error-handling examples:")
        for ex in local.error_handling_examples:
            lines.append(f"  - `{ex}`")
    if local.notable_comments:
        lines.append("- notable comments:")
        for c in local.notable_comments[:5]:
            lines.append(f"  - {c}")
    return "\n".join(lines) if lines else "- (no local signals detected)"


def _format_cross_block(cross: CrossCorpusPatterns) -> str:
    if not cross.categories:
        return "- (no cross-corpus matches; cite no external evidence)"
    lines: list[str] = []
    for category, hits in cross.categories.items():
        lines.append(f"- {category}:")
        for hit in hits:
            uri = hit.source_uri or "<unknown>"
            lines.append(f"  - chunk_id={hit.chunk_id} source={uri} score={hit.score:.3f}")
    return "\n".join(lines)


def _first_uri(cross: CrossCorpusPatterns) -> str:
    for hits in cross.categories.values():
        for h in hits:
            if h.source_uri:
                return h.source_uri
    return "vault://example"


def build_private_prompt(
    context: ProjectContext,
    local: LocalPatterns,
    cross: CrossCorpusPatterns,
) -> str:
    """Render the private (corpus-grounded) synthesis prompt."""

    return PRIVATE_PROMPT_TEMPLATE.format(
        local_block=_format_local_block(context, local),
        cross_block=_format_cross_block(cross),
        sample_uri=_first_uri(cross),
    )


def build_shareable_prompt(
    context: ProjectContext,
    local: LocalPatterns,
) -> str:
    """Render the shareable (sanitized) synthesis prompt.

    Note: this prompt deliberately does NOT receive the cross-corpus
    block. The LLM should reference only this project's own facts.
    """

    return SHAREABLE_PROMPT_TEMPLATE.format(
        local_block=_format_local_block(context, local),
    )


# ─────────────────────────────────────────────────────────────────────────
# Synthesis
# ─────────────────────────────────────────────────────────────────────────


def _collect_citations(cross: CrossCorpusPatterns) -> list[ChunkRef]:
    out: list[ChunkRef] = []
    seen: set[int] = set()
    for hits in cross.categories.values():
        for h in hits:
            if h.chunk_id in seen:
                continue
            seen.add(h.chunk_id)
            out.append(
                ChunkRef(
                    chunk_id=h.chunk_id,
                    source_uri=h.source_uri or "",
                    score=float(h.score),
                )
            )
    return out


def _sections_in(markdown: str, *, include_cross: bool) -> list[str]:
    found = []
    for heading in AGENTS_MD_REQUIRED_SECTIONS:
        if heading in markdown:
            found.append(heading)
    if include_cross and "## Cross-Corpus Patterns" in markdown:
        found.append("## Cross-Corpus Patterns")
    if "## Anti-patterns to Avoid" in markdown:
        found.append("## Anti-patterns to Avoid")
    return found


def _call_llm(llm: Callable[[str], str], prompt: str, *, label: str) -> str:
    try:
        raw = llm(prompt)
    except LLMSynthesisError:
        raise
    except Exception as exc:
        raise LLMSynthesisError(f"LLM call failed ({label}): {exc}") from exc
    if not raw or not raw.strip():
        raise LLMSynthesisError(f"LLM returned an empty response ({label})")
    return raw


def _validate_required_sections(
    markdown: str, *, label: str, extra_required: tuple[str, ...] = ()
) -> None:
    required = tuple(AGENTS_MD_REQUIRED_SECTIONS) + tuple(extra_required)
    missing = [h for h in required if h not in markdown]
    if missing:
        raise LLMSynthesisError(
            f"LLM response missing required section headings ({label}): " + ", ".join(missing)
        )


def synthesize(
    context: ProjectContext,
    local: LocalPatterns,
    cross: CrossCorpusPatterns,
    *,
    llm: Callable[[str], str],
) -> tuple[SynthesisResult, SynthesisResult]:
    """Run the two-pass synthesis: ``(private, shareable)``.

    Two LLM passes for clean separation — each prompt is rendered + sent
    independently. The private pass carries cross-corpus citations; the
    shareable pass is gated to have an empty ``citations`` list.

    Raises:
        LLMSynthesisError: on empty/missing-section response, validation
            failure, or any exception raised from the ``llm`` callable.
    """

    # Pass 1 — private, corpus-grounded
    private_prompt = build_private_prompt(context, local, cross)
    private_raw = _call_llm(llm, private_prompt, label="private")
    _validate_required_sections(
        private_raw,
        label="private",
        extra_required=("## Cross-Corpus Patterns",),
    )
    private = SynthesisResult(
        markdown=private_raw,
        sections=_sections_in(private_raw, include_cross=True),
        citations=_collect_citations(cross),
    )

    # Pass 2 — shareable, sanitized
    shareable_prompt = build_shareable_prompt(context, local)
    shareable_raw = _call_llm(llm, shareable_prompt, label="shareable")
    _validate_required_sections(shareable_raw, label="shareable")
    # Defensive sanitization: enforce the shareable contract instead of
    # trusting the prompt. The shareable body lands in `<root>/AGENTS.md`
    # (committable) and `.corpus-agents/shareable.md`, so any leaked
    # citation-shaped content would persist into shared git history.
    _SHAREABLE_FORBIDDEN_PATTERNS = (
        (r"chunk_id\s*=\s*\d+", "chunk_id= citation marker"),
        (r"\bfilesystem://", "raw filesystem:// source_uri"),
        (r"\bvault://", "raw vault:// source_uri"),
    )
    for _pattern, _description in _SHAREABLE_FORBIDDEN_PATTERNS:
        if re.search(_pattern, shareable_raw):
            raise LLMSynthesisError(
                f"shareable response leaked {_description}; refusing to emit "
                "(would persist into shared git history)"
            )
    shareable = SynthesisResult(
        markdown=shareable_raw,
        sections=_sections_in(shareable_raw, include_cross=False),
        citations=[],  # Gated empty: shareable never carries citations.
    )

    return private, shareable
