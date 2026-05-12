"""B-18 E2E smoke test: ingest a markdown vault into a SQLite corpus.db.

One happy-path test.  No Docker, no network, no real model weights.

Strategy
--------
* Build a 3-file markdown vault under ``tmp_path / "vault/"``.
* Construct a Config pointing at a SQLite backend at ``tmp_path / "corpus.db"``
  with a deterministic MarkdownChunker config (max_chars=500, overlap=50).
* Patch ``corpus_forge.ingest.registry`` to return a tiny FakeEmbedder so the
  registry never tries to load sentence-transformers or call OpenAI.
* Call ``corpus_forge.ingest.ingest_once(config)``.
* Open the resulting db file directly with ``sqlite3.connect`` and assert rows
  exist in datasets / sources / documents / chunks / embedders and that every
  chunk has a non-NULL content_hash.

Expected row counts (deterministic given max_chars=500):
  - 3 documents  (one per .md file)
  - 3 chunks     (each simple document fits in one chunk at max_chars=500)
  - 1 embedder   (our fake embedder)
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import numpy as np

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.ingest import ingest_once

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_MAX_CHARS = 500
_CHUNK_OVERLAP = 50

# Expected row counts after ingesting a 3-file vault, each file producing
# exactly 1 chunk with the chunker settings above.
_EXPECTED_DOC_COUNT = 3
_EXPECTED_CHUNK_COUNT = 3
_EXPECTED_EMBEDDER_COUNT = 1

_FAKE_EMBEDDER_NAME = "smoke-fake-embed"
_FAKE_EMBEDDER_DIM = 8

# ---------------------------------------------------------------------------
# Fake embedder
# ---------------------------------------------------------------------------


class _FakeSmokeEmbedder:
    """Minimal deterministic embedder — no model loading, no network.

    Produces unit-normalised float32 vectors of dimension ``_FAKE_EMBEDDER_DIM``
    whose values are derived from sha256(text).  Non-zero guaranteed.
    """

    name: str = _FAKE_EMBEDDER_NAME
    provider: str = "sentence_transformers"  # keeps ingest.py's registry call plausible
    model_id: str = "fake/smoke-v1"
    dimension: int = _FAKE_EMBEDDER_DIM
    normalized: bool = True
    distance: str = "cosine"

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:_FAKE_EMBEDDER_DIM], dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0
        return vec / np.linalg.norm(vec)

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        if not texts:
            return np.empty((0, _FAKE_EMBEDDER_DIM), dtype=np.float32)
        return np.stack([self._encode_one(t) for t in texts])

    def warmup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def test_smoke_sqlite_ingest(tmp_path: Path) -> None:
    """End-to-end: markdown vault → ingest_once → queryable corpus.db.

    Assertions:
    1. At least one row in datasets, sources, documents, chunks, embedders.
    2. documents.source_uri rows reference the actual vault file paths.
    3. Chunk count == _EXPECTED_CHUNK_COUNT (deterministic chunker config).
    4. Every chunk has a non-NULL content_hash.
    """
    # ── 1. Build a minimal vault ──────────────────────────────────────────────
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "alpha.md").write_text(
        "# Alpha\n\nThis is the alpha document with some content.",
        encoding="utf-8",
    )
    (vault / "beta.md").write_text(
        "# Beta\n\nThis is the beta document with different content.\n\n"
        "## Sub-section\n\nMore text here.",
        encoding="utf-8",
    )
    (vault / "gamma.md").write_text(
        "# Gamma\n\nThis is the gamma document with yet more content.\n\n"
        "## Details\n\nSome detailed text about the gamma topic.",
        encoding="utf-8",
    )

    vault_files = {str(p.resolve()) for p in vault.glob("*.md")}
    assert len(vault_files) == _EXPECTED_DOC_COUNT, (
        f"Expected {_EXPECTED_DOC_COUNT} .md files in vault, found {len(vault_files)}"
    )

    db_path = tmp_path / "corpus.db"

    # ── 2. Build Config ───────────────────────────────────────────────────────
    config = Config(
        backend=BackendConfig(kind="sqlite", dsn=str(db_path)),
        daemon=DaemonConfig(debounce_seconds=1.0, log_level="WARNING", log_format="text"),
        datasets=[
            DatasetConfig(
                name="smoke-vault",
                kind="text",
                description="B-18 smoke test vault",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root=str(vault),
                        exclude_globs=[".obsidian/**", ".trash/**"],
                        chunker="markdown",
                        chunker_config={
                            "max_chars": _CHUNK_MAX_CHARS,
                            "overlap": _CHUNK_OVERLAP,
                        },
                    )
                ],
            )
        ],
        embedders=[
            EmbedderConfig(
                name=_FAKE_EMBEDDER_NAME,
                provider="sentence_transformers",
                model_id="fake/smoke-v1",
                dimension=_FAKE_EMBEDDER_DIM,
                normalize=True,
                distance="cosine",
                active=True,
                batch_size=32,
                device="cpu",
            )
        ],
    )

    # ── 3. Call ingest_once with fake embedder ────────────────────────────────
    fake_embedder = _FakeSmokeEmbedder()

    with patch("corpus_forge.ingest.registry") as mock_registry:
        mock_registry.register.return_value = fake_embedder

        ingest_once(config)

    # ── 4. Open db and assert rows ────────────────────────────────────────────
    assert db_path.exists(), f"corpus.db not created at {db_path}"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 4a. datasets table has at least one row
        datasets = conn.execute("SELECT id, name FROM datasets").fetchall()
        assert len(datasets) >= 1, "Expected at least one row in datasets"

        # 4b. sources table has at least one row
        sources = conn.execute("SELECT id FROM sources").fetchall()
        assert len(sources) >= 1, "Expected at least one row in sources"

        # 4c. documents table has exactly _EXPECTED_DOC_COUNT rows
        documents = conn.execute("SELECT id, source_uri FROM documents").fetchall()
        assert len(documents) == _EXPECTED_DOC_COUNT, (
            f"Expected {_EXPECTED_DOC_COUNT} document rows, got {len(documents)}"
        )

        # 4d. source_uri values reference the actual vault file paths
        db_source_uris = {row["source_uri"] for row in documents}
        # MarkdownVaultSource constructs URIs as "vault://<root_name>/<rel_path>"
        expected_filenames = {"alpha.md", "beta.md", "gamma.md"}
        db_filenames = {uri.rsplit("/", 1)[-1] for uri in db_source_uris}
        assert db_filenames == expected_filenames, (
            f"source_uri filenames mismatch: expected {expected_filenames}, got {db_filenames}"
        )

        # 4e. chunks table has exactly _EXPECTED_CHUNK_COUNT rows
        chunks = conn.execute("SELECT id, content_hash FROM chunks").fetchall()
        assert len(chunks) == _EXPECTED_CHUNK_COUNT, (
            f"Expected {_EXPECTED_CHUNK_COUNT} chunk rows, got {len(chunks)}"
        )

        # 4f. Every chunk has a non-NULL content_hash
        null_hashes = [row["id"] for row in chunks if row["content_hash"] is None]
        assert null_hashes == [], f"Chunk ids with NULL content_hash: {null_hashes}"

        # 4g. embedders table has at least one row
        embedders = conn.execute("SELECT id, name FROM embedders").fetchall()
        assert len(embedders) >= _EXPECTED_EMBEDDER_COUNT, (
            f"Expected at least {_EXPECTED_EMBEDDER_COUNT} embedder row, got {len(embedders)}"
        )

    finally:
        conn.close()
