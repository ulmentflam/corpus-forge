"""Regenerate tests/fixtures/curation/selector_baseline.pickle.

Run this script against a CLEAN main-branch install (pre-O3) to re-baseline the
selector golden output whenever the selector's heuristic intentionally changes
(e.g. weight adjustments, new sub-score added to the legacy path).

Usage::

    cd <repo root>
    uv run python tests/fixtures/curation/regenerate_baseline.py

The script prints a human-readable summary of what it wrote so you can verify
the golden values by eye before committing.
"""

from __future__ import annotations

import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path

from corpus_forge.curation.selector import next_curation_batch, next_curation_target

_NOW = datetime(2026, 5, 19, 0, 0, 0, tzinfo=UTC)
_FIXTURE_ROWS_N = 20
_OUT = Path(__file__).parent / "selector_baseline.pickle"


def _make_row(
    chunk_id: int,
    *,
    doc_id: int = 1,
    text: str = "lorem ipsum",
    heading: str | None = "h",
    description: str | None = "d",
    metadata: dict | None = None,
    document_title: str | None = "Fixture Doc",
    source_uri: str | None = "vault://notes/fixture.md",
    modified_at: datetime | None = None,
    labels: list | None = None,
    classifier_label: str | None = "topic_a",
    classifier_confidence: float | None = 0.8,
    embedding: list | None = None,
) -> dict:
    """Build a backend row dict matching the shape _iter_curation_candidates expects."""
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "text": text,
        "heading": heading,
        "description": description,
        "metadata": dict(metadata if metadata is not None else {"language": "en"}),
        "document_title": document_title,
        "source_uri": source_uri,
        "modified_at": modified_at if modified_at is not None else (_NOW - timedelta(days=30)),
        "labels": list(
            labels if labels is not None else [("class", classifier_label or ""), ("topic", "x")]
        ),
        "classifier_label": classifier_label,
        "classifier_confidence": classifier_confidence,
        "embedding": embedding,
    }


def build_fixture_rows() -> list[dict]:
    """Build the canonical 20-chunk deterministic fixture corpus.

    Determinism contract:
    - chunk_id 1..20 (fixed integers)
    - classifier_confidence cycles via ``round(0.05 + (i % 10) * 0.09, 2)``
    - heading present when ``i % 3 != 0``
    - description present when ``i % 4 != 0``
    - metadata has ``language`` key when ``i % 2 == 0``
    - age = ``10 + i * 8`` days old (18..170 days)
    - source_uri groups 5 chunks per doc (doc1..doc4)
    """
    rows = []
    for i in range(1, _FIXTURE_ROWS_N + 1):
        conf = round(0.05 + (i % 10) * 0.09, 2)
        rows.append(
            _make_row(
                chunk_id=i,
                doc_id=((i - 1) // 5) + 1,
                source_uri=f"vault://notes/doc{((i - 1) // 5) + 1}.md",
                classifier_label="topic_a",
                classifier_confidence=conf,
                heading="h" if bool(i % 3) else None,
                description="d" if bool(i % 4) else None,
                metadata={"language": "en"} if i % 2 == 0 else {},
                modified_at=_NOW - timedelta(days=10 + i * 8),
            )
        )
    return rows


class _HookBackend:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def iter_curation_candidates(self, *, dataset: str | None, limit: int):
        yield from self._rows[:limit]


def main() -> None:
    rows = build_fixture_rows()
    backend = _HookBackend(rows)

    batch = next_curation_batch(backend=backend, limit=10, now=_NOW)
    single = next_curation_target(backend=backend, now=_NOW)

    assert batch is not None, "fixture corpus must yield a non-empty batch"
    assert single is not None, "fixture corpus must yield a single target"

    baseline = {
        "batch": batch,
        "single_target": single,
        "now": _NOW,
        "generator": "pre-O3 main @ 0.1.0b6",
    }

    with _OUT.open("wb") as fh:
        pickle.dump(baseline, fh, protocol=4)

    print(f"Wrote: {_OUT}")
    print(f"single_target: chunk_id={single.chunk_id} score={single.score:.6f}")
    print(
        f"  breakdown: cd={single.score_breakdown.confidence_deficit:.4f} "
        f"mm={single.score_breakdown.missing_metadata:.4f} "
        f"re={single.score_breakdown.ranker_elevation:.4f} "
        f"fr={single.score_breakdown.freshness:.4f}"
    )
    print(f"  reason: {single.selection_reason!r}")
    print(f"batch: cohesion={batch.cohesion_score:.6f} grouping_key={batch.grouping_key}")
    print("  targets (in order):")
    for t in batch.targets:
        print(f"    chunk_id={t.chunk_id} score={t.score:.6f} reason={t.selection_reason!r}")


if __name__ == "__main__":
    main()
