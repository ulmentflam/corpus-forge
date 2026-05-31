"""Integration tests — post-PR #81 bugfix: SQL-side ``extensions=`` push
for ``chunks_missing_embedding`` / ``count_chunks_missing_embedding`` on the
real SQLite backend.

Mirrors ``test_postgres_backend_routing_filter.py``. The SQLite backend
isn't a separate service — these are still considered integration tests
because they exercise the full migrate() schema + insert + query paths.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.embedders.base import BaseEmbedder

pytestmark = pytest.mark.integration


# ── fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
def backend(tmp_path: Path) -> SQLiteBackend:
    b = SQLiteBackend(path=tmp_path / "corpus.db")
    b.migrate()
    return b


def _seed_mixed_chunks(backend: SQLiteBackend) -> dict[str, int]:
    """Seed 4 chunks across .py / .ts / .md / conversation-sourced."""
    # Minimal dataset row — SQLite schema only requires name + kind.
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (1, ?, 'text')",
        ("routing-ds",),
    )
    chunk_ids: dict[str, int] = {}
    for tag, uri, text in [
        ("py", "filesystem:///x/a.py", "py code"),
        ("ts", "filesystem:///x/a.ts", "ts code"),
        ("md", "filesystem:///x/a.md", "md text"),
    ]:
        doc = backend._execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, text) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            (1, uri, f"hash_{tag}", text),
        )
        doc_id = doc[0]["id"]
        chunk = backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata, content_hash) "
            "VALUES (?, 0, ?, ?, ?) RETURNING id",
            (doc_id, text, _json.dumps({}), f"ch_{tag}"),
        )
        chunk_ids[tag] = chunk[0]["id"]

    # Conversation-sourced chunk.
    conv = backend._execute(
        "INSERT INTO conversations"
        " (dataset_id, source_uri, title, content_hash, metadata) "
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        (1, "claude-code://session-1", "t", "conv_hash_1", _json.dumps({})),
    )
    conv_id = conv[0]["id"]
    msg = backend._execute(
        "INSERT INTO messages"
        " (conversation_id, turn_index, role, content, metadata) "
        "VALUES (?, 0, 'user', ?, ?) RETURNING id",
        (conv_id, "chat text", _json.dumps({})),
    )
    msg_id = msg[0]["id"]
    chat_chunk = backend._execute(
        "INSERT INTO chunks "
        "(conversation_id, message_id, chunk_index, text, metadata, content_hash) "
        "VALUES (?, ?, 0, ?, ?, ?) RETURNING id",
        (conv_id, msg_id, "chat text", _json.dumps({}), "ch_chat"),
    )
    chunk_ids["chat"] = chat_chunk[0]["id"]

    return chunk_ids


class TestChunksMissingEmbeddingExtensionsLiveSqlite:
    def test_extensions_filter_excludes_md_and_chat(self, backend: SQLiteBackend) -> None:
        chunk_ids = _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rfs_code", provider="t", model_id="m", dimension=4)
        )

        result = list(backend.chunks_missing_embedding(emb_id, extensions=[".py", ".ts"]))
        returned = {r[0] for r in result}
        assert chunk_ids["py"] in returned
        assert chunk_ids["ts"] in returned
        assert chunk_ids["md"] not in returned, (
            f".md must be filtered out; got {returned}, md={chunk_ids['md']}"
        )
        assert chunk_ids["chat"] not in returned, (
            f"chat chunk must be filtered out; got {returned}, chat={chunk_ids['chat']}"
        )

    def test_extensions_none_returns_all_four(self, backend: SQLiteBackend) -> None:
        chunk_ids = _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rfs_all", provider="t", model_id="m", dimension=4)
        )
        result = list(backend.chunks_missing_embedding(emb_id, extensions=None))
        returned = {r[0] for r in result}
        assert returned == set(chunk_ids.values()), (
            f"extensions=None must return all 4; got {returned}"
        )

    def test_extensions_case_normalised(self, backend: SQLiteBackend) -> None:
        chunk_ids = _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rfs_case", provider="t", model_id="m", dimension=4)
        )
        result = list(backend.chunks_missing_embedding(emb_id, extensions=[".PY"]))
        returned = {r[0] for r in result}
        assert returned == {chunk_ids["py"]}, (
            f"[.PY] must normalise and match .py only; got {returned}"
        )


class TestCountChunksMissingEmbeddingExtensionsLiveSqlite:
    def test_count_with_extensions(self, backend: SQLiteBackend) -> None:
        _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rfs_count", provider="t", model_id="m", dimension=4)
        )
        assert backend.count_chunks_missing_embedding(emb_id, extensions=[".py"]) == 1
        assert backend.count_chunks_missing_embedding(emb_id, extensions=[".py", ".ts"]) == 2
        assert backend.count_chunks_missing_embedding(emb_id, extensions=None) == 4


class TestBackfillEndToEndSqlite:
    """Same as the Postgres E2E smoke but pointed at SQLite. The backfill
    writes via ``backend.write_embeddings`` which on SQLite goes to a
    BLOB / sqlite-vec table — either is fine; we only assert chunk_ids.
    """

    def test_specialist_backfill_writes_only_py_and_ts(self, backend: SQLiteBackend) -> None:
        from corpus_forge.config import Config
        from corpus_forge.embed import backfill_embedder

        chunk_ids = _seed_mixed_chunks(backend)

        # Register both embedders in the DB.
        backend.register_embedder(
            BaseEmbedder(name="rfs_e2e_text", provider="t", model_id="m", dimension=4)
        )
        backend.register_embedder(
            BaseEmbedder(name="rfs_e2e_code", provider="t", model_id="m", dimension=4)
        )

        # Build the EmbedderConfig stand-ins.
        text_cfg = MagicMock()
        text_cfg.name = "rfs_e2e_text"
        text_cfg.provider = "t"
        text_cfg.model_id = "m"
        text_cfg.dimension = 4
        text_cfg.normalize = True
        text_cfg.distance = "cosine"
        text_cfg.active = True
        text_cfg.batch_size = 32
        text_cfg.device = "auto"
        text_cfg.api_key_env = "OPENAI_API_KEY"
        text_cfg.extensions = []

        code_cfg = MagicMock()
        code_cfg.name = "rfs_e2e_code"
        code_cfg.provider = "t"
        code_cfg.model_id = "m"
        code_cfg.dimension = 4
        code_cfg.normalize = True
        code_cfg.distance = "cosine"
        code_cfg.active = True
        code_cfg.batch_size = 32
        code_cfg.device = "auto"
        code_cfg.api_key_env = "OPENAI_API_KEY"
        code_cfg.extensions = [".py", ".ts"]

        mock_config = MagicMock()
        # Backend kind=sqlite so embed.py picks SQLiteBackend constructor.
        mock_config.backend.kind = "sqlite"
        mock_config.backend.dsn = ":memory:"  # ignored — we patch the ctor
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        # Use concrete attribute carriers — backend.register_embedder reads
        # provider/model_id/dimension/etc. as JSON-serialisable scalars.
        class _Runtime:
            def __init__(self, name: str, vec: list[float], extensions: list[str]) -> None:
                self.name = name
                self.provider = "t"
                self.model_id = "m"
                self.dimension = 4
                self.normalized = True
                self.distance = "cosine"
                self.active = True
                self.extensions = extensions
                self.last_failed_indices: list[int] = []
                self._vec = vec

            def encode(self, texts, **_kw):
                return [list(self._vec) for _ in texts]

            def warmup(self) -> None:
                pass

        text_runtime = _Runtime("rfs_e2e_text", [0.1, 0.2, 0.3, 0.4], [])
        code_runtime = _Runtime("rfs_e2e_code", [0.5, 0.6, 0.7, 0.8], [".py", ".ts"])

        def _registry_dispatch(_reg, ecfg):
            return {"rfs_e2e_text": text_runtime, "rfs_e2e_code": code_runtime}[ecfg.name]

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_registry_dispatch,
            ),
            # SQLiteBackend is imported inline in embed.py
            # (``from .backends.sqlite import SQLiteBackend``) — patch the
            # source module instead of the embed module.
            patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        ):
            backfill_embedder("rfs_e2e_code")

        # Read directly from the per-embedder table — chunk_ids must be
        # exactly the .py + .ts ones.
        written = backend._execute("SELECT chunk_id FROM embeddings_rfs_e2e_code ORDER BY chunk_id")
        written_ids = {r["chunk_id"] for r in written}
        assert written_ids == {chunk_ids["py"], chunk_ids["ts"]}, (
            f"specialist must embed only .py + .ts chunks; got {written_ids}, "
            f"expected {{{chunk_ids['py']}, {chunk_ids['ts']}}}"
        )

        # No accidental writes to the catchall table.
        catchall = backend._execute("SELECT chunk_id FROM embeddings_rfs_e2e_text")
        assert catchall == [], (
            f"catchall table must be empty (we only ran the specialist backfill); got {catchall}"
        )
