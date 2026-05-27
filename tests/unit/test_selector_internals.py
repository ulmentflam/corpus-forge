"""Unit pins for the curation selector's pure internal helpers.

Surface under test: the private, pure-function helpers in
``corpus_forge.curation.selector`` that the public ``next_curation_*`` entry
points lean on but which the integration-style selector tests don't drive into
their defensive corners:

- ``_row_to_candidate`` — robustness of the backend-row → ``_Candidate``
  coercion (bad metadata JSON, non-dict metadata, unparseable date, non-list
  embedding).
- ``_cosine_distance`` / ``_compute_centroid`` / ``_normalise`` /
  ``_compute_freshness`` — the pure scoring primitives, including their
  degenerate-input branches.
- ``_ranker_elevation_scores`` — the empty-pool short-circuit and the
  reranker-raises / reranker-returns-empty fallbacks.

These are all pure (or trivially mock-driven) so no backend, models, or DB are
needed.  They exist to lock the parsing/scoring robustness contract the public
selector relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from corpus_forge.curation.selector import (
    _Candidate,
    _compute_centroid,
    _compute_freshness,
    _cosine_distance,
    _normalise,
    _ranker_elevation_scores,
    _row_to_candidate,
)

pytestmark = pytest.mark.unit


# ── _row_to_candidate defensive parsing ───────────────────────────────────────


def _base_row(**overrides):
    row = {
        "chunk_id": 1,
        "document_id": 2,
        "text": "hello",
        "heading": "h",
        "description": "d",
        "metadata": {"language": "en"},
        "document_title": "t",
        "source_uri": "vault://n.md",
        "modified_at": None,
        "classifier_label": "topic",
        "classifier_confidence": 0.5,
        "labels": [],
        "embedding": None,
    }
    row.update(overrides)
    return row


def test_row_to_candidate_bad_metadata_json_falls_back_to_empty_dict():
    cand = _row_to_candidate(_base_row(metadata="{not valid json"))
    assert cand.metadata == {}


def test_row_to_candidate_non_dict_metadata_falls_back_to_empty_dict():
    # A JSON string that parses to a list (not a dict) → coerced to {}.
    cand = _row_to_candidate(_base_row(metadata="[1, 2, 3]"))
    assert cand.metadata == {}
    # A bare non-str, non-dict value also coerces to {}.
    cand2 = _row_to_candidate(_base_row(metadata=42))
    assert cand2.metadata == {}


def test_row_to_candidate_parses_iso_string_date_and_adds_utc():
    cand = _row_to_candidate(_base_row(modified_at="2026-01-02T03:04:05Z"))
    assert cand.modified_at is not None
    assert cand.modified_at.tzinfo is not None


def test_row_to_candidate_bad_date_string_becomes_none():
    cand = _row_to_candidate(_base_row(modified_at="not-a-date"))
    assert cand.modified_at is None


def test_row_to_candidate_non_datetime_non_str_date_becomes_none():
    cand = _row_to_candidate(_base_row(modified_at=12345))
    assert cand.modified_at is None


def test_row_to_candidate_naive_datetime_object_gets_utc():
    """A naive ``datetime`` *object* (not str) is kept and stamped UTC.

    Drives the `modified_at.tzinfo is None` branch after the `isinstance(...,
    datetime)` fast path (vs. the ISO-string parse path).
    """
    naive = datetime(2026, 3, 4, 5, 6)  # intentional naive (tz-less) input
    cand = _row_to_candidate(_base_row(modified_at=naive))
    assert cand.modified_at is not None
    assert cand.modified_at.tzinfo is UTC


def test_row_to_candidate_non_list_embedding_becomes_none():
    cand = _row_to_candidate(_base_row(embedding="0.1,0.2"))
    assert cand.embedding is None


def test_row_to_candidate_list_embedding_coerced_to_floats():
    cand = _row_to_candidate(_base_row(embedding=[1, 2, 3]))
    assert cand.embedding == [1.0, 2.0, 3.0]


# ── pure scoring primitives ────────────────────────────────────────────────────


def test_cosine_distance_mismatched_or_empty_returns_zero():
    assert _cosine_distance([], []) == 0.0
    assert _cosine_distance([1.0, 2.0], [1.0]) == 0.0


def test_cosine_distance_zero_vector_returns_zero():
    assert _cosine_distance([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_distance_identical_vectors_is_zero():
    assert _cosine_distance([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)


def test_compute_centroid_empty_returns_none():
    assert _compute_centroid([]) is None
    # Vectors that are all empty also short-circuit to None.
    assert _compute_centroid([[], []]) is None


def test_compute_centroid_mismatched_dims_returns_none():
    assert _compute_centroid([[1.0, 2.0], [3.0]]) is None


def test_compute_centroid_mean():
    assert _compute_centroid([[0.0, 0.0], [2.0, 4.0]]) == [1.0, 2.0]


def test_normalise_empty_returns_empty():
    assert _normalise([]) == []


def test_normalise_constant_input_is_all_half():
    assert _normalise([3.0, 3.0, 3.0]) == [0.5, 0.5, 0.5]


def test_normalise_spread_maps_to_unit_range():
    assert _normalise([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]


def test_compute_freshness_none_is_zero():
    assert _compute_freshness(None) == 0.0


def test_compute_freshness_naive_datetime_assumed_utc():
    # A naive (tz-less) recent datetime must be treated as UTC, not crash.
    now = datetime(2026, 5, 1, tzinfo=UTC)
    naive_recent = datetime(2026, 4, 30)  # intentional naive (tz-less) input
    assert _compute_freshness(naive_recent, now=now) == 1.0


# ── _ranker_elevation_scores fallbacks ─────────────────────────────────────────


def _cand(chunk_id: int, embedding=None) -> _Candidate:
    return _Candidate(
        chunk_id=chunk_id,
        document_id=1,
        text="x",
        heading=None,
        description=None,
        metadata={},
        document_title=None,
        source_uri=None,
        modified_at=None,
        classifier_label=None,
        classifier_confidence=None,
        labels=[],
        embedding=embedding,
        learned_quality=None,
    )


def test_ranker_elevation_empty_pool_short_circuits():
    scores, meaningful = _ranker_elevation_scores([], seed_query="q", reranker=object())
    assert scores == []
    assert meaningful is True


class _RaisingReranker:
    def rerank(self, query, hits, top_n):
        raise RuntimeError("boom")


class _EmptyReranker:
    def rerank(self, query, hits, top_n):
        return []


def test_ranker_elevation_reranker_raises_falls_back_to_neutral():
    cands = [_cand(1), _cand(2)]
    scores, meaningful = _ranker_elevation_scores(
        cands, seed_query="q", reranker=_RaisingReranker()
    )
    # Both raw scores were 0.5 → constant → normalised to 0.5.
    assert scores == [0.5, 0.5]
    assert meaningful is True


def test_ranker_elevation_reranker_returns_empty_falls_back_to_neutral():
    cands = [_cand(1)]
    scores, meaningful = _ranker_elevation_scores(cands, seed_query="q", reranker=_EmptyReranker())
    assert scores == [0.5]
    assert meaningful is True
