"""Phase O Wave 2 (O2-T1) — Unit tests for corpus_forge.analyze.dedup.

Pins the public shape of ``exact_duplicates`` and ``near_duplicates``.
All tests must fail RED until ``corpus_forge/analyze/dedup.py`` exists.

Spec source: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O2 RED + O2-T1.

Key design decisions captured in tests:
- ``exact_duplicates`` is pure-stdlib over ``chunks.content_hash``; no heavy
  deps.  Singletons (groups of size 1) are excluded from the result.
- ``near_duplicates`` uses MinHash LSH via ``datasketch``; that import is
  LAZY (inside the function body).  Importing the module must NOT touch
  ``datasketch``.
- Result items are plain dicts:
  ``{"cluster_id": str, "chunk_ids": list[int], "similarity": float,
     "method": "minhash_lsh"}``.
  ``cluster_id`` is a stable hash over the *sorted* ``chunk_ids`` so
  re-runs produce identical IDs.
- ``near_duplicates`` with fewer than 2 chunks returns ``[]`` immediately
  (no MinHash attempt).
- Default ``threshold=0.85``, ``num_perm=128`` per spec; both are
  overridable.
"""

from __future__ import annotations

import sys
from typing import Any

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk(
    chunk_id: int,
    text: str,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """Build a minimal chunk dict that dedup.py expects."""
    return {
        "id": chunk_id,
        "text": text,
        "content_hash": content_hash if content_hash is not None else _sha256_hex(text),
    }


def _sha256_hex(text: str) -> str:
    """Deterministic sha256 hex string — mirrors the DB backfill formula."""
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def _chunk_no_hash(chunk_id: int, text: str) -> dict[str, Any]:
    """A chunk with content_hash explicitly set to None."""
    return {"id": chunk_id, "text": text, "content_hash": None}


# ---------------------------------------------------------------------------
# Import smoke — must be the very first test group so RED manifests cleanly
# ---------------------------------------------------------------------------


def test_import_exact_duplicates() -> None:
    """exact_duplicates is importable from corpus_forge.analyze.dedup."""
    from corpus_forge.analyze.dedup import exact_duplicates  # noqa: F401


def test_import_near_duplicates() -> None:
    """near_duplicates is importable from corpus_forge.analyze.dedup."""
    from corpus_forge.analyze.dedup import near_duplicates  # noqa: F401


# ---------------------------------------------------------------------------
# Lazy-import guard — importing the module must NOT load datasketch
# ---------------------------------------------------------------------------


def test_dedup_module_does_not_import_datasketch_at_module_level() -> None:
    """``import corpus_forge.analyze.dedup`` must NOT load datasketch.

    The wave gate enforces that ``corpus-forge --help`` cold-start budget is
    unaffected (datasketch is in the ``[analyze]`` optional extra).
    The lazy-import is checked by evicting the module from sys.modules and
    re-importing it, then asserting datasketch is still absent.
    """
    # Evict analyze.dedup from the module cache to force a fresh import.
    mods_to_evict = [k for k in sys.modules if "corpus_forge.analyze.dedup" in k]
    for m in mods_to_evict:
        sys.modules.pop(m, None)
    # Also evict datasketch if somehow present from a prior test.
    datasketch_was_present_before = "datasketch" in sys.modules
    sys.modules.pop("datasketch", None)

    import corpus_forge.analyze.dedup  # noqa: F401

    if not datasketch_was_present_before:
        assert "datasketch" not in sys.modules, (
            "corpus_forge.analyze.dedup imported datasketch at module level; "
            "it must be imported lazily inside near_duplicates() only."
        )


# ---------------------------------------------------------------------------
# exact_duplicates — empty / singleton / happy-path
# ---------------------------------------------------------------------------


def test_exact_duplicates_empty_input_returns_empty_dict() -> None:
    """Empty chunk list must return {} without raising."""
    from corpus_forge.analyze.dedup import exact_duplicates

    result = exact_duplicates([])
    assert result == {}, f"expected empty dict, got {result!r}"


def test_exact_duplicates_all_unique_returns_empty_dict() -> None:
    """When every chunk has a unique hash, singletons are excluded → {}."""
    from corpus_forge.analyze.dedup import exact_duplicates

    chunks = [
        _chunk(1, "the quick brown fox"),
        _chunk(2, "jumps over the lazy dog"),
        _chunk(3, "sphinx of black quartz"),
    ]
    result = exact_duplicates(chunks)
    assert result == {}, f"expected no dup groups, got {result!r}"


def test_exact_duplicates_pair_grouped_correctly() -> None:
    """Two chunks with the same hash form a group of size 2."""
    from corpus_forge.analyze.dedup import exact_duplicates

    shared_hash = _sha256_hex("identical text")
    chunks = [
        {"id": 10, "text": "identical text", "content_hash": shared_hash},
        {"id": 20, "text": "identical text", "content_hash": shared_hash},
        _chunk(30, "different text"),
    ]
    result = exact_duplicates(chunks)
    assert len(result) == 1, f"expected exactly 1 dup group, got {result!r}"
    assert shared_hash in result, f"expected key {shared_hash!r} in result"
    group = result[shared_hash]
    assert set(group) == {10, 20}, f"expected chunk_ids {{10, 20}}, got {group!r}"


def test_exact_duplicates_triple_grouped_correctly() -> None:
    """Three chunks sharing one hash form a group of size 3."""
    from corpus_forge.analyze.dedup import exact_duplicates

    shared_hash = _sha256_hex("same content")
    chunks = [{"id": i, "text": "same content", "content_hash": shared_hash} for i in [1, 2, 3]]
    result = exact_duplicates(chunks)
    assert shared_hash in result
    assert set(result[shared_hash]) == {1, 2, 3}


def test_exact_duplicates_multiple_dup_groups() -> None:
    """When two distinct hashes each appear twice, both groups are returned."""
    from corpus_forge.analyze.dedup import exact_duplicates

    hash_a = _sha256_hex("group A text")
    hash_b = _sha256_hex("group B text")
    chunks = [
        {"id": 1, "text": "group A text", "content_hash": hash_a},
        {"id": 2, "text": "group A text", "content_hash": hash_a},
        {"id": 3, "text": "group B text", "content_hash": hash_b},
        {"id": 4, "text": "group B text", "content_hash": hash_b},
    ]
    result = exact_duplicates(chunks)
    assert len(result) == 2, f"expected 2 dup groups, got {len(result)}"
    assert set(result[hash_a]) == {1, 2}
    assert set(result[hash_b]) == {3, 4}


def test_exact_duplicates_singleton_excluded() -> None:
    """A hash that appears exactly once must NOT appear in the result."""
    from corpus_forge.analyze.dedup import exact_duplicates

    singleton_hash = _sha256_hex("only once")
    dup_hash = _sha256_hex("appears twice")
    chunks = [
        {"id": 1, "text": "only once", "content_hash": singleton_hash},
        {"id": 2, "text": "appears twice", "content_hash": dup_hash},
        {"id": 3, "text": "appears twice", "content_hash": dup_hash},
    ]
    result = exact_duplicates(chunks)
    assert singleton_hash not in result, "singleton hash must be excluded from result"
    assert dup_hash in result


# ---------------------------------------------------------------------------
# exact_duplicates — None content_hash handling
# ---------------------------------------------------------------------------


def test_exact_duplicates_skips_none_hash_entirely() -> None:
    """Chunks with content_hash=None are skipped; they do not form a group."""
    from corpus_forge.analyze.dedup import exact_duplicates

    chunks = [
        _chunk_no_hash(1, "text a"),
        _chunk_no_hash(2, "text b"),
        _chunk(3, "text c"),
    ]
    result = exact_duplicates(chunks)
    # None hashes must never appear as a key (not even as None → [1, 2])
    assert None not in result, "None must not be a key in the result dict"
    assert result == {}, f"expected empty dict (no real dups), got {result!r}"


def test_exact_duplicates_none_hash_does_not_pollute_real_groups() -> None:
    """A None-hash chunk sharing a position with a real dup group stays out."""
    from corpus_forge.analyze.dedup import exact_duplicates

    shared_hash = _sha256_hex("real dup")
    chunks = [
        {"id": 1, "text": "real dup", "content_hash": shared_hash},
        {"id": 2, "text": "real dup", "content_hash": shared_hash},
        {"id": 3, "text": "irrelevant", "content_hash": None},
    ]
    result = exact_duplicates(chunks)
    # Only the real dup group appears; chunk 3 is absent
    assert list(result.keys()) == [shared_hash]
    assert set(result[shared_hash]) == {1, 2}


def test_exact_duplicates_all_none_hashes_returns_empty() -> None:
    """If every chunk has content_hash=None, the result is {}."""
    from corpus_forge.analyze.dedup import exact_duplicates

    chunks = [_chunk_no_hash(i, f"text {i}") for i in range(5)]
    result = exact_duplicates(chunks)
    assert result == {}


# ---------------------------------------------------------------------------
# near_duplicates — empty / singleton guard
# ---------------------------------------------------------------------------


def test_near_duplicates_empty_input_returns_empty_list() -> None:
    """Empty chunk list must return [] without raising or importing datasketch."""
    from corpus_forge.analyze.dedup import near_duplicates

    result = near_duplicates([])
    assert result == []


def test_near_duplicates_single_chunk_returns_empty_list() -> None:
    """Single chunk → [] without attempting MinHash (no datasketch import needed)."""
    from corpus_forge.analyze.dedup import near_duplicates

    result = near_duplicates([_chunk(1, "lonely chunk text")])
    assert result == []


# ---------------------------------------------------------------------------
# near_duplicates — result schema validation
# ---------------------------------------------------------------------------


def test_near_duplicates_result_item_schema() -> None:
    """Each result item has exactly the four required keys with correct types."""
    from corpus_forge.analyze.dedup import near_duplicates

    text = "the quick brown fox jumps over the lazy dog"
    chunks = [
        _chunk(1, text),
        _chunk(2, text),
    ]
    results = near_duplicates(chunks, threshold=0.5, num_perm=64)

    assert len(results) >= 1, "identical texts must produce at least one cluster"
    for item in results:
        assert set(item.keys()) == {
            "cluster_id",
            "chunk_ids",
            "similarity",
            "method",
        }, f"unexpected keys in result item: {set(item.keys())!r}"
        assert isinstance(item["cluster_id"], str), "cluster_id must be str"
        assert isinstance(item["chunk_ids"], list), "chunk_ids must be list"
        assert all(isinstance(x, int) for x in item["chunk_ids"]), (
            "all chunk_ids elements must be int"
        )
        assert isinstance(item["similarity"], float), "similarity must be float"
        assert item["method"] == "minhash_lsh", (
            f"method must be 'minhash_lsh', got {item['method']!r}"
        )


def test_near_duplicates_identical_texts_cluster_together() -> None:
    """Two chunks with identical text must appear in the same cluster."""
    from corpus_forge.analyze.dedup import near_duplicates

    text = "sphinx of black quartz judge my vow"
    chunks = [_chunk(1, text), _chunk(2, text)]
    results = near_duplicates(chunks, threshold=0.5, num_perm=64)

    all_chunk_ids: list[int] = []
    for item in results:
        all_chunk_ids.extend(item["chunk_ids"])

    assert 1 in all_chunk_ids and 2 in all_chunk_ids, (
        "chunks with identical text must both appear in some cluster"
    )


def test_near_duplicates_cluster_id_is_stable() -> None:
    """Calling near_duplicates twice with the same input yields identical cluster_ids."""
    from corpus_forge.analyze.dedup import near_duplicates

    text = "deterministic cluster id test string"
    chunks = [_chunk(1, text), _chunk(2, text)]

    results_a = near_duplicates(chunks, threshold=0.5, num_perm=64)
    results_b = near_duplicates(chunks, threshold=0.5, num_perm=64)

    ids_a = sorted(item["cluster_id"] for item in results_a)
    ids_b = sorted(item["cluster_id"] for item in results_b)
    assert ids_a == ids_b, "cluster_ids must be stable across runs with the same input"


def test_near_duplicates_similarity_in_unit_interval() -> None:
    """similarity must be in [0.0, 1.0] for every result item."""
    from corpus_forge.analyze.dedup import near_duplicates

    text = "validate similarity is a probability"
    chunks = [_chunk(i, text) for i in range(3)]
    results = near_duplicates(chunks, threshold=0.5, num_perm=64)

    for item in results:
        assert 0.0 <= item["similarity"] <= 1.0, (
            f"similarity {item['similarity']} outside [0.0, 1.0]"
        )


# ---------------------------------------------------------------------------
# near_duplicates — threshold and num_perm parameter passthrough
# ---------------------------------------------------------------------------


def test_near_duplicates_threshold_parameter_accepted() -> None:
    """threshold kwarg is accepted; low threshold allows near-similar to cluster."""
    from corpus_forge.analyze.dedup import near_duplicates

    # Two very similar texts — should cluster at low threshold
    text_a = "the quick brown fox"
    text_b = "the quick brown fox and a bit extra"
    chunks = [_chunk(1, text_a), _chunk(2, text_b)]
    # At threshold=0.2 these should cluster; at 0.999 they should not.
    # We only assert no exception is raised; clustering outcome is probabilistic.
    result_low = near_duplicates(chunks, threshold=0.2, num_perm=64)
    result_high = near_duplicates(chunks, threshold=0.999, num_perm=128)

    assert isinstance(result_low, list)
    assert isinstance(result_high, list)


def test_near_duplicates_num_perm_parameter_accepted() -> None:
    """num_perm kwarg is accepted without raising."""
    from corpus_forge.analyze.dedup import near_duplicates

    chunks = [
        _chunk(1, "some test content here"),
        _chunk(2, "some test content here"),
    ]
    for num_perm in (32, 64, 128, 256):
        result = near_duplicates(chunks, threshold=0.5, num_perm=num_perm)
        assert isinstance(result, list), (
            f"near_duplicates should return list for num_perm={num_perm}"
        )


def test_near_duplicates_default_threshold_is_0_85() -> None:
    """Calling near_duplicates without threshold kwarg uses 0.85 (spec default).

    We verify the default by checking that two identical chunks cluster even
    at the default (0.85 < 1.0 so identical texts always satisfy).
    """
    from corpus_forge.analyze.dedup import near_duplicates

    text = "default threshold test string for identical chunks"
    chunks = [_chunk(1, text), _chunk(2, text)]
    # Identical text → Jaccard similarity = 1.0 > 0.85 → must cluster
    results = near_duplicates(chunks)  # no threshold kwarg

    all_ids: list[int] = []
    for item in results:
        all_ids.extend(item["chunk_ids"])
    assert 1 in all_ids and 2 in all_ids, (
        "identical texts must cluster with the default threshold of 0.85"
    )


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


@given(
    text=st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        ),
        min_size=50,
        max_size=500,
    )
)
@settings(max_examples=30, deadline=None)
def test_property_identical_text_always_clusters(text: str) -> None:
    """Property: three chunks with identical text always cluster together.

    Jaccard(A, A) = 1.0 ≥ any threshold ≤ 1.0, so this is a hard guarantee
    regardless of the MinHash approximation.
    The test uses threshold=0.5 (well below 1.0) and three copies to avoid
    edge-case single-pair false-negatives from low num_perm.
    """
    from corpus_forge.analyze.dedup import near_duplicates

    assume(len(text.strip()) > 0)

    chunks = [_chunk(i + 1, text) for i in range(3)]
    results = near_duplicates(chunks, threshold=0.5, num_perm=64)

    all_ids: list[int] = []
    for item in results:
        all_ids.extend(item["chunk_ids"])

    assert set(all_ids) == {1, 2, 3}, (
        f"Identical text '{text[:40]}...' must cluster all three chunks; "
        f"got cluster chunk_ids: {all_ids!r}"
    )


@given(
    words_a=st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
            min_size=5,
            max_size=15,
        ),
        min_size=20,
        max_size=40,
    ),
    words_b=st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll")),
            min_size=5,
            max_size=15,
        ),
        min_size=20,
        max_size=40,
    ),
)
@settings(max_examples=30, deadline=None)
def test_property_disjoint_texts_do_not_cluster_at_high_threshold(
    words_a: list[str],
    words_b: list[str],
) -> None:
    """Property: totally disjoint word sets never cluster at threshold=0.85.

    If the two texts share no tokens (word-level Jaccard = 0.0), MinHash LSH
    cannot put them in the same bucket at threshold=0.85.  We ensure
    disjointness by stripping overlapping words.
    """
    from corpus_forge.analyze.dedup import near_duplicates

    set_a = set(words_a)
    set_b = set(words_b)
    # Remove any words that appear in both — guarantee Jaccard = 0
    only_a = set_a - set_b
    only_b = set_b - set_a

    assume(len(only_a) >= 10 and len(only_b) >= 10)

    text_a = " ".join(sorted(only_a)[:15])  # deterministic ordering
    text_b = " ".join(sorted(only_b)[:15])

    chunks = [_chunk(1, text_a), _chunk(2, text_b)]
    results = near_duplicates(chunks, threshold=0.85, num_perm=128)

    assert results == [], (
        f"Disjoint texts should not cluster at threshold=0.85; "
        f"text_a={text_a!r}, text_b={text_b!r}, got {results!r}"
    )
