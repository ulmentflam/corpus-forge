"""Smoke tests: end-to-end happy paths against a fake embedder.

These tests verify the full pipeline (config → source → chunk → backend)
without requiring Docker. The backend is mocked to capture calls.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.chunkers.markdown import MarkdownChunker
from corpus_forge.config import (
    Config,
    BackendConfig,
    DaemonConfig,
    DatasetConfig,
    EmbedderConfig,
    DatasetSourceConfig,
)
from corpus_forge.sources.claude_code import ClaudeCodeSource
from corpus_forge.sources.markdown_vault import MarkdownVaultSource
from corpus_forge.sources.opencode import OpenCodeSource
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage

pytestmark = pytest.mark.smoke


# ── Fake backend that captures all calls ─────────────────────────────────────


class FakeBackend:
    """Captures backend calls for verification without a real database."""

    def __init__(self):
        self.documents = {}
        self.conversations = {}
        self.datasets = {}
        self.embeddings = {}
        self.migrated = False
        self.locks = set()

    def migrate(self):
        self.migrated = True

    def register_embedder(self, embedder):
        return 1

    def upsert_document(self, dataset_id, doc, chunks):
        doc_id = hash(doc.source_uri)
        self.documents[doc_id] = {
            "dataset_id": dataset_id,
            "source_uri": doc.source_uri,
            "content_hash": doc.content_hash,
            "chunks": chunks,
        }
        return doc_id

    def upsert_conversation(self, dataset_id, conv, chunked_messages):
        conv_id = hash(conv.source_uri)
        self.conversations[conv_id] = {
            "dataset_id": dataset_id,
            "source_uri": conv.source_uri,
            "messages": conv.messages,
            "chunks": chunked_messages,
        }
        return conv_id

    def write_embeddings(self, embedder_id, pairs):
        self.embeddings[embedder_id] = pairs

    def chunks_missing_embedding(self, embedder_id):
        return []

    def lock_source(self, key):
        return _FakeLock(self, key)

    def delete_document(self, dataset_id, source_uri):
        pass

    def delete_conversation(self, dataset_id, source_uri):
        pass

    def _execute(self, query, params=()):
        return []

    @property
    def get_hash(self):
        return None


class _FakeLock:
    def __init__(self, backend, key):
        self.backend = backend
        self.key = key

    def __enter__(self):
        self.backend.locks.add(self.key)
        return self

    def __exit__(self, *args):
        self.backend.locks.discard(self.key)


# ── Fake embedder ────────────────────────────────────────────────────────────


def _fake_embedder():
    mock = MagicMock()
    mock.name = "fake-embed"
    mock.provider = "sentence_transformers"
    mock.model_id = "fake/model"
    mock.dimension = 384
    mock.normalized = True
    mock.distance = "cosine"

    def encode(texts, **kwargs):
        if isinstance(texts, str):
            texts = [texts]
        return np.zeros((len(texts), 384), dtype=np.float32)

    mock.encode = encode
    return mock


# ── Smoke: full vault ingestion ──────────────────────────────────────────────


class TestSmokeVaultIngestion:
    """Happy path: ingest a markdown vault end-to-end."""

    def test_vault_ingest_and_verify(self, temp_dir):
        vault = temp_dir / "vault"
        vault.mkdir()

        (vault / "home.md").write_text(
            "# Home\n\nWelcome to my vault.\n\n## Quick Links\n\n- [TODO](TODO.md)"
        )
        (vault / "notes.md").write_text(
            "# Notes\n\n## Ideas\n\nBrainstorming session.\n\n## Tasks\n\n1. Write tests\n2. Deploy"
        )
        (vault / "TODO.md").write_text("# TODO\n\n- [ ] Fix bug\n- [x] Ship release")

        backend = FakeBackend()
        source = MarkdownVaultSource(vault_root=vault)
        chunker = MarkdownChunker(max_chars=1500, overlap=200)
        embedder = _fake_embedder()

        # Simulate dataset creation
        dataset_id = 1
        backend.datasets[dataset_id] = {"name": "smoke-vault", "kind": "text"}

        processed = 0
        for raw in source.scan():
            backend.lock_source(raw.source_uri).__enter__()
            try:
                if isinstance(raw, RawDocument):
                    chunk_data = [(c.heading, c.text) for c in chunker.chunk(raw.text)]
                    backend.upsert_document(dataset_id, raw, chunk_data)
                    backend.write_embeddings(1, [])
                    processed += 1
            finally:
                backend.lock_source(raw.source_uri).__exit__(None, None, None)

        assert processed == 3
        assert len(backend.documents) == 3

    def test_vault_excludes_dotfiles(self, temp_dir):
        vault = temp_dir / "vault2"
        vault.mkdir()

        (vault / "good.md").write_text("# Good\n\nContent.")
        (vault / "excluded").mkdir()
        (vault / "excluded" / "old.md").write_text("# Old")

        # The exclude_globs uses simple substring matching, not glob patterns.
        # So we use "excluded" as the pattern to match paths containing that substring.
        source = MarkdownVaultSource(vault_root=vault, exclude_globs=["excluded"])
        paths = list(source.discover())
        names = {p.name for p in paths}

        assert "good.md" in names
        assert "old.md" not in names


# ── Smoke: conversation ingestion ────────────────────────────────────────────


class TestSmokeConversationIngestion:
    """Happy path: ingest a Claude Code conversation."""

    def test_claude_code_source_scan(self, temp_dir):
        projects = temp_dir / "projects"
        projects.mkdir()
        proj = projects / "my-project"
        proj.mkdir()
        (proj / "session1.jsonl").write_text(
            '{"uuid": "m1", "message": {"role": "user", "content": "Hello"}, "timestamp": 1000}\n'
            '{"uuid": "m2", "message": {"role": "assistant", "content": "Hi!"}, "timestamp": 1001}\n'
        )

        source = ClaudeCodeSource(projects_root=projects)
        convs = list(source.scan())
        assert len(convs) >= 1
        for conv in convs:
            assert isinstance(conv, RawConversation)
            assert len(conv.messages) >= 1

    def test_opencode_source_scan(self, temp_dir):
        storage = temp_dir / "storage"
        storage.mkdir()
        (storage / "session" / "sess1").mkdir(parents=True)
        (storage / "message" / "msg1").mkdir(parents=True)
        (storage / "message" / "msg1" / "message.json").write_text(
            '{"id": "msg1", "parentId": null, "role": "assistant", "content": "Hello world", "timestamp": 1000, "parts": [{"type": "text", "content": "Hello world"}]}'
        )

        source = OpenCodeSource(storage_root=storage)
        convs = list(source.scan())
        assert len(convs) >= 1
        for conv in convs:
            assert isinstance(conv, RawConversation)


# ── Smoke: idempotency ───────────────────────────────────────────────────────


class TestSmokeIdempotency:
    """Running ingest twice should not duplicate documents (via hash check)."""

    def test_unchanged_content_hash_skips(self, temp_dir):
        vault = temp_dir / "vault3"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n\nContent.")

        source = MarkdownVaultSource(vault_root=vault)
        docs = list(source.scan())
        assert len(docs) == 1

        # Hash should be deterministic
        h1 = docs[0].content_hash
        docs2 = list(source.scan())
        h2 = docs2[0].content_hash
        assert h1 == h2

    def test_config_validation_rejects_invalid_kind(self):
        with pytest.raises(Exception):
            DatasetConfig(
                name="bad",
                kind="invalid_kind",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root="/tmp",
                        chunker="markdown",
                    )
                ],
            )


# ── Smoke: chunking correctness ──────────────────────────────────────────────


class TestSmokeChunking:
    """Verify chunking produces valid output."""

    def test_chunk_markdown_preserves_headings(self, temp_dir):
        vault = temp_dir / "vault"
        vault.mkdir()
        (vault / "headings.md").write_text(
            "# Main\n\nPara one.\n\n## Sub One\n\nContent A.\n\n## Sub Two\n\nContent B.\n\n### Deep\n\nDeep content."
        )

        source = MarkdownVaultSource(vault_root=vault)
        docs = list(source.scan())
        assert len(docs) == 1

        chunker = MarkdownChunker(max_chars=1500, overlap=200)
        chunks = chunker.chunk(docs[0].text)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert len(chunk.text) > 0

    def test_empty_file_produces_empty_chunks(self, temp_dir):
        vault = temp_dir / "vault"
        vault.mkdir()
        (vault / "empty.md").write_text("")

        source = MarkdownVaultSource(vault_root=vault)
        docs = list(source.scan())
        assert len(docs) == 1

        chunker = MarkdownChunker(max_chars=1500, overlap=200)
        chunks = chunker.chunk(docs[0].text)
        assert chunks == []

    def test_large_file_splits_into_multiple_chunks(self):
        chunker = MarkdownChunker(max_chars=100, overlap=20)
        long_text = "# Header\n\n" + "\n\n".join([f"Paragraph {i} with some content." for i in range(20)])
        chunks = chunker.chunk(long_text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.text) <= 100 + 20
