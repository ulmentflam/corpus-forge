"""Unit tests for the prune-admin module (``corpus_forge.admin.prune``).

These tests run against a fake backend that supplies candidates via the
``iter_curation_candidates`` hook (same pattern as
``tests/unit/test_curation_selector.py``) plus a couple of optional
hooks the prune surface consults (``iter_chunk_feedback``,
``delete_chunks_by_ids``).

The MinHash branch is exercised via a ``monkeypatch`` over
``corpus_forge.admin.prune._minhash_available`` so the tests never
require the ``rfc-nlp-data-quality-signals`` module to actually exist.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from corpus_forge.admin import PruneCandidate, PruneReport, prune_dataset
from corpus_forge.admin import prune as prune_mod

# ─────────────────────────────────────────────────────────────────────────
# Fixtures / fake backend
# ─────────────────────────────────────────────────────────────────────────


_NOW = datetime(2026, 5, 22, tzinfo=UTC)
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
) -> dict[str, Any]:
    """Build a backend row dict in the shape the curation selector expects.

    Defaults represent a "well-filled" chunk; callers override fields to
    push specific signals.
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
        "embedding": None,
    }


class _FakeBackend:
    """Fake backend covering the prune-module surface.

    Records delete calls so tests can assert the apply path was (or was
    NOT) reached. Optionally yields feedback rows via
    ``iter_chunk_feedback``.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        feedback_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._rows = rows
        self._feedback_rows = feedback_rows or []
        self.delete_calls: list[list[int]] = []
        self.iter_calls: list[dict[str, Any]] = []

    def iter_curation_candidates(
        self, *, dataset: str | None, limit: int
    ) -> Iterable[dict[str, Any]]:
        self.iter_calls.append({"dataset": dataset, "limit": limit})
        yield from self._rows[:limit]

    def iter_chunk_feedback(self) -> Iterable[dict[str, Any]]:
        yield from self._feedback_rows

    def delete_chunks_by_ids(self, chunk_ids: list[int]) -> int:
        self.delete_calls.append(list(chunk_ids))
        return len(chunk_ids)


class _ExecuteOnlyBackend:
    """Backend exposing ``_execute`` (no hook). Records executed SQL.

    Used to verify the bulk-DELETE fallback fires correctly when a
    backend doesn't ship ``delete_chunks_by_ids``.
    """

    def __init__(self, rows: list[dict[str, Any]], *, name: str = "PostgresBackend") -> None:
        self._rows = rows
        self.__class__.__name__ = name  # for `type(backend).__name__` branching
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def iter_curation_candidates(
        self, *, dataset: str | None, limit: int
    ) -> Iterable[dict[str, Any]]:
        yield from self._rows[:limit]

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.executed.append((sql, params))
        return []


# ─────────────────────────────────────────────────────────────────────────
# Dry-run / empty-pool basics
# ─────────────────────────────────────────────────────────────────────────


def test_dry_run_default_does_not_delete() -> None:
    backend = _FakeBackend([_row(i, classifier_confidence=0.1) for i in range(1, 11)])
    report = prune_dataset(backend, dataset="x", now=_NOW)
    assert report.applied is False
    assert report.deleted == 0
    assert backend.delete_calls == []
    # Default percentile is 10 → ceil(10 * 0.1) == 1 selected row.
    assert len(report.selected) == 1


def test_empty_pool_returns_empty_report() -> None:
    backend = _FakeBackend([])
    report = prune_dataset(backend, dataset="x", now=_NOW)
    assert report.considered == 0
    assert report.selected == []
    assert report.applied is False
    assert report.deleted == 0
    assert report.summary_by_source == {}


# ─────────────────────────────────────────────────────────────────────────
# Score ordering invariants
# ─────────────────────────────────────────────────────────────────────────


def test_score_ordering_invariants(monkeypatch: pytest.MonkeyPatch) -> None:
    # MinHash off → duplicate_density is 0 for every chunk. Differences
    # come from confidence_deficit + missing_metadata + freshness_inverted.
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [
        # 1: pristine → minimal prune score.
        _row(1, classifier_confidence=1.0, modified_at=_NOW),
        # 2: mid-tier → some missing metadata.
        _row(
            2,
            classifier_confidence=0.5,
            modified_at=_NOW - timedelta(days=30),
            description=None,
            heading=None,
        ),
        # 3: worst — no classifier, no metadata, stale.
        _row(
            3,
            classifier_confidence=None,
            modified_at=_NOW - timedelta(days=365),
            document_title=None,
            heading=None,
            description=None,
            metadata={},
            source_uri=None,
            labels=[],
        ),
    ]
    backend = _FakeBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    # All three selected; first must be the worst (chunk 3).
    assert [c.chunk_id for c in report.selected] == [3, 2, 1]
    # Scores must be in monotonic-descending order.
    scores = [c.prune_score for c in report.selected]
    assert scores == sorted(scores, reverse=True)
    # Bounds check.
    assert all(0.0 <= c.prune_score <= 1.0 for c in report.selected)


# ─────────────────────────────────────────────────────────────────────────
# Percentile controls selection count
# ─────────────────────────────────────────────────────────────────────────


def test_percentile_controls_selection_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [_row(i, classifier_confidence=0.1) for i in range(1, 21)]
    backend = _FakeBackend(rows)

    r10 = prune_dataset(backend, dataset="x", percentile=10, now=_NOW)
    assert len(r10.selected) == math.ceil(20 * 0.10) == 2

    r50 = prune_dataset(backend, dataset="x", percentile=50, now=_NOW)
    assert len(r50.selected) == math.ceil(20 * 0.50) == 10

    r100 = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    assert len(r100.selected) == 20

    r0 = prune_dataset(backend, dataset="x", percentile=0, now=_NOW)
    assert r0.selected == []


def test_percentile_out_of_range_raises() -> None:
    backend = _FakeBackend([_row(1)])
    with pytest.raises(ValueError, match="percentile"):
        prune_dataset(backend, dataset="x", percentile=-1, now=_NOW)
    with pytest.raises(ValueError, match="percentile"):
        prune_dataset(backend, dataset="x", percentile=101, now=_NOW)


# ─────────────────────────────────────────────────────────────────────────
# Feedback-drag signal
# ─────────────────────────────────────────────────────────────────────────


def test_feedback_drag_flips_on_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    # Two identical rows. Chunk 7 has a rejected feedback row; chunk 8 doesn't.
    rows = [
        _row(7, classifier_confidence=0.5),
        _row(8, classifier_confidence=0.5),
    ]
    backend = _FakeBackend(
        rows,
        feedback_rows=[{"chunk_id": 7, "kind": "rejected", "rating": None}],
    )
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    # Chunk 7 must sort strictly higher than chunk 8.
    by_id = {c.chunk_id: c for c in report.selected}
    assert by_id[7].prune_score > by_id[8].prune_score
    # And feedback_drag = 1.0 on 7, 0.0 on 8.
    assert by_id[7].sub_scores["feedback_drag"] == 1.0
    assert by_id[8].sub_scores["feedback_drag"] == 0.0


def test_feedback_drag_flips_on_negative_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [_row(1, classifier_confidence=0.5)]
    backend = _FakeBackend(
        rows,
        feedback_rows=[{"chunk_id": 1, "kind": "comment", "rating": -1}],
    )
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    assert report.selected[0].sub_scores["feedback_drag"] == 1.0


def test_feedback_drag_zero_when_no_hook_and_no_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    class _BareBackend:
        """Backend without _execute or iter_chunk_feedback — feedback must degrade to 0.0."""

        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def iter_curation_candidates(
            self, *, dataset: str | None, limit: int
        ) -> Iterable[dict[str, Any]]:
            yield from self._rows[:limit]

    backend = _BareBackend([_row(1, classifier_confidence=0.5)])
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    assert report.selected[0].sub_scores["feedback_drag"] == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Duplicate-density signal
# ─────────────────────────────────────────────────────────────────────────


def test_duplicate_density_skipped_when_minhash_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [_row(i, classifier_confidence=0.5) for i in range(1, 4)]
    backend = _FakeBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    # Every selected candidate has duplicate_density = 0.0.
    for cand in report.selected:
        assert cand.sub_scores["duplicate_density"] == 0.0
    # The report exposes the availability flag — promoted off of
    # `selected[0].sub_scores` so candidate shape is uniform.
    assert report.duplicate_density_available is False
    # No candidate carries the legacy `duplicate_density_available` key.
    for cand in report.selected:
        assert "duplicate_density_available" not in cand.sub_scores


def test_duplicate_density_used_when_minhash_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub a minhash module that gives chunk 1 a very small jaccard
    # distance (high duplicate density) and chunk 2 a large distance.
    class _StubMinhash:
        @staticmethod
        def jaccard_neighbor_distance(*, chunk_id: int, text: str) -> float:
            _ = text
            return 0.05 if chunk_id == 1 else 0.95

    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: True)
    # Inject the stub into corpus_forge.quality.minhash.
    import sys
    import types

    pkg = types.ModuleType("corpus_forge.quality")
    pkg.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "corpus_forge.quality", pkg)
    monkeypatch.setitem(sys.modules, "corpus_forge.quality.minhash", _StubMinhash)

    rows = [
        _row(1, classifier_confidence=0.5),
        _row(2, classifier_confidence=0.5),
    ]
    backend = _FakeBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    by_id = {c.chunk_id: c for c in report.selected}
    # Chunk 1 — near-duplicate — must outrank chunk 2.
    assert by_id[1].sub_scores["duplicate_density"] > by_id[2].sub_scores["duplicate_density"]
    assert by_id[1].prune_score > by_id[2].prune_score
    # Report-level flag shows the signal ran. Candidate sub_scores remain
    # uniform across the selection (no special-case head element).
    assert report.duplicate_density_available is True
    for cand in report.selected:
        assert "duplicate_density_available" not in cand.sub_scores


# ─────────────────────────────────────────────────────────────────────────
# Apply path
# ─────────────────────────────────────────────────────────────────────────


def test_apply_calls_delete_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [_row(i, classifier_confidence=0.1) for i in range(1, 11)]
    backend = _FakeBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=30, apply=True, now=_NOW)
    expected_count = math.ceil(10 * 0.30)
    assert len(report.selected) == expected_count
    assert backend.delete_calls, "apply=True must invoke the delete path"
    deleted_ids = backend.delete_calls[-1]
    assert deleted_ids == [c.chunk_id for c in report.selected]
    assert report.applied is True
    assert report.deleted == expected_count


def test_apply_with_execute_fallback_emits_bulk_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the backend lacks `delete_chunks_by_ids` but has `_execute` +
    a Postgres-shaped class name, the prune surface must emit one bulk
    ``DELETE ... WHERE id = ANY(%s)`` statement."""

    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [_row(i, classifier_confidence=0.1) for i in range(1, 6)]
    backend = _ExecuteOnlyBackend(rows, name="PostgresBackend")
    report = prune_dataset(backend, dataset="x", percentile=40, apply=True, now=_NOW)
    expected = math.ceil(5 * 0.40)
    assert len(report.selected) == expected
    # The recorded statements include exactly one DELETE.
    deletes = [s for s, _p in backend.executed if "DELETE" in s.upper()]
    assert len(deletes) == 1
    assert "ANY(%s)" in deletes[0]
    assert report.deleted == expected


def test_apply_with_empty_selection_does_not_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [_row(i) for i in range(1, 6)]
    backend = _FakeBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=0, apply=True, now=_NOW)
    assert report.selected == []
    assert backend.delete_calls == []
    # `apply=True` with nothing to delete is a no-op — `applied` stays False.
    assert report.applied is False
    assert report.deleted == 0


# ─────────────────────────────────────────────────────────────────────────
# Summary / dataclass discipline
# ─────────────────────────────────────────────────────────────────────────


def test_summary_by_source_groups_by_stem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    rows = [
        _row(1, source_uri="vault://docs/a.md", classifier_confidence=0.0),
        _row(2, source_uri="vault://docs/a.md", classifier_confidence=0.0),
        _row(3, source_uri="vault://docs/b.md", classifier_confidence=0.0),
        _row(4, source_uri=None, classifier_confidence=0.0),
    ]
    backend = _FakeBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=100, now=_NOW)
    assert report.summary_by_source == {"a": 2, "b": 1, "<unknown>": 1}


def test_dataclasses_frozen() -> None:
    candidate = PruneCandidate(
        chunk_id=1,
        document_id=2,
        source_uri="vault://x.md",
        prune_score=0.5,
        sub_scores={"confidence_deficit": 0.5},
        reason="test",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.prune_score = 0.9  # type: ignore[misc]

    report = PruneReport(
        dataset="x",
        percentile=10,
        considered=0,
        selected=[],
        applied=False,
        deleted=0,
        summary_by_source={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.deleted = 1  # type: ignore[misc]


def test_dataset_label_propagates_to_backend_call() -> None:
    backend = _FakeBackend([_row(1)])
    prune_dataset(backend, dataset="demo", now=_NOW)
    assert backend.iter_calls == [{"dataset": "demo", "limit": 2000}]


def test_candidate_pool_overrides_default() -> None:
    backend = _FakeBackend([_row(i) for i in range(1, 6)])
    prune_dataset(backend, dataset="x", candidate_pool=3, now=_NOW)
    assert backend.iter_calls[-1]["limit"] == 3


# ─────────────────────────────────────────────────────────────────────────
# SQLite chunked-DELETE fallback
# ─────────────────────────────────────────────────────────────────────────


class _SQLiteShapedBackend:
    """Execute-only backend that mimics SQLite: ``_paramstyle = 'qmark'``
    and no Postgres-y substring in the class name. No
    ``delete_chunks_by_ids`` hook so the chunked-IN path fires.
    """

    _paramstyle = "qmark"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def iter_curation_candidates(
        self, *, dataset: str | None, limit: int
    ) -> Iterable[dict[str, Any]]:
        yield from self._rows[:limit]

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.executed.append((sql, params))
        return []


def test_apply_sqlite_chunked_delete_via_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLite-shaped backend falls back to chunked ``DELETE … WHERE id IN (?, …)``."""

    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)
    # Shrink the batch size so a modest fixture exercises chunking.
    monkeypatch.setattr(prune_mod, "_SQLITE_BATCH_SIZE", 3)

    # 7 candidates with percentile=100 → 7 deletes → ceil(7/3) = 3 batches.
    rows = [_row(i, classifier_confidence=0.0) for i in range(1, 8)]
    backend = _SQLiteShapedBackend(rows)
    report = prune_dataset(backend, dataset="x", percentile=100, apply=True, now=_NOW)

    assert report.applied is True
    assert report.deleted == 7

    deletes = [(sql, params) for sql, params in backend.executed if "DELETE" in sql.upper()]
    # Three batches: sizes 3, 3, 1.
    assert len(deletes) == 3
    batch_sizes = [len(params) for _sql, params in deletes]
    assert batch_sizes == [3, 3, 1]

    for sql, params in deletes:
        # Schema-prefix asymmetry vs Postgres: SQLite path uses
        # unqualified `chunks` (no `corpus.` prefix).
        assert "corpus." not in sql
        assert "DELETE FROM chunks" in sql
        # ``?`` placeholders, exactly one per parameter.
        assert sql.count("?") == len(params)
        # Postgres `ANY(%s)` must not appear on the SQLite path.
        assert "ANY(" not in sql


# ─────────────────────────────────────────────────────────────────────────
# Postgres dispatch via _paramstyle capability probe
# ─────────────────────────────────────────────────────────────────────────


def test_paramstyle_pyformat_routes_to_postgres_bulk_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend whose class name doesn't say ``Postgres`` but whose
    ``_paramstyle`` is ``"pyformat"`` must still take the bulk-ANY(%s)
    path. This locks in the capability probe over the brittle class-name
    check."""

    monkeypatch.setattr(prune_mod, "_minhash_available", lambda: False)

    class _OpaqueBackend(_ExecuteOnlyBackend):
        _paramstyle = "pyformat"

    rows = [_row(i, classifier_confidence=0.0) for i in range(1, 6)]
    # Deliberately pick a class name that contains no Postgres hint.
    backend = _OpaqueBackend(rows, name="OpaqueSqlBackend")
    report = prune_dataset(backend, dataset="x", percentile=40, apply=True, now=_NOW)
    expected = math.ceil(5 * 0.40)
    assert report.deleted == expected
    deletes = [s for s, _p in backend.executed if "DELETE" in s.upper()]
    assert len(deletes) == 1
    assert "ANY(%s)" in deletes[0]
    assert "corpus.chunks" in deletes[0]


# ─────────────────────────────────────────────────────────────────────────
# Unknown-dataset safety
# ─────────────────────────────────────────────────────────────────────────


def test_unknown_dataset_raises() -> None:
    """A named dataset that the backend doesn't know must abort the prune.

    The guard is critical under ``apply=True`` — without it the run
    would silently fall through to "walk every dataset", which would
    delete rows from the wrong scope.
    """

    class _NamedDatasetBackend(_FakeBackend):
        def find_dataset_id_by_name(self, name: str) -> int | None:
            _ = name
            return None  # unknown

    backend = _NamedDatasetBackend([_row(1)])
    with pytest.raises(ValueError, match="dataset 'ghost' not found"):
        prune_dataset(backend, dataset="ghost", now=_NOW)
    # No iteration over candidates happens.
    assert backend.iter_calls == []
    assert backend.delete_calls == []


def test_unknown_dataset_resolver_absent_falls_through() -> None:
    """Backends without ``find_dataset_id_by_name`` keep the old behaviour:
    we trust the caller and let the iter step decide. This is the
    bare-minimum API the prune surface accepts."""

    rows = [_row(1)]
    backend = _FakeBackend(rows)
    # The default _FakeBackend has no find_dataset_id_by_name attr.
    assert not hasattr(backend, "find_dataset_id_by_name")
    report = prune_dataset(backend, dataset="anything", now=_NOW)
    assert report.considered == 1


def test_dataset_none_skips_unknown_dataset_check() -> None:
    """When the caller intentionally passes ``dataset=None`` (walk all),
    the unknown-dataset guard must not fire even if the backend would
    resolve names — None is the explicit opt-out."""

    class _NamedDatasetBackend(_FakeBackend):
        def find_dataset_id_by_name(self, name: str) -> int | None:
            _ = name
            return None  # would block, but we never call it

    backend = _NamedDatasetBackend([_row(1)])
    report = prune_dataset(backend, dataset=None, now=_NOW)
    assert report.considered == 1
