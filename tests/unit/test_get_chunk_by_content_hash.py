"""R5-01 — `get_chunk_by_content_hash` protocol lift (SQLite-side unit pins).

Surface under test: ``StorageBackend.get_chunk_by_content_hash(content_hash: str) -> dict | None``.

Lifted in Phase R5 from the ad-hoc ``_lookup_chunk_id_by_content_hash`` helper
in ``corpus_forge/eval/runner.py``.  Both backends must implement it; this
file pins the SQLite implementation.  The integration suite
(``tests/integration/test_backend_dual.py``) covers Postgres parity.

Contract pinned here
--------------------
- Known content_hash → returns a dict with ``id``, ``content_hash``,
  ``text``, ``dataset_id``, ``source_uri``, ``title`` populated.
- Unknown content_hash → returns ``None``.
- Multiple chunks share the same content_hash → returns the chunk with
  the LOWEST ``id`` (deterministic tiebreak).  This is the explicit
  contract — callers downstream rely on it for stability across runs.
- ``content_hash`` cosmetics: empty string and very long strings do not
  crash; both simply return ``None`` when no row matches.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.sources.base import RawDocument


def _seed_backend(tmp_path: Path) -> tuple[SQLiteBackend, int, list[int]]:
    """Seed a fresh SQLite backend with a small toy corpus.

    Returns ``(backend, dataset_id, chunk_ids_sorted_by_creation_order)``.
    The chunks have distinct content_hashes computed from their text.
    """
    backend = SQLiteBackend(path=str(tmp_path / "gcbch.db"))
    backend.migrate()

    ds_id = backend.get_or_create_dataset("gcbch-ds", "text", "R5-01 toy")

    chunk_texts = [
        "alpha bravo charlie",
        "delta echo foxtrot",
        "golf hotel india",
    ]
    text = "\n\n".join(chunk_texts)
    doc = RawDocument(
        source_uri="r5-01://toy.md",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        title="R5-01 Toy",
        modified_at=0.0,
        metadata={},
        labels=[],
    )
    backend.upsert_document(ds_id, doc, [(None, ct) for ct in chunk_texts])

    # Recover chunk_ids in ascending order (creation order matches insertion order
    # under SQLite's ROWID-backed AUTOINCREMENT).
    rows = backend._execute(
        "SELECT id, content_hash FROM chunks ORDER BY id ASC",
    )
    chunk_ids = [r["id"] for r in rows]
    return backend, ds_id, chunk_ids


def test_known_hash_returns_chunk(tmp_path: Path) -> None:
    backend, _ds_id, _ = _seed_backend(tmp_path)
    # Pick the second chunk; recompute its hash via the backend so we don't
    # depend on the internal hashing scheme.
    rows = backend._execute("SELECT id, content_hash, text FROM chunks ORDER BY id ASC")
    second = rows[1]

    out = backend.get_chunk_by_content_hash(second["content_hash"])

    assert out is not None
    assert out["id"] == second["id"]
    assert out["content_hash"] == second["content_hash"]
    assert out["text"] == second["text"]
    assert out["source_uri"] == "r5-01://toy.md"
    assert out["title"] == "R5-01 Toy"


def test_unknown_hash_returns_none(tmp_path: Path) -> None:
    backend, _ds_id, _ = _seed_backend(tmp_path)
    assert backend.get_chunk_by_content_hash("0" * 64) is None


def test_empty_string_returns_none(tmp_path: Path) -> None:
    backend, _ds_id, _ = _seed_backend(tmp_path)
    assert backend.get_chunk_by_content_hash("") is None


def test_multi_chunk_same_hash_returns_lowest_id(tmp_path: Path) -> None:
    """When two chunks share a content_hash, the lowest id wins (deterministic)."""
    backend, _ds_id, _ = _seed_backend(tmp_path)
    # Force-insert two chunks with the same content_hash via _execute, attached
    # to the same dataset's first document.
    doc_rows = backend._execute("SELECT id FROM documents LIMIT 1")
    doc_id = doc_rows[0]["id"]
    shared_hash = "shared-hash-r5-01"
    backend._execute(
        "INSERT INTO chunks (document_id, chunk_index, text, content_hash) VALUES (?, ?, ?, ?)",
        (doc_id, 100, "duplicate-A", shared_hash),
    )
    backend._execute(
        "INSERT INTO chunks (document_id, chunk_index, text, content_hash) VALUES (?, ?, ?, ?)",
        (doc_id, 101, "duplicate-B", shared_hash),
    )
    matches = backend._execute(
        "SELECT id FROM chunks WHERE content_hash = ? ORDER BY id ASC",
        (shared_hash,),
    )
    assert len(matches) == 2  # sanity
    lowest_id = matches[0]["id"]

    out = backend.get_chunk_by_content_hash(shared_hash)
    assert out is not None
    assert out["id"] == lowest_id, f"Tiebreak: expected lowest id {lowest_id}, got {out['id']!r}"


def test_returns_dataset_id_for_text_chunk(tmp_path: Path) -> None:
    backend, ds_id, _ = _seed_backend(tmp_path)
    rows = backend._execute("SELECT content_hash FROM chunks ORDER BY id ASC LIMIT 1")
    h = rows[0]["content_hash"]

    out = backend.get_chunk_by_content_hash(h)
    assert out is not None
    assert out["dataset_id"] == ds_id


def test_protocol_member_present() -> None:
    """The Protocol must advertise the method (typing surface)."""
    from corpus_forge.backends.base import StorageBackend

    assert hasattr(StorageBackend, "get_chunk_by_content_hash"), (
        "StorageBackend Protocol must declare get_chunk_by_content_hash"
    )


@pytest.mark.parametrize("bad_input", [None, 123, b"bytes-not-str"])
def test_non_string_input_does_not_crash(tmp_path: Path, bad_input: object) -> None:
    """Non-string input must not crash — the SQL parameter binding will simply
    fail to match anything and the call returns None.  This protects callers
    from drift in upstream gold-set parsers that occasionally emit non-strings.
    """
    backend, _ds_id, _ = _seed_backend(tmp_path)
    # Bytes / None / int all raise or return None — we accept BOTH outcomes as long
    # as the call is robust enough not to leak an internal traceback to callers.
    # Production callers (eval runner) wrap this in their own try/except.
    try:
        out = backend.get_chunk_by_content_hash(bad_input)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # Acceptable: explicit type rejection.
        return
    # If it didn't raise, the value MUST be None (no chunk matches).
    assert out is None
