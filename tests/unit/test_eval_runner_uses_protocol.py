"""R5-01 — `_lookup_chunk_id_by_content_hash` must delegate through the protocol.

The R3 implementation reached around to ``backend._execute`` with backend-class
sniffing (``if backend_cls == "PostgresBackend": ... else: ...``).  R5 lifts the
shape into ``StorageBackend.get_chunk_by_content_hash`` and the runner must now
call that instead.

This test pins the new behaviour by stubbing the protocol method and asserting:

1. ``_lookup_chunk_id_by_content_hash`` calls ``backend.get_chunk_by_content_hash``.
2. It does NOT call ``backend._execute`` (no SQL reach-around).
3. The drift-resolution flow in ``_resolve_gold_ids`` still works end-to-end
   when the chunk_id is missing but the content_hash resolves.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_lookup_uses_get_chunk_by_content_hash() -> None:
    from corpus_forge.eval.runner import _lookup_chunk_id_by_content_hash

    backend = MagicMock()
    backend.get_chunk_by_content_hash.return_value = {"id": 42, "content_hash": "abc123"}

    out = _lookup_chunk_id_by_content_hash(backend, "abc123")

    assert out == 42
    backend.get_chunk_by_content_hash.assert_called_once_with("abc123")


def test_lookup_returns_none_when_protocol_returns_none() -> None:
    from corpus_forge.eval.runner import _lookup_chunk_id_by_content_hash

    backend = MagicMock()
    backend.get_chunk_by_content_hash.return_value = None

    out = _lookup_chunk_id_by_content_hash(backend, "no-such-hash")

    assert out is None
    backend.get_chunk_by_content_hash.assert_called_once_with("no-such-hash")


def test_lookup_does_not_call_execute() -> None:
    """Belt-and-braces: ensure we no longer reach around to ``_execute``."""
    from corpus_forge.eval.runner import _lookup_chunk_id_by_content_hash

    backend = MagicMock()
    backend.get_chunk_by_content_hash.return_value = {"id": 7}
    # _execute MUST NOT be called by the new implementation.

    _lookup_chunk_id_by_content_hash(backend, "h")

    backend._execute.assert_not_called()


def test_lookup_swallows_backend_exception() -> None:
    """If the backend raises, the helper must return None (parity with old behaviour)."""
    from corpus_forge.eval.runner import _lookup_chunk_id_by_content_hash

    backend = MagicMock()
    backend.get_chunk_by_content_hash.side_effect = RuntimeError("boom")

    out = _lookup_chunk_id_by_content_hash(backend, "x")
    assert out is None


def test_resolve_gold_ids_uses_protocol_for_drift() -> None:
    """End-to-end drift flow: id missing → content_hash lookup replaces id."""
    from corpus_forge.eval.runner import _resolve_gold_ids

    # Build a fake gold query carrying id+hash pairs.
    q = MagicMock()
    q.query_id = "q-drift-1"
    q.relevant_chunk_ids = [999]  # missing id
    q.content_hashes = ["hash-found"]
    q.graded = None

    backend = MagicMock()
    # get_chunk(999) → None (id missing)
    backend.get_chunk.return_value = None
    # get_chunk_by_content_hash("hash-found") → resolves to id=12
    backend.get_chunk_by_content_hash.return_value = {"id": 12, "content_hash": "hash-found"}

    resolved_ids, graded = _resolve_gold_ids(q, backend)

    assert resolved_ids == {12}
    assert graded is None
    backend.get_chunk_by_content_hash.assert_called_once_with("hash-found")
    backend._execute.assert_not_called()


def test_resolve_gold_ids_drops_when_neither_resolves() -> None:
    """id missing AND hash missing → entry dropped."""
    from corpus_forge.eval.runner import _resolve_gold_ids

    q = MagicMock()
    q.query_id = "q-drift-2"
    q.relevant_chunk_ids = [999]
    q.content_hashes = ["unknown-hash"]
    q.graded = None

    backend = MagicMock()
    backend.get_chunk.return_value = None
    backend.get_chunk_by_content_hash.return_value = None

    resolved_ids, graded = _resolve_gold_ids(q, backend)

    assert resolved_ids == set()
    assert graded is None


@pytest.mark.parametrize("attr", ["get_chunk_by_content_hash"])
def test_protocol_advertises_method(attr: str) -> None:
    """Belt-and-braces typing pin (mirrors test_get_chunk_by_content_hash.py)."""
    from corpus_forge.backends.base import StorageBackend

    assert hasattr(StorageBackend, attr), f"StorageBackend must declare {attr!r}"
