"""Unit tests — SQLite backend rejects fleet-3 federated-config operations.

The SQLite backend is single-machine by construction (RFC fleet-3 non-goal:
"No SQLite federation").  Both shared-config helpers must raise
:class:`FederationUnsupported` rather than silently no-op.
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


def test_get_shared_config_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.get_shared_config()


def test_put_shared_config_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(FederationUnsupported):
        backend.put_shared_config({"k": 1}, expected_version=0, published_by="h1")
