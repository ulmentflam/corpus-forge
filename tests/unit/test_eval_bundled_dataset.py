"""R3-06 — bundled `forge_self` gold-set unit pins.

The bundled JSONL must:

- Parse cleanly via ``load_gold``.
- Contain ≥ 20 queries (the master plan's minimum).
- Every row must carry parallel ``content_hashes`` so the runner's
  drift fallback works even when chunk ids rotate.
- Provenance doc (``forge_self.corpus.md``) must exist alongside.
"""

from __future__ import annotations

from pathlib import Path

# Path is fixed relative to the package, not the tests dir.
_BUNDLED = (
    Path(__file__).resolve().parents[2] / "corpus_forge" / "eval" / "datasets" / "forge_self.jsonl"
)
_PROVENANCE = _BUNDLED.with_suffix(".corpus.md")


def test_bundled_gold_set_exists():
    assert _BUNDLED.exists(), f"bundled gold set missing at {_BUNDLED}"


def test_provenance_doc_exists():
    assert _PROVENANCE.exists(), f"provenance doc missing at {_PROVENANCE}"


def test_bundled_gold_set_parses():
    from corpus_forge.eval.dataset import load_gold

    queries = load_gold(_BUNDLED)
    assert len(queries) >= 20, f"gold set too small: {len(queries)} queries (need >= 20)"


def test_every_row_has_content_hashes():
    from corpus_forge.eval.dataset import load_gold

    queries = load_gold(_BUNDLED)
    missing = [q.query_id for q in queries if not q.content_hashes]
    assert not missing, (
        f"every gold row must carry content_hashes for drift tolerance; missing on: {missing}"
    )


def test_query_ids_are_unique():
    from corpus_forge.eval.dataset import load_gold

    queries = load_gold(_BUNDLED)
    ids = [q.query_id for q in queries]
    assert len(ids) == len(set(ids)), f"duplicate query_ids: {[i for i in ids if ids.count(i) > 1]}"


def test_every_row_has_at_least_one_relevant_id():
    from corpus_forge.eval.dataset import load_gold

    queries = load_gold(_BUNDLED)
    empties = [q.query_id for q in queries if len(q.relevant_chunk_ids) == 0]
    assert not empties, f"rows with empty relevant_chunk_ids: {empties}"


def test_relevant_ids_and_hashes_parallel_length():
    from corpus_forge.eval.dataset import load_gold

    queries = load_gold(_BUNDLED)
    bad = [
        q.query_id
        for q in queries
        if q.content_hashes is not None and len(q.content_hashes) != len(q.relevant_chunk_ids)
    ]
    # Loader already enforces this, but double-belt: assert here so a
    # corrupted bundled file fails loudly in CI rather than at runtime.
    assert not bad, f"rows with mismatched ids/hashes length: {bad}"
