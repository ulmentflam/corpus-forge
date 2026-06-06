"""Integration tests — post-PR #81 bugfix: SQL-side ``extensions=`` push
for ``chunks_missing_embedding`` / ``count_chunks_missing_embedding`` on the
real Postgres backend.

The unit tests in ``tests/unit/test_postgres_backend.py`` pin the SQL shape
(mock-execute level); these integration tests prove the SQL actually runs
against a live Postgres + pgvector schema and returns the right chunk_ids.

Two backend methods + one end-to-end smoke through ``backfill_embedder``.
"""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.embedders.base import BaseEmbedder

pytestmark = pytest.mark.integration


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    """Migrated PostgresBackend against the testcontainers Postgres."""
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    yield b
    b.close()


def _seed_mixed_chunks(backend: PostgresBackend) -> dict[str, int]:
    """Seed 4 chunks across .py / .ts / .md / conversation-source, return the
    chunk_ids keyed by tag.
    """
    dataset_id = backend.get_or_create_dataset(name="routing-ds", kind="text", description="")

    chunk_ids: dict[str, int] = {}
    for tag, uri, text in [
        ("py", "filesystem:///x/a.py", "py code"),
        ("ts", "filesystem:///x/a.ts", "ts code"),
        ("md", "filesystem:///x/a.md", "md text"),
    ]:
        doc = backend._execute(
            "INSERT INTO corpus.documents (dataset_id, source_uri, content_hash, text) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (dataset_id, uri, f"hash_{tag}", text),
        )
        doc_id = doc[0]["id"]
        chunk = backend._execute(
            "INSERT INTO corpus.chunks (document_id, chunk_index, text, metadata, content_hash) "
            "VALUES (%s, 0, %s, %s, %s) RETURNING id",
            (doc_id, text, _json.dumps({}), f"ch_{tag}"),
        )
        chunk_ids[tag] = chunk[0]["id"]

    # Conversation-sourced chunk (no document parent — chunks XOR parents).
    conv = backend._execute(
        "INSERT INTO corpus.conversations"
        " (dataset_id, source_uri, title, content_hash, metadata) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (dataset_id, "claude-code://session-1", "t", "conv_hash_1", _json.dumps({})),
    )
    conv_id = conv[0]["id"]
    msg = backend._execute(
        "INSERT INTO corpus.messages"
        " (conversation_id, turn_index, role, content, metadata) "
        "VALUES (%s, 0, 'user', %s, %s) RETURNING id",
        (conv_id, "chat text", _json.dumps({})),
    )
    msg_id = msg[0]["id"]
    chat_chunk = backend._execute(
        "INSERT INTO corpus.chunks "
        "(conversation_id, message_id, chunk_index, text, metadata, content_hash) "
        "VALUES (%s, %s, 0, %s, %s, %s) RETURNING id",
        (conv_id, msg_id, "chat text", _json.dumps({}), "ch_chat"),
    )
    chunk_ids["chat"] = chat_chunk[0]["id"]

    return chunk_ids


# ── chunks_missing_embedding(extensions=...) ─────────────────────────────────


class TestChunksMissingEmbeddingExtensionsLive:
    def test_extensions_filter_excludes_md_and_chat(self, backend: PostgresBackend) -> None:
        chunk_ids = _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rf-nomic-code", provider="t", model_id="m", dimension=4)
        )

        result = list(backend.chunks_missing_embedding(emb_id, extensions=[".py", ".ts"]))
        returned = {r[0] for r in result}
        assert chunk_ids["py"] in returned
        assert chunk_ids["ts"] in returned
        assert chunk_ids["md"] not in returned, (
            f".md must be filtered out by [.py, .ts]; got returned={returned}, "
            f"md chunk_id={chunk_ids['md']}"
        )
        assert chunk_ids["chat"] not in returned, (
            f"conversation-sourced chunk (no file extension) must be filtered out; "
            f"got returned={returned}, chat chunk_id={chunk_ids['chat']}"
        )

    def test_extensions_none_returns_all_four(self, backend: PostgresBackend) -> None:
        chunk_ids = _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rf-nomic-all", provider="t", model_id="m", dimension=4)
        )
        result = list(backend.chunks_missing_embedding(emb_id, extensions=None))
        returned = {r[0] for r in result}
        assert returned == set(chunk_ids.values()), (
            f"extensions=None must return all 4 seeded chunks; got {returned} "
            f"expected {set(chunk_ids.values())}"
        )

    def test_extensions_case_normalised(self, backend: PostgresBackend) -> None:
        """``[".PY"]`` (uppercase, leading dot present) matches the
        ``filesystem:///x/a.py`` URI thanks to lower() normalisation."""
        chunk_ids = _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rf-nomic-case", provider="t", model_id="m", dimension=4)
        )
        result = list(backend.chunks_missing_embedding(emb_id, extensions=[".PY"]))
        returned = {r[0] for r in result}
        assert returned == {chunk_ids["py"]}, (
            f"[.PY] must normalise and match the .py chunk only; got {returned}"
        )


# ── count_chunks_missing_embedding(extensions=...) ───────────────────────────


class TestCountChunksMissingEmbeddingExtensionsLive:
    def test_count_with_extensions_returns_filtered_total(self, backend: PostgresBackend) -> None:
        _seed_mixed_chunks(backend)
        emb_id = backend.register_embedder(
            BaseEmbedder(name="rf-count-code", provider="t", model_id="m", dimension=4)
        )

        n_py = backend.count_chunks_missing_embedding(emb_id, extensions=[".py"])
        n_code = backend.count_chunks_missing_embedding(emb_id, extensions=[".py", ".ts"])
        n_all = backend.count_chunks_missing_embedding(emb_id, extensions=None)

        assert n_py == 1, f"expected 1 .py chunk; got {n_py}"
        assert n_code == 2, f"expected 2 code chunks; got {n_code}"
        assert n_all == 4, f"expected 4 total chunks (incl. chat); got {n_all}"


# ── End-to-end backfill smoke ────────────────────────────────────────────────


class TestBackfillEndToEndOnlyEmbedsClaimedChunks:
    """Drive ``backfill_embedder`` against a real Postgres backend with the
    test fixture chunks. The specialist (.py/.ts) must only embed those 2
    chunks; the in-memory ``route_for`` filter must drop zero rows
    (the SQL filter already did the work)."""

    def test_specialist_backfill_writes_only_py_and_ts(self, backend: PostgresBackend) -> None:
        from corpus_forge.config import Config
        from corpus_forge.embed import backfill_embedder

        chunk_ids = _seed_mixed_chunks(backend)

        # Register both embedders in the DB so register_embedder doesn't
        # need to roundtrip to a fake model.
        emb_text_id = backend.register_embedder(
            BaseEmbedder(name="rf-e2e-nomic", provider="t", model_id="m", dimension=4)
        )
        emb_code_id = backend.register_embedder(
            BaseEmbedder(name="rf-e2e-nomic-code", provider="t", model_id="m", dimension=4)
        )
        assert emb_text_id != emb_code_id  # silence unused-var

        # Build the EmbedderConfig stand-ins that backfill_embedder will
        # iterate through Config.embedders.
        text_cfg = MagicMock()
        text_cfg.name = "rf-e2e-nomic"
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
        code_cfg.name = "rf-e2e-nomic-code"
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
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]
        # RFC fleet-2 claim path: host_id() and [embed] reach SQL params —
        # a bare MagicMock can't be adapted by psycopg ("cannot adapt type
        # 'MagicMock'"). Concrete values; the silent-heartbeat → first-claim
        # FK-violation → fallback demotion is an exercised, supported path.
        mock_config.host_id.return_value = "rf-e2e-host"
        mock_config.embed.claim_lease_ttl = 600
        mock_config.embed.lanes = []

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
                self._encode_calls: list[list[str]] = []

            def encode(self, texts, **_kw):
                self._encode_calls.append(list(texts))
                # ``PostgresBackend.write_embeddings`` calls ``.tolist()`` on
                # each row, so return real numpy arrays rather than plain lists.
                return [np.array(self._vec, dtype=np.float32) for _ in texts]

            def warmup(self) -> None:
                pass

        text_runtime = _Runtime("rf-e2e-nomic", [0.1, 0.2, 0.3, 0.4], [])
        code_runtime = _Runtime("rf-e2e-nomic-code", [0.5, 0.6, 0.7, 0.8], [".py", ".ts"])

        def _registry_dispatch(_reg, ecfg):
            if ecfg.name == "rf-e2e-nomic":
                return text_runtime
            if ecfg.name == "rf-e2e-nomic-code":
                return code_runtime
            raise AssertionError(f"unexpected embedder name {ecfg.name!r}")

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_registry_dispatch,
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("rf-e2e-nomic-code")

        # Assert ONLY .py + .ts were embedded in the code embedder's table.
        # PR #81 names the table by the registered embedder name with
        # '-' → '_' for SQL safety.
        written = backend._execute(
            "SELECT chunk_id FROM corpus.embeddings_rf_e2e_nomic_code ORDER BY chunk_id"
        )
        written_ids = {r["chunk_id"] for r in written}
        assert written_ids == {chunk_ids["py"], chunk_ids["ts"]}, (
            f"specialist must embed exactly the .py + .ts chunks; got {written_ids}, "
            f"expected {{{chunk_ids['py']}, {chunk_ids['ts']}}} "
            f"(md={chunk_ids['md']}, chat={chunk_ids['chat']} must be excluded)"
        )

        # The in-memory route_for filter must have been a no-op: every
        # fetched row already matched the specialist (SQL did its job).
        # We can prove this by comparing what the runtime encoder saw vs.
        # what was written — they must be the same size.
        total_encoded = sum(len(batch) for batch in code_runtime._encode_calls)
        assert total_encoded == len(written_ids), (
            f"in-memory route_for dropped {total_encoded - len(written_ids)} rows the "
            f"SQL filter approved — defeats the SQL-push optimisation. "
            f"encoded={total_encoded}, written={len(written_ids)}"
        )

        # And the catchall table was untouched (we didn't call backfill_embedder
        # for nomic, so nothing should be there).
        catchall_rows = backend._execute("SELECT chunk_id FROM corpus.embeddings_rf_e2e_nomic")
        assert catchall_rows == [], (
            f"catchall table should be empty (we only ran nomic-code's backfill); "
            f"got {catchall_rows}"
        )

        # Sanity: numpy import is here only because the embedder stubs are
        # tolerant of plain lists, but ``backend.write_embeddings`` may
        # convert internally; the assertion above doesn't read the vectors.
        assert isinstance(np.array([0.0], dtype=np.float32)[0], np.float32)
