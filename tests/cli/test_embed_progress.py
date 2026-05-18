"""Phase L Wave 4 — embed progress wrapper + loader logger discipline.

Validates that ``backfill_embedder`` emits the bookending INFO lines via
the shared ``make_progress`` factory and that
``SentenceTransformersEmbedder._load_model`` logs to the
``corpus_forge.embedders.loader`` namespace.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def stub_config():
    """Minimal :class:`Config` stub with one active embedder."""

    embedder_cfg = MagicMock()
    embedder_cfg.name = "e1"
    embedder_cfg.provider = "sentence_transformers"
    embedder_cfg.model_id = "all-MiniLM-L6-v2"
    embedder_cfg.dimension = 4
    embedder_cfg.normalize = True
    embedder_cfg.distance = "cosine"
    embedder_cfg.batch_size = 32
    embedder_cfg.device = "cpu"
    embedder_cfg.api_key_env = "OPENAI_API_KEY"
    embedder_cfg.active = True

    backend_cfg = MagicMock()
    backend_cfg.kind = "sqlite"
    backend_cfg.dsn = ":memory:"
    backend_cfg.schema = "corpus"

    config = MagicMock()
    config.backend = backend_cfg
    config.embedders = [embedder_cfg]
    return config


def _stub_embedder(dim: int = 4):
    """Embedder mock whose ``encode`` returns a tensor of the right shape."""

    emb = MagicMock()

    def _encode(texts):
        return np.zeros((len(list(texts)), dim), dtype=np.float32)

    emb.encode.side_effect = _encode
    emb.warmup = MagicMock()
    return emb


def test_backfill_embedder_emits_bookend_logs(stub_config, caplog):
    """``make_progress`` should auto-emit "started" + "complete" bookends."""

    from corpus_forge import embed as embed_module

    backend = MagicMock()
    backend.register_embedder.return_value = 1
    backend.count_chunks_missing_embedding.return_value = 2
    backend.chunks_missing_embedding.side_effect = [
        [(1, "alpha"), (2, "beta")],
        [],
    ]
    backend.write_embeddings = MagicMock()
    backend.migrate = MagicMock()

    registry_stub = MagicMock()
    registry_stub.register.return_value = _stub_embedder()

    with (
        patch.object(embed_module.Config, "load", return_value=stub_config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        patch.object(embed_module, "registry", registry_stub),
        caplog.at_level(logging.INFO, logger="corpus_forge.embed"),
    ):
        embed_module.backfill_embedder("e1")

    bookend_messages = [r.message for r in caplog.records if "Embedding chunks" in r.message]
    assert any("started: 2 items" in m for m in bookend_messages), (
        f"missing started bookend in {bookend_messages!r}"
    )
    assert any("complete" in m for m in bookend_messages), (
        f"missing complete bookend in {bookend_messages!r}"
    )


def test_backfill_embedder_uses_count_helper_for_total(stub_config):
    """``backfill_embedder`` must consult ``count_chunks_missing_embedding``."""

    from corpus_forge import embed as embed_module

    backend = MagicMock()
    backend.register_embedder.return_value = 1
    backend.count_chunks_missing_embedding.return_value = 42
    backend.chunks_missing_embedding.side_effect = [[], []]
    backend.write_embeddings = MagicMock()
    backend.migrate = MagicMock()

    registry_stub = MagicMock()
    registry_stub.register.return_value = _stub_embedder()

    with (
        patch.object(embed_module.Config, "load", return_value=stub_config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        patch.object(embed_module, "registry", registry_stub),
    ):
        embed_module.backfill_embedder("e1")

    backend.count_chunks_missing_embedding.assert_called_once_with(1)


def _insert_doc(backend, dataset_id: int, source_uri: str, content_hash: str) -> int:
    """Insert a raw documents row and return its id (bypasses upsert)."""

    rows = backend._execute(
        "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, content_hash, ""),
    )
    return int(rows[0]["id"])


def test_count_chunks_missing_embedding_sqlite(tmp_path):
    """``count_chunks_missing_embedding`` returns rows-missing-from-table."""

    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.embedders.registry import EmbedderRegistry

    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()

    dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")
    doc_id = _insert_doc(backend, dataset_id, "s://doc.md", "h0" * 32)
    backend._execute(
        "INSERT INTO chunks (document_id, chunk_index, text, content_hash) VALUES (?, ?, ?, ?)",
        (doc_id, 0, "hello", "h1" * 32),
    )
    backend._execute(
        "INSERT INTO chunks (document_id, chunk_index, text, content_hash) VALUES (?, ?, ?, ?)",
        (doc_id, 1, "world", "h2" * 32),
    )

    reg = EmbedderRegistry()
    embedder = reg.register(
        name="e_tiny",
        provider="sentence_transformers",
        model_id="dummy",
        dimension=4,
        normalized=True,
        distance="cosine",
    )
    embedder_id = backend.register_embedder(embedder)

    # Brand-new embedder against two chunks → both missing.
    assert backend.count_chunks_missing_embedding(embedder_id) == 2

    # Unknown id → 0.
    assert backend.count_chunks_missing_embedding(999_999) == 0


def test_pending_documents_sqlite(tmp_path):
    """``pending_documents`` reports docs with zero chunk rows + samples."""

    from corpus_forge.backends.sqlite import SQLiteBackend

    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()

    dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")
    doc_a = _insert_doc(backend, dataset_id, "s://a.md", "ha" * 32)
    backend._execute(
        "INSERT INTO chunks (document_id, chunk_index, text, content_hash) VALUES (?, ?, ?, ?)",
        (doc_a, 0, "a", "h1" * 32),
    )
    # Docs B and C: not chunked.
    _insert_doc(backend, dataset_id, "s://b.md", "hb" * 32)
    _insert_doc(backend, dataset_id, "s://c.md", "hc" * 32)

    count, samples = backend.pending_documents(limit=5)
    assert count == 2
    assert set(samples) == {"s://b.md", "s://c.md"}


def test_loader_logs_on_model_load(caplog):
    """``_load_model`` writes to ``corpus_forge.embedders.loader``."""

    from corpus_forge.embedders import sentence_transformers as stmod

    embedder = stmod.SentenceTransformersEmbedder(
        name="e1",
        model_id="dummy/model",
        dimension=4,
        device="cpu",
    )

    fake_model = MagicMock()
    with (
        patch.object(stmod, "SENTENCE_TRANSFORMERS_AVAILABLE", True),
        patch.object(stmod, "SentenceTransformer", return_value=fake_model),
        patch("corpus_forge._ml_device.resolve_device", return_value="cpu"),
        caplog.at_level(logging.INFO, logger="corpus_forge.embedders.loader"),
    ):
        embedder._load_model()

    messages = [r.message for r in caplog.records]
    assert any("Loading embedder e1" in m for m in messages), messages
    assert any("Embedder e1 ready" in m for m in messages), messages
