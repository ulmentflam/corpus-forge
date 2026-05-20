"""Phase O Wave 3 (O3-T2) — Unit tests for corpus_forge.analyze.quality.

Pins the public shape of:
  - ``score_chunk_quality(chunk, *, model_path=None) -> float``
  - ``score_chunks_batch(chunks, *, model_path=None) -> list[float]``

Contract source: task O3-T2 brief + ``.planning/tdd/phase_o_eda_cleaning.md``
§ Wave O3 RED.

RED state: ``from corpus_forge.analyze.quality import ...`` fails with
``ModuleNotFoundError: No module named 'corpus_forge.analyze.quality'``
because ``quality.py`` does not yet exist.

Key design decisions captured in tests
--------------------------------------
- Heuristic mode (``model_path=None`` or file does not exist):
    * Combines token-count adequacy, label presence, and metadata richness.
    * Returns ``float ∈ [0.0, 1.0]`` — always finite and in range.
    * Deterministic — same input, same output, no PRNG.
    * Short text (< 100 chars) → score ≤ 0.3 (short-chunk penalty).
    * Very long text (> 5000 chars) → score ≤ 0.7 (long-chunk penalty caps
      the adequacy sub-score, not a hard ceiling on the combined score).
    * Empty / whitespace text → 0.0.
    * A classifier label present → score > baseline without label (bonus).
    * Rich non-trivial metadata (≥ 3 non-empty valued fields) → score higher
      than the same chunk without metadata (bonus).
- Trained-model mode (``model_path`` points to a joblib-loadable fixture):
    * ``_load_trained_model()`` (internal) consulted via ``model_path``.
    * ``predict_proba`` output is used and clamped to ``[0, 1]``.
    * The model fixture for tests is a minimal sklearn ``DummyClassifier``
      persisted with ``joblib.dump`` in a ``tmp_path`` fixture — no real model.
- Lazy-import guard:
    * Importing the module does NOT bring ``sklearn`` or ``joblib`` into
      ``sys.modules``.
    * ``joblib`` is only loaded when a valid ``model_path`` is actually passed.
- Batch ordering: ``score_chunks_batch(chunks)`` returns scores in the same
  order as the input list.
- Hypothesis property: ``score_chunk_quality(chunk)`` is always a finite
  float in ``[0.0, 1.0]`` for any reasonable chunk dict.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk(
    text: str = "A reasonable paragraph of text that discusses something meaningful.",
    token_count: int = 50,
    classifier_label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal chunk dict that quality.py expects."""
    c: dict[str, Any] = {
        "text": text,
        "token_count": token_count,
    }
    if classifier_label is not None:
        c["classifier_label"] = classifier_label
    if metadata is not None:
        c["metadata"] = metadata
    return c


def _short_chunk() -> dict[str, Any]:
    """A chunk whose text is fewer than 100 characters."""
    return _chunk(text="Short.", token_count=3)


def _long_chunk() -> dict[str, Any]:
    """A chunk whose text exceeds 5000 characters."""
    long_text = "word " * 1200  # ~6000 chars
    return _chunk(text=long_text, token_count=1200)


def _well_formed_chunk() -> dict[str, Any]:
    """A chunk that represents a well-formed paragraph of 200+ characters with a label."""
    text = (
        "The research demonstrates that distributed systems require careful coordination. "
        "Engineers have developed many protocols to address the fundamental challenges. "
        "This work provides a comprehensive overview of the current state of the art."
    )
    assert len(text) >= 200, f"text is {len(text)} chars, need >= 200"
    return _chunk(
        text=text,
        token_count=50,
        classifier_label="research",
        metadata={"language": "en", "source_type": "paper", "year": "2024"},
    )


# ---------------------------------------------------------------------------
# Import smoke — must be the first test group so RED manifests cleanly
# ---------------------------------------------------------------------------


def test_import_score_chunk_quality() -> None:
    """``score_chunk_quality`` is importable."""
    from corpus_forge.analyze.quality import score_chunk_quality  # noqa: F401


def test_import_score_chunks_batch() -> None:
    """``score_chunks_batch`` is importable."""
    from corpus_forge.analyze.quality import score_chunks_batch  # noqa: F401


def test_import_persist_quality_signals() -> None:
    """``persist_quality_signals`` is importable."""
    from corpus_forge.analyze.quality import persist_quality_signals  # noqa: F401


# ---------------------------------------------------------------------------
# Lazy-import guard — sklearn and joblib must NOT load on module import
# ---------------------------------------------------------------------------


def test_module_import_does_not_load_sklearn() -> None:
    """Importing corpus_forge.analyze.quality must not pull sklearn into sys.modules.

    The [analyze] extra brings sklearn transitively, but quality.py must be
    callable in heuristic mode on a plain ``pip install corpus-forge``
    installation.  The lazy-import contract (project memory
    project_phase_d_treesitter_lazy_fetch.md) requires heavy deps inside
    function bodies only.
    """
    # Evict corpus_forge.analyze.quality if already cached from prior tests.
    mods_to_evict = [k for k in sys.modules if "corpus_forge.analyze.quality" in k]
    for m in mods_to_evict:
        sys.modules.pop(m, None)

    # Snapshot modules before import.
    before = set(sys.modules.keys())
    import corpus_forge.analyze.quality  # noqa: F401

    after = set(sys.modules.keys())
    new_mods = after - before

    sklearn_loaded = any("sklearn" in m for m in new_mods)
    assert not sklearn_loaded, (
        f"Importing corpus_forge.analyze.quality must not load sklearn. "
        f"New sklearn modules: {[m for m in new_mods if 'sklearn' in m]}"
    )


def test_module_import_does_not_load_joblib() -> None:
    """Importing corpus_forge.analyze.quality must not pull joblib into sys.modules."""
    mods_to_evict = [k for k in sys.modules if "corpus_forge.analyze.quality" in k]
    for m in mods_to_evict:
        sys.modules.pop(m, None)

    before = set(sys.modules.keys())
    import corpus_forge.analyze.quality  # noqa: F401

    after = set(sys.modules.keys())
    new_mods = after - before

    joblib_loaded = any("joblib" in m for m in new_mods)
    assert not joblib_loaded, (
        f"Importing corpus_forge.analyze.quality must not load joblib. "
        f"New joblib modules: {[m for m in new_mods if 'joblib' in m]}"
    )


# ---------------------------------------------------------------------------
# Heuristic scorer — return type + bounds
# ---------------------------------------------------------------------------


def test_score_chunk_quality_returns_float() -> None:
    """score_chunk_quality returns a Python float."""
    from corpus_forge.analyze.quality import score_chunk_quality

    result = score_chunk_quality(_chunk())
    assert isinstance(result, float), f"Expected float, got {type(result).__name__}"


def test_score_chunk_quality_in_unit_interval() -> None:
    """score_chunk_quality returns a value in [0.0, 1.0]."""
    from corpus_forge.analyze.quality import score_chunk_quality

    result = score_chunk_quality(_chunk())
    assert 0.0 <= result <= 1.0, f"Score {result!r} is outside [0.0, 1.0]"


def test_score_chunk_quality_is_finite() -> None:
    """score_chunk_quality must never return NaN or Infinity."""
    from corpus_forge.analyze.quality import score_chunk_quality

    result = score_chunk_quality(_chunk())
    assert math.isfinite(result), f"Expected finite score, got {result!r}"


def test_score_chunk_quality_deterministic() -> None:
    """Same chunk input must produce the same score on repeated calls (no PRNG)."""
    from corpus_forge.analyze.quality import score_chunk_quality

    chunk = _chunk(
        text="A stable piece of text for determinism testing.",
        token_count=20,
        classifier_label="test",
    )
    first = score_chunk_quality(chunk)
    second = score_chunk_quality(chunk)
    assert first == second, (
        f"score_chunk_quality is not deterministic: got {first!r} then {second!r}"
    )


# ---------------------------------------------------------------------------
# Heuristic scorer — short-chunk penalty
# ---------------------------------------------------------------------------


def test_short_chunk_penalty_score_at_most_0_3() -> None:
    """A chunk with text shorter than 100 characters scores at most 0.3.

    The heuristic applies a penalty for very short text (low information
    density).  The spec pins the upper bound at 0.3 for sub-100-char chunks.
    """
    from corpus_forge.analyze.quality import score_chunk_quality

    score = score_chunk_quality(_short_chunk())
    assert score <= 0.3, f"Short chunk (text < 100 chars) should score <= 0.3, got {score!r}"


def test_empty_text_scores_zero() -> None:
    """Empty text must score exactly 0.0."""
    from corpus_forge.analyze.quality import score_chunk_quality

    chunk = _chunk(text="", token_count=0)
    score = score_chunk_quality(chunk)
    assert score == 0.0, f"Empty text chunk must score 0.0, got {score!r}"


def test_whitespace_only_text_scores_zero() -> None:
    """Whitespace-only text must score exactly 0.0."""
    from corpus_forge.analyze.quality import score_chunk_quality

    chunk = _chunk(text="   \n\t  \n  ", token_count=0)
    score = score_chunk_quality(chunk)
    assert score == 0.0, f"Whitespace-only chunk must score 0.0, got {score!r}"


# ---------------------------------------------------------------------------
# Heuristic scorer — long-chunk penalty
# ---------------------------------------------------------------------------


def test_long_chunk_penalty_score_at_most_0_7() -> None:
    """A chunk with > 5000 characters has its adequacy contribution capped.

    Very long chunks are split poorly — the heuristic penalises them.
    The spec does not set a hard ceiling below 1.0 on the combined score,
    but the adequacy sub-score (token-count bonus) tops out before the
    long-chunk threshold.  We verify the combined score does not reach the
    maximum achievable by a short well-formed chunk, to confirm the penalty
    is active.

    Implementation note: the spec says long-chunk *caps the adequacy*
    sub-score; other sub-scores (label, metadata) may still contribute.
    We test that the long chunk (no label, no metadata) scores ≤ 0.7.
    """
    from corpus_forge.analyze.quality import score_chunk_quality

    score = score_chunk_quality(_long_chunk())
    assert score <= 0.7, (
        f"Long chunk (text > 5000 chars, no label, no metadata) should score <= 0.7, got {score!r}"
    )


# ---------------------------------------------------------------------------
# Heuristic scorer — label presence bonus
# ---------------------------------------------------------------------------


def test_label_presence_increases_score() -> None:
    """A chunk with a classifier label scores higher than the same chunk without one."""
    from corpus_forge.analyze.quality import score_chunk_quality

    base_text = (
        "A moderately long paragraph about natural language processing and its applications "
        "in modern software systems, providing enough context for the quality scorer."
    )
    chunk_no_label = _chunk(text=base_text, token_count=40, classifier_label=None)
    chunk_with_label = _chunk(text=base_text, token_count=40, classifier_label="nlp")

    score_no_label = score_chunk_quality(chunk_no_label)
    score_with_label = score_chunk_quality(chunk_with_label)

    assert score_with_label > score_no_label, (
        f"Label-present chunk should score higher than label-absent chunk. "
        f"no_label={score_no_label!r}, with_label={score_with_label!r}"
    )


# ---------------------------------------------------------------------------
# Heuristic scorer — metadata richness bonus
# ---------------------------------------------------------------------------


def test_metadata_richness_increases_score() -> None:
    """Rich non-trivial metadata boosts score vs. empty metadata."""
    from corpus_forge.analyze.quality import score_chunk_quality

    text = (
        "A well-documented research note describing the experimental methodology, "
        "findings, and implications for future work in the field of corpus linguistics."
    )
    chunk_no_meta = _chunk(text=text, token_count=40, metadata={})
    chunk_rich_meta = _chunk(
        text=text,
        token_count=40,
        metadata={
            "language": "en",
            "source_type": "article",
            "year": "2024",
            "author": "Smith",
        },
    )

    score_no_meta = score_chunk_quality(chunk_no_meta)
    score_rich_meta = score_chunk_quality(chunk_rich_meta)

    assert score_rich_meta > score_no_meta, (
        f"Rich-metadata chunk should score higher than empty-metadata chunk. "
        f"no_meta={score_no_meta!r}, rich_meta={score_rich_meta!r}"
    )


# ---------------------------------------------------------------------------
# Heuristic scorer — well-formed chunk gets a high score
# ---------------------------------------------------------------------------


def test_well_formed_chunk_scores_at_least_0_6() -> None:
    """A well-formed paragraph (200+ chars, label, rich metadata) scores >= 0.6.

    The spec states: 'well-formed paragraph (200+ chars, has both noun and
    verb tokens, has a sentence terminator) → ≥ 0.6'.  The heuristic uses
    character count and metadata presence as proxies for this (no NLP
    parsing in heuristic mode).
    """
    from corpus_forge.analyze.quality import score_chunk_quality

    score = score_chunk_quality(_well_formed_chunk())
    assert score >= 0.6, (
        f"Well-formed chunk (200+ chars, label, rich metadata) should score >= 0.6, got {score!r}"
    )


# ---------------------------------------------------------------------------
# Heuristic mode when model_path does not exist
# ---------------------------------------------------------------------------


def test_missing_model_path_falls_back_to_heuristic(tmp_path: Path) -> None:
    """When model_path points to a non-existent file, heuristic mode is used.

    No exception must be raised — the function silently falls back.
    The result must still be in [0.0, 1.0].
    """
    from corpus_forge.analyze.quality import score_chunk_quality

    nonexistent = tmp_path / "no_such_model.joblib"
    assert not nonexistent.exists()

    result = score_chunk_quality(_chunk(), model_path=nonexistent)
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0, f"Fallback heuristic score out of range: {result!r}"


def test_none_model_path_uses_heuristic() -> None:
    """model_path=None explicitly requests heuristic mode (no joblib call)."""
    from corpus_forge.analyze.quality import score_chunk_quality

    # Should not raise even if joblib is not installed.
    result = score_chunk_quality(_chunk(), model_path=None)
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# Trained-model mode (monkeypatched fixture)
# ---------------------------------------------------------------------------


def test_trained_model_path_uses_model_predict_proba(tmp_path: Path) -> None:
    """When model_path is a valid joblib file, the model's predict_proba is consulted.

    The fixture uses sklearn's DummyClassifier to avoid a real model download.
    The output is clamped to [0, 1].
    """
    joblib = pytest.importorskip("joblib")
    sklearn_dummy = pytest.importorskip("sklearn.dummy")

    DummyClassifier = sklearn_dummy.DummyClassifier
    model = DummyClassifier(strategy="constant", constant=1)
    # Fit on trivial data so predict_proba is available. The constant target
    # value must be present in the training labels, so train on [0, 1].
    model.fit([[0], [1]], [0, 1])

    model_path = tmp_path / "quality.joblib"
    joblib.dump(model, model_path)

    from corpus_forge.analyze.quality import score_chunk_quality

    result = score_chunk_quality(_chunk(), model_path=model_path)
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0, f"Trained-model score out of range: {result!r}"


class _OverclampedStub:
    """Picklable stub model whose predict_proba returns a value > 1.0.

    MagicMock is not picklable under Python 3.13's stricter pickling rules,
    so we use a real picklable class to simulate a buggy model output.
    """

    def predict_proba(self, x):
        return [[0.0, 1.5] for _ in x]


def test_trained_model_output_is_clamped(tmp_path: Path) -> None:
    """The model's predict_proba output is clamped to [0.0, 1.0].

    Uses a picklable stub class whose predict_proba returns 1.5 to verify the
    clamping logic in quality.py.
    """
    joblib = pytest.importorskip("joblib")

    model_path = tmp_path / "clamped_model.joblib"
    joblib.dump(_OverclampedStub(), model_path)

    from corpus_forge.analyze.quality import score_chunk_quality

    result = score_chunk_quality(_chunk(), model_path=model_path)
    assert result <= 1.0, f"Model output > 1.0 was not clamped; got {result!r}"
    assert result >= 0.0, f"Clamped score is negative: {result!r}"


# ---------------------------------------------------------------------------
# Batch ordering
# ---------------------------------------------------------------------------


def test_score_chunks_batch_empty_list() -> None:
    """score_chunks_batch([]) returns an empty list without error."""
    from corpus_forge.analyze.quality import score_chunks_batch

    result = score_chunks_batch([])
    assert result == [], f"Expected [] for empty input, got {result!r}"


def test_score_chunks_batch_single_element() -> None:
    """score_chunks_batch with one chunk returns a list of length 1."""
    from corpus_forge.analyze.quality import score_chunks_batch

    result = score_chunks_batch([_chunk()])
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], float)
    assert 0.0 <= result[0] <= 1.0


def test_score_chunks_batch_preserves_order() -> None:
    """Batch output is in the same order as the input list.

    We use chunks with distinct, deterministic characteristics so we can
    verify that a short chunk's score appears at its expected index.
    """
    from corpus_forge.analyze.quality import score_chunks_batch

    chunks = [
        _short_chunk(),  # index 0 — should score low
        _well_formed_chunk(),  # index 1 — should score high
        _chunk(text="", token_count=0),  # index 2 — should score 0.0
    ]
    scores = score_chunks_batch(chunks)

    assert len(scores) == 3, f"Expected 3 scores for 3 chunks, got {len(scores)}"
    # Empty text at index 2 must be 0.0.
    assert scores[2] == 0.0, f"Empty-text chunk at index 2 should score 0.0, got {scores[2]!r}"
    # Well-formed chunk at index 1 should outscore the short chunk at index 0.
    assert scores[1] > scores[0], (
        f"Well-formed chunk at index 1 ({scores[1]!r}) should beat short chunk "
        f"at index 0 ({scores[0]!r})"
    )


def test_score_chunks_batch_matches_individual_scores() -> None:
    """score_chunks_batch(chunks)[i] == score_chunk_quality(chunks[i]) for all i."""
    from corpus_forge.analyze.quality import score_chunk_quality, score_chunks_batch

    chunks = [
        _short_chunk(),
        _well_formed_chunk(),
        _chunk(
            text="Medium length text with enough characters to avoid the short penalty.",
            token_count=18,
        ),
    ]
    batch_scores = score_chunks_batch(chunks)
    individual_scores = [score_chunk_quality(c) for c in chunks]

    for i, (b, ind) in enumerate(zip(batch_scores, individual_scores, strict=True)):
        assert b == ind, f"Batch score at index {i} ({b!r}) != individual score ({ind!r})"


# ---------------------------------------------------------------------------
# Hypothesis property: score is always finite and in [0, 1]
# ---------------------------------------------------------------------------


@given(
    text=st.text(max_size=8000),
    token_count=st.integers(min_value=0, max_value=10000),
    has_label=st.booleans(),
    n_meta_fields=st.integers(min_value=0, max_value=6),
)
@settings(max_examples=100, deadline=2000)
def test_property_score_always_finite_in_unit_interval(
    text: str,
    token_count: int,
    has_label: bool,
    n_meta_fields: int,
) -> None:
    """Hypothesis: score_chunk_quality(chunk) ∈ [0.0, 1.0] and finite for any input.

    This property must hold across the heuristic scorer's entire input domain.
    Covers degenerate text, extreme token counts, and arbitrary metadata shapes.
    """
    from corpus_forge.analyze.quality import score_chunk_quality

    metadata: dict[str, Any] = {f"field_{i}": f"value_{i}" for i in range(n_meta_fields)}
    chunk: dict[str, Any] = {
        "text": text,
        "token_count": token_count,
    }
    if has_label:
        chunk["classifier_label"] = "hypothesis_label"
    if metadata:
        chunk["metadata"] = metadata

    result = score_chunk_quality(chunk)
    assert isinstance(result, float), f"Expected float, got {type(result).__name__}"
    assert math.isfinite(result), f"Score is not finite: {result!r}"
    assert 0.0 <= result <= 1.0, f"Score {result!r} is outside [0.0, 1.0]"
