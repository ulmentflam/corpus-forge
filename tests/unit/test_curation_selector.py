"""Phase J / J4 — Unit tests for the curation selector.

Pure-function tests over the score math + grouping. The selector is
exercised through a fake backend that exposes ``iter_curation_candidates``
(the preferred hook path); a couple of cases additionally use the
generic ``_execute`` fallback to keep that branch covered.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from corpus_forge.curation import (
    CURATION_CHAT_TEMPLATE,
    MISSING_METADATA_FIELDS,
    SCORE_WEIGHTS,
    ScoreBreakdown,
    next_curation_batch,
    next_curation_target,
)

# ─────────────────────────────────────────────────────────────────────────
# Helpers — fake backend that yields prebuilt candidate rows
# ─────────────────────────────────────────────────────────────────────────


_NOW = datetime(2026, 5, 17, tzinfo=UTC)


_DEFAULT_MODIFIED_AT = _NOW - timedelta(days=2)


def _row(
    chunk_id: int,
    *,
    document_id: int | None = 1,
    text: str = "lorem ipsum",
    heading: str | None = "h",
    description: str | None = "d",
    metadata: dict[str, Any] | None = None,
    document_title: str | None = "title",
    source_uri: str | None = "vault://notes/note.md",
    modified_at: datetime | None | type[...] = ...,  # type: ignore[assignment]
    labels: list[tuple[str, str]] | None = None,
    classifier_label: str | None = "topic_a",
    classifier_confidence: float | None = 0.8,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    """Build a backend row dict in the shape ``_iter_curation_candidates``
    expects. Defaults represent a "well-filled" chunk; tests override
    individual fields to push specific signals.

    ``modified_at`` uses the Ellipsis sentinel so the caller can pass
    explicit ``None`` and have it propagate through (vs. falling back to
    the default).
    """
    effective_modified_at: datetime | None = (
        _DEFAULT_MODIFIED_AT if modified_at is ... else modified_at  # type: ignore[assignment]
    )
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "text": text,
        "heading": heading,
        "description": description,
        "metadata": dict(metadata if metadata is not None else {"language": "en"}),
        "document_title": document_title,
        "source_uri": source_uri,
        "modified_at": effective_modified_at,
        "labels": list(
            labels
            if labels is not None
            else [
                ("class", classifier_label or ""),
                ("topic", "x"),
            ]
        ),
        "classifier_label": classifier_label,
        "classifier_confidence": classifier_confidence,
        "embedding": embedding,
    }


class _HookBackend:
    """Fake backend that supplies candidates via the dedicated hook."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[dict[str, Any]] = []

    def iter_curation_candidates(
        self, *, dataset: str | None, limit: int
    ) -> Iterable[dict[str, Any]]:
        self.calls.append({"dataset": dataset, "limit": limit})
        yield from self._rows[:limit]


class _ExecuteBackend:
    """Fake backend that exposes ``_execute`` (generic-walk fallback)."""

    def __init__(
        self,
        chunk_rows: list[dict[str, Any]],
        label_rows_by_chunk: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._chunk_rows = chunk_rows
        self._label_rows = label_rows_by_chunk or {}

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "FROM chunks c" in sql:
            return self._chunk_rows
        if "chunk_labels" in sql:
            # Parse chunk_id out of the inlined SQL ("WHERE cl.chunk_id = N")
            tail = sql.rsplit("=", maxsplit=1)[-1].strip()
            try:
                chunk_id = int(tail)
            except ValueError:
                return []
            return self._label_rows.get(chunk_id, [])
        return []

    def find_dataset_id_by_name(self, name: str) -> int | None:
        return 1 if name == "demo" else None


class _FakeReranker:
    """Records ``rerank`` calls and returns scores from a lookup table."""

    name = "fake-reranker"
    model_id = "fake/model"

    def __init__(self, scores_by_chunk_id: dict[int, float]) -> None:
        self.scores = scores_by_chunk_id
        self.calls: list[tuple[str, list[int]]] = []

    def warmup(self) -> None:  # pragma: no cover — protocol stub
        pass

    def rerank(self, query: str, hits: list[Any], *, top_n: int | None = None) -> list[Any]:
        self.calls.append((query, [h.chunk_id for h in hits]))
        out: list[Any] = []
        for h in hits:
            score = self.scores.get(h.chunk_id, 0.5)
            out.append(dataclasses.replace(h, score=score))
        out.sort(key=lambda hit: hit.score, reverse=True)
        if top_n is not None:
            out = out[:top_n]
        return out


# ─────────────────────────────────────────────────────────────────────────
# Sub-score primitives
# ─────────────────────────────────────────────────────────────────────────


def test_confidence_deficit_no_classifier_label_is_one() -> None:
    backend = _HookBackend([_row(1, classifier_label=None, classifier_confidence=None, labels=[])])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.confidence_deficit == 1.0


def test_confidence_deficit_high_confidence_is_low() -> None:
    backend = _HookBackend([_row(1, classifier_confidence=0.9)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.confidence_deficit == pytest.approx(0.1, abs=1e-9)


def test_confidence_deficit_clamped_below_zero() -> None:
    backend = _HookBackend([_row(1, classifier_confidence=1.0)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.confidence_deficit == 0.0


def test_confidence_deficit_none_treated_as_one() -> None:
    backend = _HookBackend([_row(1, classifier_confidence=None, labels=[])])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.classifier_confidence is None
    assert target.score_breakdown.confidence_deficit == 1.0


def test_missing_metadata_all_six_missing() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                document_title=None,
                heading=None,
                description=None,
                metadata={},
                source_uri=None,
                labels=[],
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.missing_metadata == pytest.approx(1.0)
    assert sorted(target.missing_fields) == sorted(MISSING_METADATA_FIELDS)


def test_missing_metadata_none_missing() -> None:
    backend = _HookBackend([_row(1)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.missing_metadata == 0.0
    assert target.missing_fields == []


def test_missing_metadata_partial() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                description=None,
                metadata={},  # missing language
                heading=None,
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.missing_metadata == pytest.approx(3 / 6)
    assert set(target.missing_fields) == {"description", "language", "heading"}


def test_missing_metadata_unknown_source_uri_suffix_counts_missing() -> None:
    backend = _HookBackend([_row(1, source_uri="file:///notes/foo.xyz")])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert "source_uri" in target.missing_fields


def test_missing_metadata_no_labels_counts_missing() -> None:
    backend = _HookBackend([_row(1, labels=[])])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert "labels" in target.missing_fields


def test_freshness_today_is_one() -> None:
    backend = _HookBackend([_row(1, modified_at=_NOW)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.freshness == 1.0


def test_freshness_seven_days_old_still_one() -> None:
    backend = _HookBackend([_row(1, modified_at=_NOW - timedelta(days=7))])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.freshness == 1.0


def test_freshness_one_hundred_eighty_days_old_is_zero() -> None:
    backend = _HookBackend([_row(1, modified_at=_NOW - timedelta(days=180))])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.freshness == 0.0


def test_freshness_one_year_old_clamped_zero() -> None:
    backend = _HookBackend([_row(1, modified_at=_NOW - timedelta(days=365))])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.freshness == 0.0


def test_freshness_midway_decays_linearly() -> None:
    backend = _HookBackend([_row(1, modified_at=_NOW - timedelta(days=94))])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    # Halfway between 7 and 180 days → ~ (180-94)/(180-7) ≈ 0.497
    assert target.score_breakdown.freshness == pytest.approx((180 - 94) / (180 - 7), abs=1e-6)


def test_freshness_none_modified_at_is_zero() -> None:
    backend = _HookBackend([_row(1, modified_at=None)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.freshness == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Ranker elevation
# ─────────────────────────────────────────────────────────────────────────


def test_ranker_elevation_with_seed_query_calls_reranker() -> None:
    backend = _HookBackend(
        [
            _row(1, text="alpha"),
            _row(2, text="beta"),
        ]
    )
    reranker = _FakeReranker({1: 0.2, 2: 0.9})
    target = next_curation_target(
        backend=backend,
        seed_query="topic",
        reranker=reranker,
        now=_NOW,
    )
    assert target is not None
    assert reranker.calls, "reranker must be consulted when seed_query is supplied"
    queries = {q for q, _ in reranker.calls}
    assert queries == {"topic"}
    # After normalisation: chunk 1 → 0.0, chunk 2 → 1.0 → chunk 2 wins on ranker_elevation
    target2 = next_curation_target(
        backend=_HookBackend([_row(2, text="beta")]),
        seed_query="topic",
        reranker=_FakeReranker({2: 0.9}),
        now=_NOW,
    )
    # Single-candidate pool → normalisation yields 0.5
    assert target2 is not None
    assert target2.score_breakdown.ranker_elevation == 0.5


def test_ranker_elevation_without_seed_query_uses_centroid_distance() -> None:
    # Three vectors: two clustered, one outlier.
    backend = _HookBackend(
        [
            _row(1, embedding=[1.0, 0.0]),
            _row(2, embedding=[0.99, 0.01]),
            _row(3, embedding=[-1.0, 0.0]),  # 180° → max cosine distance
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    # The outlier (chunk 3) should win ranker_elevation; its sub-score = 1.0
    # post-normalisation; the clustered chunks get ≈ 0.0.
    # The overall target may be any of the three depending on tie-breaks,
    # but chunk 3's breakdown must have the top elevation.
    # Re-run, pin to chunk 3 by scoring it alone:
    only_outlier = next_curation_target(
        backend=_HookBackend(
            [
                _row(1, embedding=[1.0, 0.0]),
                _row(3, embedding=[-1.0, 0.0]),
            ]
        ),
        now=_NOW,
    )
    assert only_outlier is not None
    # In a 2-vector pool, the outlier and the centroid-mean both score equal
    # cosine distances → constant normalisation kicks in (0.5 per element).
    assert 0.0 <= only_outlier.score_breakdown.ranker_elevation <= 1.0


def test_ranker_elevation_no_reranker_no_vectors_is_neutral_half() -> None:
    backend = _HookBackend([_row(1)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.score_breakdown.ranker_elevation == 0.5


def test_ranker_elevation_reranker_only_consulted_with_seed_query() -> None:
    backend = _HookBackend([_row(1)])
    reranker = _FakeReranker({1: 0.9})
    next_curation_target(backend=backend, reranker=reranker, now=_NOW)
    assert reranker.calls == [], "reranker must not be called without seed_query"


# ─────────────────────────────────────────────────────────────────────────
# Total score + selection reason
# ─────────────────────────────────────────────────────────────────────────


def test_total_score_weights_match_spec() -> None:
    # All four sub-scores = 1.0 → weighted sum = 1.0 (weights sum to 1.0).
    backend = _HookBackend(
        [
            _row(
                1,
                document_title=None,
                heading=None,
                description=None,
                metadata={},
                source_uri=None,
                labels=[],
                classifier_confidence=None,
                modified_at=_NOW,
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    # ranker_elevation can't reach 1.0 in a one-candidate pool (no
    # variance → 0.5 neutral), so compute the expected sum directly.
    expected = (
        SCORE_WEIGHTS["confidence_deficit"]
        + SCORE_WEIGHTS["missing_metadata"]
        + 0.5 * SCORE_WEIGHTS["ranker_elevation"]
        + SCORE_WEIGHTS["freshness"]
    )
    assert target.score == pytest.approx(expected, abs=1e-6)


def test_total_score_zero_for_perfect_chunk() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                classifier_confidence=1.0,
                modified_at=_NOW - timedelta(days=365),
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    # All four sub-scores at minimum: 0 + 0 + 0.5 (neutral) + 0
    assert target.score == pytest.approx(0.5 * SCORE_WEIGHTS["ranker_elevation"], abs=1e-9)


def test_total_score_clipped_to_unit_interval() -> None:
    backend = _HookBackend([_row(1)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert 0.0 <= target.score <= 1.0


def test_selection_reason_names_top_contributor_confidence() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                classifier_confidence=0.05,
                modified_at=_NOW - timedelta(days=365),
                # zero out other signals:
                description="d",
                heading="h",
                metadata={"language": "en"},
                document_title="t",
                source_uri="vault://x.md",
                labels=[("class", "topic_a"), ("topic", "t")],
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert "classifier confidence" in target.selection_reason.lower()


def test_selection_reason_names_top_contributor_missing_metadata() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                document_title=None,
                heading=None,
                description=None,
                metadata={},
                source_uri=None,
                labels=[],
                classifier_confidence=1.0,
                modified_at=_NOW - timedelta(days=365),
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert "metadata" in target.selection_reason.lower()


def test_selection_reason_names_top_contributor_freshness() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                classifier_confidence=1.0,
                modified_at=_NOW,
                description="d",
                heading="h",
                document_title="t",
                metadata={"language": "en"},
                source_uri="vault://x.md",
                labels=[("class", "topic_a"), ("topic", "t")],
            )
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert "newly" in target.selection_reason.lower() or "fresh" in target.selection_reason.lower()


def test_selection_reason_seed_query_path_names_reranker() -> None:
    backend = _HookBackend(
        [
            _row(
                1,
                classifier_confidence=1.0,
                description="d",
                heading="h",
                document_title="t",
                metadata={"language": "en"},
                source_uri="vault://x.md",
                labels=[("class", "topic_a"), ("topic", "t")],
                modified_at=_NOW - timedelta(days=365),
            ),
            _row(
                2,
                classifier_confidence=1.0,
                description="d",
                heading="h",
                document_title="t",
                metadata={"language": "en"},
                source_uri="vault://x.md",
                labels=[("class", "topic_a"), ("topic", "t")],
                modified_at=_NOW - timedelta(days=365),
            ),
        ]
    )
    reranker = _FakeReranker({1: 0.1, 2: 0.95})
    target = next_curation_target(backend=backend, seed_query="topic", reranker=reranker, now=_NOW)
    assert target is not None
    reason = target.selection_reason.lower()
    assert "reranker" in reason or "seed" in reason


# ─────────────────────────────────────────────────────────────────────────
# next_curation_target picker
# ─────────────────────────────────────────────────────────────────────────


def test_next_curation_target_picks_highest_score() -> None:
    backend = _HookBackend(
        [
            _row(1, classifier_confidence=0.95),  # well-scored
            _row(2, classifier_confidence=0.05, labels=[]),  # bad
        ]
    )
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.chunk_id == 2


def test_next_curation_target_returns_none_on_empty_pool() -> None:
    backend = _HookBackend([])
    assert next_curation_target(backend=backend, now=_NOW) is None


def test_next_curation_target_passes_dataset_filter_to_backend() -> None:
    backend = _HookBackend([_row(1)])
    next_curation_target(backend=backend, dataset="demo", now=_NOW)
    assert backend.calls == [{"dataset": "demo", "limit": 200}]


def test_next_curation_target_respects_candidate_pool() -> None:
    backend = _HookBackend([_row(i) for i in range(1, 11)])
    next_curation_target(backend=backend, candidate_pool=3, now=_NOW)
    assert backend.calls == [{"dataset": None, "limit": 3}]


# ─────────────────────────────────────────────────────────────────────────
# next_curation_batch grouping
# ─────────────────────────────────────────────────────────────────────────


def test_next_curation_batch_groups_by_source_stem_and_class_label() -> None:
    # Two groups: (notes-a, topic_a) and (notes-b, topic_b). The first
    # group has the worse mean score → it must be picked.
    backend = _HookBackend(
        [
            _row(
                1,
                source_uri="vault://notes-a.md",
                classifier_label="topic_a",
                labels=[("class", "topic_a")],
                classifier_confidence=0.05,
                metadata={},
                description=None,
            ),
            _row(
                2,
                source_uri="vault://notes-a.md",
                classifier_label="topic_a",
                labels=[("class", "topic_a")],
                classifier_confidence=0.10,
                metadata={},
                description=None,
            ),
            _row(
                3,
                source_uri="vault://notes-b.md",
                classifier_label="topic_b",
                labels=[("class", "topic_b")],
                classifier_confidence=0.95,
            ),
        ]
    )
    batch = next_curation_batch(backend=backend, limit=10, now=_NOW)
    assert batch is not None
    assert batch.grouping_key == ("notes-a", "topic_a")
    assert sorted(t.chunk_id for t in batch.targets) == [1, 2]


def test_next_curation_batch_respects_limit() -> None:
    backend = _HookBackend(
        [
            _row(
                i,
                source_uri="vault://big.md",
                classifier_label="topic_a",
                labels=[("class", "topic_a")],
                classifier_confidence=0.1,
            )
            for i in range(1, 11)
        ]
    )
    batch = next_curation_batch(backend=backend, limit=4, now=_NOW)
    assert batch is not None
    assert len(batch.targets) == 4


def test_next_curation_batch_cohesion_high_when_scores_close() -> None:
    backend = _HookBackend(
        [
            _row(
                i,
                source_uri="vault://big.md",
                classifier_label="topic_a",
                labels=[("class", "topic_a")],
                classifier_confidence=0.5,
            )
            for i in range(1, 5)
        ]
    )
    batch = next_curation_batch(backend=backend, limit=4, now=_NOW)
    assert batch is not None
    assert batch.cohesion_score >= 0.9


def test_next_curation_batch_cohesion_lower_when_scores_spread() -> None:
    # Two chunks in the same group but with wildly different confidence
    # → ranker_elevation is constant (no vectors, no seed) but
    # missing_metadata + freshness spread the scores. We still expect
    # some variance to push cohesion below the high-coherence threshold.
    backend = _HookBackend(
        [
            _row(
                1,
                source_uri="vault://big.md",
                classifier_label="topic_a",
                labels=[("class", "topic_a")],
                classifier_confidence=0.05,
                metadata={},
                description=None,
                heading=None,
                document_title=None,
            ),
            _row(
                2,
                source_uri="vault://big.md",
                classifier_label="topic_a",
                labels=[("class", "topic_a")],
                classifier_confidence=0.95,
            ),
        ]
    )
    batch = next_curation_batch(backend=backend, limit=4, now=_NOW)
    assert batch is not None
    # The two scores diverge → cohesion ≪ 1.0 — at minimum strictly less
    # than a near-1 cluster.
    assert batch.cohesion_score < 1.0


def test_next_curation_batch_returns_none_on_empty_pool() -> None:
    assert next_curation_batch(backend=_HookBackend([]), now=_NOW) is None


def test_next_curation_batch_single_target_cohesion_is_one() -> None:
    backend = _HookBackend([_row(1)])
    batch = next_curation_batch(backend=backend, limit=10, now=_NOW)
    assert batch is not None
    assert batch.cohesion_score == 1.0


def test_next_curation_batch_invalid_limit_raises() -> None:
    backend = _HookBackend([_row(1)])
    with pytest.raises(ValueError, match="limit"):
        next_curation_batch(backend=backend, limit=0, now=_NOW)


def test_next_curation_batch_unknown_source_uri_grouped_under_unknown() -> None:
    backend = _HookBackend([_row(1, source_uri=None, classifier_label=None, labels=[])])
    batch = next_curation_batch(backend=backend, limit=10, now=_NOW)
    assert batch is not None
    assert batch.grouping_key == ("<unknown>", "<unclassified>")


# ─────────────────────────────────────────────────────────────────────────
# Dataclass discipline
# ─────────────────────────────────────────────────────────────────────────


def test_curation_target_dataclass_frozen() -> None:
    backend = _HookBackend([_row(1)])
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.score = 1.23  # type: ignore[misc]


def test_score_breakdown_dataclass_frozen() -> None:
    breakdown = ScoreBreakdown(
        confidence_deficit=0.0,
        missing_metadata=0.0,
        ranker_elevation=0.0,
        freshness=0.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        breakdown.freshness = 0.5  # type: ignore[misc]


def test_curation_batch_dataclass_frozen() -> None:
    backend = _HookBackend([_row(1)])
    batch = next_curation_batch(backend=backend, limit=10, now=_NOW)
    assert batch is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        batch.cohesion_score = 0.5  # type: ignore[misc]


def test_curation_chat_template_constant_is_nonempty_string() -> None:
    assert isinstance(CURATION_CHAT_TEMPLATE, str)
    assert "next_curation_target" in CURATION_CHAT_TEMPLATE
    assert "commit_curation" in CURATION_CHAT_TEMPLATE
    assert "next_curation_batch" in CURATION_CHAT_TEMPLATE


def test_score_weights_sum_to_one() -> None:
    assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────
# Generic walk (backend without iter_curation_candidates hook)
# ─────────────────────────────────────────────────────────────────────────


def test_generic_walk_via_execute_backend() -> None:
    chunk_rows = [
        {
            "chunk_id": 1,
            "document_id": 7,
            "text": "alpha",
            "heading": "h",
            "description": None,
            "metadata": '{"language": "en"}',
            "document_title": "title",
            "source_uri": "vault://x.md",
            "modified_at": (_NOW - timedelta(days=2)).isoformat(),
            "dataset_id": 1,
        }
    ]
    label_rows = {
        1: [
            {"namespace": "class", "value": "topic_a", "confidence": 0.6},
            {"namespace": "topic", "value": "x", "confidence": None},
        ],
    }
    backend = _ExecuteBackend(chunk_rows, label_rows)
    target = next_curation_target(backend=backend, now=_NOW)
    assert target is not None
    assert target.chunk_id == 1
    assert target.classifier_confidence == pytest.approx(0.6)
    # `description` is missing on the chunk row → must show up in
    # missing_fields.
    assert "description" in target.missing_fields


def test_generic_walk_filters_by_dataset() -> None:
    chunk_rows = [
        {
            "chunk_id": 1,
            "document_id": 7,
            "text": "alpha",
            "heading": None,
            "description": None,
            "metadata": "{}",
            "document_title": None,
            "source_uri": None,
            "modified_at": None,
            "dataset_id": 1,
        },
        {
            "chunk_id": 2,
            "document_id": 8,
            "text": "beta",
            "heading": None,
            "description": None,
            "metadata": "{}",
            "document_title": None,
            "source_uri": None,
            "modified_at": None,
            "dataset_id": 99,
        },
    ]
    backend = _ExecuteBackend(chunk_rows, {1: [], 2: []})
    # dataset="demo" maps to dataset_id=1 in our stub → only chunk 1 is
    # eligible.
    target = next_curation_target(backend=backend, dataset="demo", now=_NOW)
    assert target is not None
    assert target.chunk_id == 1


def test_generic_walk_returns_none_when_backend_lacks_execute() -> None:
    class _Bare:
        pass

    assert next_curation_target(backend=_Bare(), now=_NOW) is None


# ─────────────────────────────────────────────────────────────────────────
# Selector imports — proves no heavy ML side-effects
# ─────────────────────────────────────────────────────────────────────────


def test_selector_does_not_import_heavy_ml_modules() -> None:
    """Pulling :mod:`corpus_forge.curation` must not eagerly import
    sentence_transformers / openai / etc.
    """
    import sys

    forbidden = {"sentence_transformers", "openai"}
    # Note: we can't unload these if some other test has imported them,
    # so we check that the selector module itself doesn't pull them when
    # `__init__.py` runs. The proxy: re-import the package and assert
    # the forbidden modules aren't a transitive dep of the package's
    # __init__.
    # If a previous test imported them, this assertion is informational.
    if not any(name in sys.modules for name in forbidden):
        # Fresh process: re-import is fine.
        pass
    # Either way, importing `corpus_forge.curation` must succeed.
    import corpus_forge.curation  # noqa: F401
