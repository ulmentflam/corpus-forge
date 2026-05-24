"""Phase P Wave 3 (P3-T1) — Unit tests for corpus_forge.cag.cache.

Scope guardrail (re-stated per task contract)
----------------------------------------------
corpus-forge is a corpus-side tool.  CAG ships as a **precomputed cache
builder** + **hybrid selector**, NOT an inference server.  The cache is a
JSON file consumed by downstream inference clients; we never run a model from
corpus-forge.

Pins the public API of ``corpus_forge.cag.cache``:

  build_cache(conn, dataset, *, top_k=50, template="chatml", root=None) -> Path
  cache_key(dataset_id, content_hashes, template) -> str
  cache_path(root, dataset, key) -> Path
  list_cached_keys(root, dataset) -> list[str]
  invalidate(root, dataset, content_hash) -> int

Key design decisions captured in tests
---------------------------------------
- ``cache_key`` is ``sha256((dataset_id, sorted(content_hashes), template))``
  truncated to 16 hex chars.  Sorting is internal so input ordering does not
  matter.
- Each cache JSON file records ``content_hashes``, ``dataset``, ``template``,
  ``messages``, ``built_at``, and ``cache_key`` at the top level.
- ``invalidate`` reads each file's ``content_hashes`` list and deletes any
  file that includes the target hash.  Returns the count deleted.
- ``build_cache`` uses a mock ``conn`` (duck-type) plus a monkeypatched
  template resolver to avoid touching a real database or running a model.
- All imports are lazy within the module (no sklearn / no model at module top).

RED state: ``from corpus_forge.cag.cache import ...`` fails with
``ModuleNotFoundError: No module named 'corpus_forge.cag'`` because the
package does not yet exist.

Spec source: task P3-T1, roadmap Phase P Wave 3.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    chunk_id: int,
    content_hash: str,
    text: str = "A representative corpus chunk about domain topic.",
    modified_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, object]:
    """Return a minimal chunk dict matching the corpus_forge chunk row shape."""
    return {
        "id": chunk_id,
        "content_hash": content_hash,
        "text": text,
        "modified_at": modified_at,
    }


def _make_conn(chunks: list[dict[str, object]], dataset_id: int = 42) -> MagicMock:
    """Return a mock connection whose ``execute`` returns ``chunks``."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = chunks
    cursor.fetchone.return_value = {"id": dataset_id}
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    # Also support direct .execute().fetchall() patterns.
    conn.execute.return_value.fetchall.return_value = chunks
    conn.execute.return_value.fetchone.return_value = {"id": dataset_id}
    return conn


def _expected_key(dataset_id: int, hashes: list[str], template: str) -> str:
    """Reference implementation of the cache_key formula."""
    raw = repr((dataset_id, sorted(hashes), template))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. Import smoke
# ---------------------------------------------------------------------------


def test_import_smoke() -> None:
    """All five public names importable from corpus_forge.cag.cache."""
    from corpus_forge.cag.cache import (  # noqa: F401
        build_cache,
        cache_key,
        cache_path,
        invalidate,
        list_cached_keys,
    )


# ---------------------------------------------------------------------------
# 2. cache_key — determinism and ordering invariance
# ---------------------------------------------------------------------------


def test_cache_key_deterministic() -> None:
    """Same inputs produce the same key across multiple calls."""
    from corpus_forge.cag.cache import cache_key

    k1 = cache_key(7, ["aaa", "bbb", "ccc"], "chatml")
    k2 = cache_key(7, ["aaa", "bbb", "ccc"], "chatml")
    assert k1 == k2


def test_cache_key_ordering_invariant() -> None:
    """Input hash list order does not affect the key (sorted internally)."""
    from corpus_forge.cag.cache import cache_key

    k_abc = cache_key(1, ["aaa", "bbb", "ccc"], "chatml")
    k_cba = cache_key(1, ["ccc", "bbb", "aaa"], "chatml")
    k_bac = cache_key(1, ["bbb", "aaa", "ccc"], "chatml")
    assert k_abc == k_cba == k_bac


def test_cache_key_matches_sha256_formula() -> None:
    """Key equals sha256((dataset_id, sorted(hashes), template))[:16]."""
    from corpus_forge.cag.cache import cache_key

    hashes = ["h1", "h2", "h3"]
    dataset_id = 5
    template = "chatml"
    expected = _expected_key(dataset_id, hashes, template)
    assert cache_key(dataset_id, hashes, template) == expected


def test_cache_key_is_16_hex_chars() -> None:
    """Key is exactly 16 lowercase hex characters."""
    from corpus_forge.cag.cache import cache_key

    k = cache_key(1, ["abcdef"], "chatml")
    assert len(k) == 16
    assert all(c in "0123456789abcdef" for c in k)


def test_cache_key_differs_by_template() -> None:
    """Different template names produce different keys for the same dataset/hashes."""
    from corpus_forge.cag.cache import cache_key

    k_chatml = cache_key(1, ["hash1"], "chatml")
    k_llama3 = cache_key(1, ["hash1"], "llama3")
    assert k_chatml != k_llama3


def test_cache_key_differs_by_dataset_id() -> None:
    """Different dataset_ids produce different keys."""
    from corpus_forge.cag.cache import cache_key

    k_a = cache_key(1, ["hash1"], "chatml")
    k_b = cache_key(2, ["hash1"], "chatml")
    assert k_a != k_b


def test_cache_key_differs_by_hash_set() -> None:
    """Different content hash sets produce different keys."""
    from corpus_forge.cag.cache import cache_key

    k_a = cache_key(1, ["hash1"], "chatml")
    k_b = cache_key(1, ["hash2"], "chatml")
    assert k_a != k_b


def test_cache_key_empty_hashes() -> None:
    """Empty hash list is valid and produces a deterministic key."""
    from corpus_forge.cag.cache import cache_key

    k1 = cache_key(1, [], "chatml")
    k2 = cache_key(1, [], "chatml")
    assert k1 == k2
    assert len(k1) == 16


# ---------------------------------------------------------------------------
# 3. cache_path — path construction
# ---------------------------------------------------------------------------


def test_cache_path_resolves_correctly(tmp_path: Path) -> None:
    """cache_path returns root / dataset / '{key}.json'."""
    from corpus_forge.cag.cache import cache_path

    key = "abcd1234ef567890"
    result = cache_path(tmp_path, "my_dataset", key)
    assert result == tmp_path / "my_dataset" / f"{key}.json"


def test_cache_path_dataset_subdirectory(tmp_path: Path) -> None:
    """The dataset name is always an intermediate directory, not inlined in filename."""
    from corpus_forge.cag.cache import cache_path

    result = cache_path(tmp_path, "corpus_alpha", "0000111122223333")
    assert result.parent.name == "corpus_alpha"
    assert result.name == "0000111122223333.json"
    assert result.suffix == ".json"


# ---------------------------------------------------------------------------
# 4. build_cache — writes file at expected path
# ---------------------------------------------------------------------------


def test_build_cache_writes_json_file(tmp_path: Path) -> None:
    """build_cache creates a .json file under root/dataset/<key>.json."""
    from corpus_forge.cag.cache import build_cache

    chunks = [
        _make_chunk(1, "hash_a"),
        _make_chunk(2, "hash_b"),
    ]
    conn = _make_conn(chunks, dataset_id=10)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(10, chunks)),
        patch(
            "corpus_forge.cag.cache._render_template",
            return_value="[SYSTEM]\nChunks here.",
        ),
    ):
        result_path = build_cache(conn, "test_ds", top_k=2, template="chatml", root=tmp_path)

    assert result_path.exists()
    assert result_path.suffix == ".json"
    assert result_path.parent.name == "test_ds"


def test_build_cache_returns_path_object(tmp_path: Path) -> None:
    """build_cache return value is a pathlib.Path."""
    from corpus_forge.cag.cache import build_cache

    chunks = [_make_chunk(1, "hash_x")]
    conn = _make_conn(chunks, dataset_id=3)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(3, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="content"),
    ):
        result = build_cache(conn, "ds", root=tmp_path)

    assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# 5. build_cache — JSON content structure
# ---------------------------------------------------------------------------


def test_build_cache_json_has_required_keys(tmp_path: Path) -> None:
    """Cache JSON contains dataset, template, content_hashes, messages, built_at, cache_key."""
    from corpus_forge.cag.cache import build_cache

    chunks = [
        _make_chunk(1, "aaaa"),
        _make_chunk(2, "bbbb"),
    ]
    conn = _make_conn(chunks, dataset_id=7)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(7, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="rendered text"),
    ):
        path = build_cache(conn, "myds", top_k=2, template="chatml", root=tmp_path)

    data = json.loads(path.read_text())
    assert "dataset" in data
    assert "template" in data
    assert "content_hashes" in data
    assert "messages" in data
    assert "built_at" in data
    assert "cache_key" in data


def test_build_cache_json_dataset_field(tmp_path: Path) -> None:
    """Cache JSON records the dataset name."""
    from corpus_forge.cag.cache import build_cache

    chunks = [_make_chunk(1, "c1")]
    conn = _make_conn(chunks, dataset_id=2)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(2, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="x"),
    ):
        path = build_cache(conn, "special_ds", root=tmp_path)

    data = json.loads(path.read_text())
    assert data["dataset"] == "special_ds"


def test_build_cache_json_template_field(tmp_path: Path) -> None:
    """Cache JSON records the template name used."""
    from corpus_forge.cag.cache import build_cache

    chunks = [_make_chunk(1, "c1")]
    conn = _make_conn(chunks, dataset_id=2)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(2, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="x"),
    ):
        path = build_cache(conn, "ds", template="llama3", root=tmp_path)

    data = json.loads(path.read_text())
    assert data["template"] == "llama3"


def test_build_cache_json_content_hashes_list(tmp_path: Path) -> None:
    """Cache JSON content_hashes is a list of the chunk hashes used."""
    from corpus_forge.cag.cache import build_cache

    chunks = [
        _make_chunk(1, "hash_alpha"),
        _make_chunk(2, "hash_beta"),
    ]
    conn = _make_conn(chunks, dataset_id=9)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(9, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="x"),
    ):
        path = build_cache(conn, "ds", root=tmp_path)

    data = json.loads(path.read_text())
    assert isinstance(data["content_hashes"], list)
    assert set(data["content_hashes"]) == {"hash_alpha", "hash_beta"}


def test_build_cache_json_cache_key_field_matches_formula(tmp_path: Path) -> None:
    """Cache JSON cache_key field matches cache_key(dataset_id, hashes, template)."""
    from corpus_forge.cag.cache import build_cache, cache_key

    chunks = [
        _make_chunk(1, "hx1"),
        _make_chunk(2, "hx2"),
    ]
    dataset_id = 11
    conn = _make_conn(chunks, dataset_id=dataset_id)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(dataset_id, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="rendered"),
    ):
        path = build_cache(conn, "ds", template="chatml", root=tmp_path)

    data = json.loads(path.read_text())
    expected_key = cache_key(dataset_id, [c["content_hash"] for c in chunks], "chatml")
    assert data["cache_key"] == expected_key


def test_build_cache_json_built_at_is_iso_timestamp(tmp_path: Path) -> None:
    """Cache JSON built_at is a parseable ISO 8601 timestamp string."""
    from corpus_forge.cag.cache import build_cache

    chunks = [_make_chunk(1, "hh")]
    conn = _make_conn(chunks, dataset_id=1)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(1, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="x"),
    ):
        path = build_cache(conn, "ds", root=tmp_path)

    data = json.loads(path.read_text())
    ts = data["built_at"]
    assert isinstance(ts, str)
    # Must be parseable — fromisoformat raises ValueError on bad format.
    parsed = datetime.fromisoformat(ts)
    assert isinstance(parsed, datetime)


def test_build_cache_uses_root_override(tmp_path: Path) -> None:
    """build_cache writes under the root= override, not ~/.cache/corpus-forge/cag."""
    from corpus_forge.cag.cache import build_cache

    custom_root = tmp_path / "custom_root"
    chunks = [_make_chunk(1, "hh")]
    conn = _make_conn(chunks, dataset_id=1)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(1, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="x"),
    ):
        path = build_cache(conn, "ds", root=custom_root)

    assert path.is_relative_to(custom_root)


# ---------------------------------------------------------------------------
# 6. list_cached_keys
# ---------------------------------------------------------------------------


def test_list_cached_keys_returns_empty_before_build(tmp_path: Path) -> None:
    """list_cached_keys returns [] for a dataset directory that does not exist."""
    from corpus_forge.cag.cache import list_cached_keys

    keys = list_cached_keys(tmp_path, "nonexistent_ds")
    assert keys == []


def test_list_cached_keys_returns_keys_after_build(tmp_path: Path) -> None:
    """list_cached_keys returns the key string(s) after build_cache writes a file."""
    from corpus_forge.cag.cache import build_cache, list_cached_keys

    chunks = [_make_chunk(1, "hh1"), _make_chunk(2, "hh2")]
    conn = _make_conn(chunks, dataset_id=20)

    with (
        patch("corpus_forge.cag.cache._fetch_chunks", return_value=(20, chunks)),
        patch("corpus_forge.cag.cache._render_template", return_value="x"),
    ):
        built_path = build_cache(conn, "target_ds", root=tmp_path)

    expected_key = built_path.stem
    keys = list_cached_keys(tmp_path, "target_ds")
    assert expected_key in keys


def test_list_cached_keys_multiple_files(tmp_path: Path) -> None:
    """list_cached_keys enumerates all .json files in the dataset directory."""
    from corpus_forge.cag.cache import list_cached_keys

    ds_dir = tmp_path / "multi_ds"
    ds_dir.mkdir(parents=True)
    expected_keys = ["aaaa111122223333", "bbbb444455556666", "cccc777788889999"]
    for key in expected_keys:
        (ds_dir / f"{key}.json").write_text(json.dumps({"cache_key": key, "content_hashes": []}))

    keys = list_cached_keys(tmp_path, "multi_ds")
    assert sorted(keys) == sorted(expected_keys)


def test_list_cached_keys_ignores_non_json_files(tmp_path: Path) -> None:
    """list_cached_keys skips files that are not .json."""
    from corpus_forge.cag.cache import list_cached_keys

    ds_dir = tmp_path / "mixed_ds"
    ds_dir.mkdir(parents=True)
    (ds_dir / "aaaa111122223333.json").write_text(
        json.dumps({"cache_key": "aaaa111122223333", "content_hashes": []})
    )
    (ds_dir / "README.txt").write_text("not a cache file")
    (ds_dir / "something.bak").write_text("also not a cache file")

    keys = list_cached_keys(tmp_path, "mixed_ds")
    assert keys == ["aaaa111122223333"]


# ---------------------------------------------------------------------------
# 7. invalidate
# ---------------------------------------------------------------------------


def test_invalidate_removes_matching_file(tmp_path: Path) -> None:
    """invalidate deletes a cache file whose content_hashes includes the target hash."""
    from corpus_forge.cag.cache import invalidate

    ds_dir = tmp_path / "inv_ds"
    ds_dir.mkdir(parents=True)
    cache_file = ds_dir / "aaaa111122223333.json"
    cache_file.write_text(
        json.dumps(
            {
                "cache_key": "aaaa111122223333",
                "content_hashes": ["hash_target", "hash_other"],
            }
        )
    )

    count = invalidate(tmp_path, "inv_ds", "hash_target")
    assert count == 1
    assert not cache_file.exists()


def test_invalidate_noop_when_hash_not_present(tmp_path: Path) -> None:
    """invalidate returns 0 and removes nothing when no file matches the hash."""
    from corpus_forge.cag.cache import invalidate

    ds_dir = tmp_path / "noop_ds"
    ds_dir.mkdir(parents=True)
    cache_file = ds_dir / "bbbb222233334444.json"
    cache_file.write_text(
        json.dumps(
            {
                "cache_key": "bbbb222233334444",
                "content_hashes": ["hash_unrelated"],
            }
        )
    )

    count = invalidate(tmp_path, "noop_ds", "hash_missing")
    assert count == 0
    assert cache_file.exists()


def test_invalidate_noop_empty_dataset_directory(tmp_path: Path) -> None:
    """invalidate returns 0 when dataset directory does not exist."""
    from corpus_forge.cag.cache import invalidate

    count = invalidate(tmp_path, "absent_ds", "any_hash")
    assert count == 0


def test_invalidate_removes_only_matching_files(tmp_path: Path) -> None:
    """invalidate removes only files whose content_hashes include the target."""
    from corpus_forge.cag.cache import invalidate

    ds_dir = tmp_path / "partial_ds"
    ds_dir.mkdir(parents=True)

    # This file SHOULD be invalidated.
    match_file = ds_dir / "aaaa000011112222.json"
    match_file.write_text(
        json.dumps(
            {
                "cache_key": "aaaa000011112222",
                "content_hashes": ["shared_hash", "other_hash"],
            }
        )
    )

    # This file should NOT be invalidated.
    keep_file = ds_dir / "bbbb333344445555.json"
    keep_file.write_text(
        json.dumps(
            {
                "cache_key": "bbbb333344445555",
                "content_hashes": ["different_hash"],
            }
        )
    )

    count = invalidate(tmp_path, "partial_ds", "shared_hash")
    assert count == 1
    assert not match_file.exists()
    assert keep_file.exists()


def test_invalidate_multiple_matches(tmp_path: Path) -> None:
    """invalidate removes all files containing the target hash and returns correct count."""
    from corpus_forge.cag.cache import invalidate

    ds_dir = tmp_path / "multi_match_ds"
    ds_dir.mkdir(parents=True)

    for key in ["k1k1k1k1k1k1k1k1", "k2k2k2k2k2k2k2k2"]:
        (ds_dir / f"{key}.json").write_text(
            json.dumps({"cache_key": key, "content_hashes": ["stale_hash"]})
        )
    (ds_dir / "k3k3k3k3k3k3k3k3.json").write_text(
        json.dumps({"cache_key": "k3k3k3k3k3k3k3k3", "content_hashes": ["fresh_hash"]})
    )

    count = invalidate(tmp_path, "multi_match_ds", "stale_hash")
    assert count == 2
    assert not (ds_dir / "k1k1k1k1k1k1k1k1.json").exists()
    assert not (ds_dir / "k2k2k2k2k2k2k2k2.json").exists()
    assert (ds_dir / "k3k3k3k3k3k3k3k3.json").exists()


# ---------------------------------------------------------------------------
# 8. Property test (Hypothesis)
# ---------------------------------------------------------------------------

_SAFE_HASH_CHARS = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=4,
    max_size=64,
)
_HASH_LIST = st.lists(_SAFE_HASH_CHARS, min_size=1, max_size=20)
_TEMPLATE_NAMES = st.sampled_from(["chatml", "llama3", "alpaca", "vicuna", "gemma"])


@given(
    dataset_id=st.integers(min_value=1, max_value=10_000),
    hashes=_HASH_LIST,
    template=_TEMPLATE_NAMES,
)
@settings(max_examples=50)
def test_property_cache_key_ordering_invariant(
    dataset_id: int,
    hashes: list[str],
    template: str,
) -> None:
    """cache_key(d, [a,b,c], t) == cache_key(d, [c,a,b], t) for any ordering."""
    from corpus_forge.cag.cache import cache_key

    shuffled = hashes.copy()
    random.shuffle(shuffled)
    assert cache_key(dataset_id, hashes, template) == cache_key(dataset_id, shuffled, template)
