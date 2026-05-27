"""Unit tests for embed module backfill logic."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.config import Config
from corpus_forge.embed import backfill_embedder


def _make_embedder_config_mock() -> MagicMock:
    """Build a config-side embedder mock matching the shape
    ``backfill_embedder`` expects."""

    cfg = MagicMock()
    cfg.name = "test-embedder"
    cfg.provider = "sentence_transformers"
    cfg.model_id = "test-model"
    cfg.dimension = 384
    cfg.normalize = True
    cfg.distance = "cosine"
    cfg.active = True
    cfg.batch_size = 32
    cfg.device = "auto"
    cfg.api_key_env = "OPENAI_API_KEY"
    return cfg


class TestBackfillEmbedder:
    """Tests for backfill_embedder function."""

    def test_backfill_embedder_not_found(self, temp_dir):
        """Test that missing embedder raises ValueError."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "other-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with pytest.raises(ValueError, match="not found in config"):
                backfill_embedder("nonexistent-embedder")

    def test_backfill_embedder_unsupported_backend(self, temp_dir):
        """Test that an unsupported backend kind raises ValueError."""
        config_content = """
[backend]
kind = "duckdb"
dsn = "duckdb://memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "duckdb"
            mock_config.backend.dsn = "duckdb://memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with pytest.raises(ValueError, match="Unsupported backend kind"):
                backfill_embedder("test-embedder")

    def test_backfill_embedder_no_chunks_needed(self, temp_dir):
        """Test backfill when no chunks need embedding."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_embedder_config = MagicMock()
            mock_embedder_config.name = "test-embedder"
            mock_embedder_config.provider = "sentence_transformers"
            mock_embedder_config.model_id = "test-model"
            mock_embedder_config.dimension = 384
            mock_embedder_config.normalize = True
            mock_embedder_config.distance = "cosine"
            mock_embedder_config.active = True
            mock_embedder_config.batch_size = 32
            mock_embedder_config.device = "auto"
            mock_embedder_config.api_key_env = "OPENAI_API_KEY"
            mock_config.embedders = [mock_embedder_config]
            mock_load.return_value = mock_config

            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):  # noqa: SIM117
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    # Should not raise
                    backfill_embedder("test-embedder")
                    mock_embedder.warmup.assert_called_once()

    def test_backfill_embedder_with_limit(self, temp_dir):
        """Test backfill with limit parameter."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_embedder_config = MagicMock()
            mock_embedder_config.name = "test-embedder"
            mock_embedder_config.provider = "sentence_transformers"
            mock_embedder_config.model_id = "test-model"
            mock_embedder_config.dimension = 384
            mock_embedder_config.normalize = True
            mock_embedder_config.distance = "cosine"
            mock_embedder_config.active = True
            mock_embedder_config.batch_size = 32
            mock_embedder_config.device = "auto"
            mock_embedder_config.api_key_env = "OPENAI_API_KEY"
            mock_config.embedders = [mock_embedder_config]
            mock_load.return_value = mock_config

            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):  # noqa: SIM117
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend.chunks_missing_embedding.return_value = [
                        (1, "text1"),
                        (2, "text2"),
                        (3, "text3"),
                    ]

                    # Make encode return a list of embeddings matching input length
                    def mock_encode(texts):
                        return [[0.1] * 384 for _ in texts]

                    mock_embedder.encode.side_effect = mock_encode
                    mock_backend_cls.return_value = mock_backend

                    # Should process only 2 chunks due to limit
                    backfill_embedder("test-embedder", limit=2)
                    # encode should be called with limited texts (2 items)
                    mock_embedder.encode.assert_called_once()
                    # Verify it was called with exactly 2 texts
                    call_args = mock_embedder.encode.call_args[0][0]
                    assert len(call_args) == 2

    def test_backfill_embedder_dataset_not_found(self, temp_dir):
        """Test that missing dataset raises ValueError."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = []
            mock_load.return_value = mock_config

            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):  # noqa: SIM117
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend._execute.return_value = []  # Dataset not found
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    with pytest.raises(ValueError, match="not found"):
                        backfill_embedder("test-embedder", dataset_name="nonexistent")


class TestBackfillAllSkippedLoopGuard:
    """Regression: when every chunk in a batch is bisected out by the
    embedder (``last_failed_indices`` covers all of them), the
    backfill loop USED to re-fetch the same ``chunks_missing_embedding``
    rows forever — those chunks stay missing, so the next iteration
    sees them again. PR #49 added an explicit ``break`` on
    ``pairs == []`` to short-circuit. This test pins the contract.
    """

    @pytest.mark.timeout(15)
    def test_all_chunks_skipped_breaks_loop_no_hang(self) -> None:
        """If we ever regress, the test hangs and the pytest-timeout
        plugin kills it at 15s — much faster than a real infinite
        loop. Without the guard, ``chunks_missing_embedding`` would
        be called repeatedly with the same input."""

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = [_make_embedder_config_mock()]
            mock_load.return_value = mock_config

            # Embedder mock: encode returns empty array + flags every
            # input as failed. This mirrors the bisecting OpenAI
            # embedder when the model is fully wedged.
            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            def fake_encode(texts):  # type: ignore[no-untyped-def]
                # Bisecting embedder returns (M, dim) where M < len(texts)
                # — in the worst case M = 0.
                mock_embedder.last_failed_indices = list(range(len(texts)))
                # Return zero-row array (numpy-ish — backfill_embedder
                # zips chunk_ids with this and asserts equal lengths;
                # an empty iterable matches an empty filtered chunk_ids).
                return []

            mock_embedder.encode.side_effect = fake_encode
            mock_embedder.last_failed_indices = []

            with (
                patch("corpus_forge.embed.registry.register", return_value=mock_embedder),
                patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls,
                patch(
                    "corpus_forge.embedders.registry.register_from_config",
                    return_value=mock_embedder,
                ),
            ):
                mock_backend = MagicMock()
                mock_backend.register_embedder.return_value = 1
                # ALWAYS return non-empty pending chunks. Without the
                # loop guard, ``backfill_embedder`` would call
                # ``chunks_missing_embedding`` again and again with
                # the same return value, never advancing — the
                # ``@pytest.mark.timeout(15)`` would fire.
                mock_backend.chunks_missing_embedding.return_value = [
                    (1, "text-1"),
                    (2, "text-2"),
                    (3, "text-3"),
                ]
                mock_backend.count_chunks_missing_embedding.return_value = 3
                mock_backend_cls.return_value = mock_backend

                backfill_embedder("test-embedder")

                # encode should be called ONCE — the empty-pairs guard
                # then breaks the loop. Without the guard this would be
                # ∞ (caught by the timeout).
                assert mock_embedder.encode.call_count == 1
                # write_embeddings is the wrong thing to call with an
                # empty list — the guard runs BEFORE write_embeddings.
                mock_backend.write_embeddings.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Batched embed flush — _flush_all_pending_embeddings
# ─────────────────────────────────────────────────────────────────────


class TestFlushAllPendingEmbeddings:
    """The 2026-05-27 ingest profile showed that the per-file
    ``chunks_missing_embedding`` query was the dominant cost (~209ms
    over Tailscale, called after every file). The fix batches the
    embed flush across :data:`_FLUSH_EMBEDDINGS_EVERY_N_FILES` files
    via ``ingest._flush_all_pending_embeddings``, which loops on
    ``_write_embeddings_for_chunks`` until it returns 0.

    These tests pin the drain-until-zero contract — the
    behaviorally-load-bearing invariant that lets the flush handle
    the whole backlog accumulated since the previous flush in one
    invocation.
    """

    def test_flush_loops_until_write_returns_zero(self) -> None:
        """``_flush_all_pending_embeddings`` keeps calling
        ``_write_embeddings_for_chunks`` while it reports work done,
        then breaks on the first zero. With 3 batches of work
        followed by 0, expect exactly 4 calls.
        """

        from corpus_forge.ingest import _flush_all_pending_embeddings

        backend = MagicMock()
        backend.register_embedder.return_value = 7
        embedder = MagicMock(name="emb")

        with patch(
            "corpus_forge.ingest._write_embeddings_for_chunks",
            side_effect=[1024, 1024, 200, 0],
        ) as mock_write:
            _flush_all_pending_embeddings(backend, [embedder])

        assert mock_write.call_count == 4
        # Each call should have used the same embedder_id from
        # register_embedder so the loop sees consistent state.
        for call in mock_write.call_args_list:
            assert call.args[1] == 7
            assert call.args[2] is embedder

    def test_flush_handles_multiple_embedders(self) -> None:
        """One embedder draining 2 batches + another 1 batch + a third
        immediately empty. The flush should issue 4 + 2 + 1 = 7 total
        ``_write_embeddings_for_chunks`` calls (each embedder loops
        until 0)."""

        from corpus_forge.ingest import _flush_all_pending_embeddings

        backend = MagicMock()
        backend.register_embedder.side_effect = [10, 20, 30]
        emb_a = MagicMock(name="a")
        emb_b = MagicMock(name="b")
        emb_c = MagicMock(name="c")

        with patch(
            "corpus_forge.ingest._write_embeddings_for_chunks",
            side_effect=[5, 7, 0, 3, 0, 0],  # a: 5,7,0 ; b: 3,0 ; c: 0
        ) as mock_write:
            _flush_all_pending_embeddings(backend, [emb_a, emb_b, emb_c])

        assert mock_write.call_count == 6  # 3 + 2 + 1
        # Verify each embedder was processed in turn with its registered id.
        seen_emb_ids = [call.args[1] for call in mock_write.call_args_list]
        # First 3 calls: emb_a (id=10); next 2: emb_b (id=20); last: emb_c (id=30).
        assert seen_emb_ids == [10, 10, 10, 20, 20, 30]

    def test_flush_with_no_pending_returns_immediately(self) -> None:
        """When ``_write_embeddings_for_chunks`` reports 0 on the first
        call, the loop exits with no further work — important so the
        end-of-source flush in ``ingest_once`` is cheap when the
        modulo-N boundary already drained the queue."""

        from corpus_forge.ingest import _flush_all_pending_embeddings

        backend = MagicMock()
        backend.register_embedder.return_value = 1
        embedder = MagicMock(name="emb")

        with patch(
            "corpus_forge.ingest._write_embeddings_for_chunks",
            return_value=0,
        ) as mock_write:
            _flush_all_pending_embeddings(backend, [embedder])

        assert mock_write.call_count == 1


class TestWriteEmbeddingsForChunksReturnCount:
    """``_write_embeddings_for_chunks`` returns the number of
    embeddings actually persisted so callers (notably
    ``_flush_all_pending_embeddings``) can loop until done without
    needing a second backend round-trip to ``count_chunks_missing_embedding``.
    """

    def test_returns_zero_when_nothing_pending(self) -> None:
        from corpus_forge.ingest import _write_embeddings_for_chunks

        backend = MagicMock()
        backend.chunks_missing_embedding.return_value = iter([])
        embedder = MagicMock(name="emb")
        result = _write_embeddings_for_chunks(backend, 1, embedder)
        assert result == 0

    def test_returns_pair_count_after_write(self) -> None:
        from corpus_forge.ingest import _write_embeddings_for_chunks

        # 3 chunks come back from chunks_missing_embedding; embedder
        # encodes all 3 without skipping → pair count = 3.
        backend = MagicMock()
        backend.chunks_missing_embedding.return_value = iter([(1, "a"), (2, "b"), (3, "c")])
        embedder = MagicMock(name="emb")
        embedder.encode.return_value = [[0.1] * 4, [0.2] * 4, [0.3] * 4]
        embedder.last_failed_indices = []
        result = _write_embeddings_for_chunks(backend, 1, embedder)
        assert result == 3
        backend.write_embeddings.assert_called_once()

    def test_returns_zero_when_all_chunks_bisected_out(self) -> None:
        """If the bisecting embedder skipped every chunk in the
        batch (``last_failed_indices`` covers all of them), no pairs
        are written and the function returns 0 — which lets the
        outer loop in ``_flush_all_pending_embeddings`` break out
        cleanly instead of spinning."""

        from corpus_forge.ingest import _write_embeddings_for_chunks

        backend = MagicMock()
        backend.chunks_missing_embedding.return_value = iter([(1, "a"), (2, "b")])
        embedder = MagicMock(name="emb")
        # Encode returns nothing usable; embedder flags both as failed.
        embedder.encode.return_value = []
        embedder.last_failed_indices = [0, 1]
        result = _write_embeddings_for_chunks(backend, 1, embedder)
        assert result == 0
