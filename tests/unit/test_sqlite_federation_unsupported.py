"""Unit tests — SQLite backend rejects fleet-2 claim operations.

The SQLite backend is single-machine by construction (RFC fleet-2 non-goal:
"No SQLite federation").  All three claim methods must raise
:class:`FederationUnsupported` rather than silently no-op or let two hosts
duplicate GPU compute.
"""

from __future__ import annotations

import pytest

from corpus_forge.backends.base import FederationUnsupported
from corpus_forge.backends.sqlite import SQLiteBackend


@pytest.fixture
def backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def test_claim_chunks_for_embedding_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.claim_chunks_for_embedding(embedder_id=1, host_id="h1", batch=10, lease_ttl=600)


def test_release_claims_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.release_claims(embedder_id=1, host_id="h1", chunk_ids=[1, 2, 3])


def test_expire_stale_claims_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.expire_stale_claims()


def test_expire_stale_claims_with_embedder_id_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.expire_stale_claims(embedder_id=1)


def test_count_stale_claims_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.count_stale_claims()


def test_count_stale_claims_with_embedder_id_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.count_stale_claims(embedder_id=1)


def test_federation_unsupported_is_runtime_error() -> None:
    """FederationUnsupported subclasses RuntimeError so generic handlers catch it."""
    assert issubclass(FederationUnsupported, RuntimeError)
