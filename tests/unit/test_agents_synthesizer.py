"""Unit tests for ``corpus_forge.agents.synthesizer`` — T4 (redirected).

The synthesizer now runs TWO LLM passes and returns
``(private, shareable)``:

- The private pass is corpus-grounded with ``chunk_id`` citations.
- The shareable pass is sanitized: no chunk_id citations, no
  cross-corpus references, no "based on your past work" framing.
  Its ``citations`` list must be empty.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from corpus_forge.agents.cross_corpus import CrossCorpusPatterns
from corpus_forge.agents.detector import ProjectContext
from corpus_forge.agents.sampler import LocalPatterns
from corpus_forge.agents.synthesizer import (
    AGENTS_MD_REQUIRED_SECTIONS,
    PRIVATE_PROMPT_TEMPLATE,
    SHAREABLE_PROMPT_TEMPLATE,
    ChunkRef,
    LLMSynthesisError,
    SynthesisResult,
    build_private_prompt,
    build_shareable_prompt,
    synthesize,
)
from corpus_forge.retrieval.types import Hit

# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


def _ctx() -> ProjectContext:
    return ProjectContext(
        languages={"python": 12, "typescript": 3},
        package_managers=["pyproject", "uv"],
        test_framework="pytest",
        build_tool="pyproject",
        existing_agents_md=None,
        existing_claude_md=None,
        existing_readme=None,
        license="MIT",
        license_header_sample="MIT License\nCopyright (c) ...",
    )


def _local() -> LocalPatterns:
    return LocalPatterns(
        import_style="primarily `from X import Y`",
        docstring_style="google",
        error_handling_examples=["raise FileNotFoundError(path)"],
        type_hint_density=0.93,
        test_naming_pattern="`test_<snake_case>`",
        notable_comments=["# TODO: profile later"],
    )


def _hit(chunk_id: int, score: float, uri: str) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        score=score,
        text=f"text-{chunk_id}",
        document_id=None,
        source_uri=uri,
        title=None,
        dataset_id=1,
        metadata={},
        source="fused",
    )


def _cross() -> CrossCorpusPatterns:
    return CrossCorpusPatterns(
        categories={
            "pytest fixture": [_hit(1, 0.9, "file:///a/test_fixtures.py")],
            "logging.getLogger": [_hit(2, 0.8, "file:///a/log_setup.py")],
        }
    )


def _good_private_md() -> str:
    return dedent(
        """
        ## Project Overview

        Small Python service.

        ## Languages & Tooling

        Python with uv + pyproject.

        ## Project Conventions

        Use `from X import Y`. (see file:///a/test_fixtures.py chunk_id=1)

        ## Cross-Corpus Patterns

        - pytest fixtures via `@pytest.fixture` (chunk_id=1).

        ## Testing

        Pytest, naming `test_*`.
        """
    ).strip()


def _good_shareable_md() -> str:
    return dedent(
        """
        ## Project Overview

        Small Python service.

        ## Languages & Tooling

        Python with uv + pyproject.

        ## Project Conventions

        Use `from X import Y`.

        ## Testing

        Pytest, naming `test_*`.
        """
    ).strip()


# ─────────────────────────────────────────────────────────────────────────
# Prompt-shape tests
# ─────────────────────────────────────────────────────────────────────────


def test_required_sections_constant_is_stable() -> None:
    """The exported tuple of section headings is what the synth uses to validate."""

    # Note: shareable does NOT need ## Cross-Corpus Patterns; private does.
    assert "## Project Overview" in AGENTS_MD_REQUIRED_SECTIONS
    assert "## Languages & Tooling" in AGENTS_MD_REQUIRED_SECTIONS
    assert "## Project Conventions" in AGENTS_MD_REQUIRED_SECTIONS
    assert "## Testing" in AGENTS_MD_REQUIRED_SECTIONS


def test_private_prompt_template_contains_required_sections() -> None:
    """The private prompt instructs the LLM to emit every required heading + cross-corpus."""

    assert "## Project Overview" in PRIVATE_PROMPT_TEMPLATE
    assert "## Languages & Tooling" in PRIVATE_PROMPT_TEMPLATE
    assert "## Project Conventions" in PRIVATE_PROMPT_TEMPLATE
    assert "## Cross-Corpus Patterns" in PRIVATE_PROMPT_TEMPLATE
    assert "## Testing" in PRIVATE_PROMPT_TEMPLATE


def test_shareable_prompt_template_contains_sanitization_instructions() -> None:
    """The shareable prompt must explicitly forbid chunk_id citations + 'past work' framing."""

    # The exact sanitization clauses per spec
    assert "Do NOT cite chunk_ids" in SHAREABLE_PROMPT_TEMPLATE
    assert "Do NOT reference any external repository" in SHAREABLE_PROMPT_TEMPLATE
    assert "Do NOT phrase claims as 'based on your past work'" in SHAREABLE_PROMPT_TEMPLATE
    assert "committed alongside the project" in SHAREABLE_PROMPT_TEMPLATE


def test_shareable_prompt_does_not_include_cross_corpus_block() -> None:
    """The shareable prompt is sanitized — no cross-corpus evidence block."""

    # Cross-corpus evidence block lives only in the private prompt.
    rendered = build_shareable_prompt(_ctx(), _local())
    # The literal cross-corpus block header is absent from the prompt.
    assert "CROSS-CORPUS — patterns from" not in rendered
    # No source URIs from cross-corpus leak through.
    assert "file:///a/test_fixtures.py" not in rendered
    # No chunk_id=N citation hints (the sanitization prompt mentions
    # "chunk_ids" as a forbidden pattern; what we want to rule out is
    # the actual citation shape ``chunk_id=N``).
    import re

    assert re.search(r"chunk_id\s*=\s*\d+", rendered) is None


def test_build_private_prompt_includes_local_and_cross_evidence() -> None:
    """The composed private prompt should reference detected languages + citations."""

    p = build_private_prompt(_ctx(), _local(), _cross())
    assert "python" in p.lower()
    assert "MIT" in p
    assert "pytest fixture" in p
    assert "file:///a/test_fixtures.py" in p


def test_build_shareable_prompt_includes_local_signals_only() -> None:
    """The shareable prompt references this project's facts but no cross-corpus."""

    p = build_shareable_prompt(_ctx(), _local())
    assert "python" in p.lower()
    assert "pytest" in p.lower()
    # Project metadata is fine
    assert "MIT" in p


# ─────────────────────────────────────────────────────────────────────────
# synthesize() — two-pass
# ─────────────────────────────────────────────────────────────────────────


def test_synthesize_returns_two_results() -> None:
    """``synthesize`` returns ``(private, shareable)`` tuple."""

    captured: list[str] = []

    def fake_llm(prompt: str) -> str:
        captured.append(prompt)
        # Second call (shareable) returns the sanitized markdown
        if "Do NOT cite chunk_ids" in prompt:
            return _good_shareable_md()
        return _good_private_md()

    res = synthesize(_ctx(), _local(), _cross(), llm=fake_llm)
    assert isinstance(res, tuple)
    assert len(res) == 2
    private, shareable = res
    assert isinstance(private, SynthesisResult)
    assert isinstance(shareable, SynthesisResult)


def test_synthesize_makes_two_llm_calls() -> None:
    """One call for the private pass, one for the shareable pass."""

    call_count = {"n": 0}

    def fake_llm(prompt: str) -> str:
        call_count["n"] += 1
        if "Do NOT cite chunk_ids" in prompt:
            return _good_shareable_md()
        return _good_private_md()

    synthesize(_ctx(), _local(), _cross(), llm=fake_llm)
    assert call_count["n"] == 2


def test_private_result_carries_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private SynthesisResult preserves cross-corpus chunk_ids in citations list."""

    def fake_llm(prompt: str) -> str:
        if "Do NOT cite chunk_ids" in prompt:
            return _good_shareable_md()
        return _good_private_md()

    private, _shareable = synthesize(_ctx(), _local(), _cross(), llm=fake_llm)
    assert len(private.citations) >= 2
    uris = {c.source_uri for c in private.citations}
    assert "file:///a/test_fixtures.py" in uris


def test_shareable_result_has_no_citations() -> None:
    """The shareable result MUST have an empty citations list (gated)."""

    def fake_llm(prompt: str) -> str:
        if "Do NOT cite chunk_ids" in prompt:
            return _good_shareable_md()
        return _good_private_md()

    _private, shareable = synthesize(_ctx(), _local(), _cross(), llm=fake_llm)
    assert shareable.citations == []


def test_synthesize_validates_required_sections_for_private() -> None:
    """If the private response misses ## Cross-Corpus Patterns → LLMSynthesisError."""

    bad_private = _good_private_md().replace("## Cross-Corpus Patterns", "## Notes")

    def fake_llm(prompt: str) -> str:
        if "Do NOT cite chunk_ids" in prompt:
            return _good_shareable_md()
        return bad_private

    with pytest.raises(LLMSynthesisError):
        synthesize(_ctx(), _local(), _cross(), llm=fake_llm)


def test_synthesize_validates_required_sections_for_shareable() -> None:
    """If the shareable response misses ## Testing → LLMSynthesisError."""

    bad_shareable = _good_shareable_md().replace("## Testing", "## Notes")

    def fake_llm(prompt: str) -> str:
        if "Do NOT cite chunk_ids" in prompt:
            return bad_shareable
        return _good_private_md()

    with pytest.raises(LLMSynthesisError):
        synthesize(_ctx(), _local(), _cross(), llm=fake_llm)


def test_synthesize_raises_when_either_response_empty() -> None:
    def fake_llm(_prompt: str) -> str:
        return ""

    with pytest.raises(LLMSynthesisError):
        synthesize(_ctx(), _local(), _cross(), llm=fake_llm)


def test_synthesize_propagates_upstream_http_error() -> None:
    class _Boom(RuntimeError):
        pass

    def fake_llm(_prompt: str) -> str:
        raise _Boom("upstream 500")

    with pytest.raises(LLMSynthesisError):
        synthesize(_ctx(), _local(), _cross(), llm=fake_llm)


def test_chunk_ref_shape() -> None:
    """ChunkRef carries chunk_id, source_uri, score."""

    ref = ChunkRef(chunk_id=42, source_uri="file:///x.py", score=0.7)
    assert ref.chunk_id == 42
    assert ref.source_uri == "file:///x.py"
    assert ref.score == 0.7


def test_shareable_pass_distinct_canned_response_from_private() -> None:
    """Mock returns two distinct canned responses; synth wires them to the right slots."""

    def fake_llm(prompt: str) -> str:
        if "Do NOT cite chunk_ids" in prompt:
            return _good_shareable_md()
        return _good_private_md()

    private, shareable = synthesize(_ctx(), _local(), _cross(), llm=fake_llm)
    assert private.markdown == _good_private_md()
    assert shareable.markdown == _good_shareable_md()
    # Crucially: shareable markdown contains no chunk_id leakage.
    assert "chunk_id" not in shareable.markdown
    assert "file:///a/test_fixtures.py" not in shareable.markdown
