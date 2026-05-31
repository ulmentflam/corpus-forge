"""Unit tests for ingest module — extended coverage."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.chunkers.base import Chunker, TextChunk
from corpus_forge.config import Config
from corpus_forge.ingest import (
    _get_or_create_dataset,
    _instantiate_source,
    _process_conversation,
    _process_document,
    _write_embeddings_for_chunks,
    get_active_embedders,
    ingest_once,
    main,
)
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage


class TestProcessDocument:
    """Tests for _process_document."""

    def test_process_document_returns_chunks(self):
        """Test that _process_document returns TextChunk instances (Phase D HK-1)."""
        doc = RawDocument(
            source_uri="vault://test.md",
            content_hash="abc",
            text="# Header\n\nPara one.\n\nPara two.",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = Chunker(max_chars=50, overlap=10)
        result = _process_document(doc, chunker)
        assert len(result) >= 1
        for chunk in result:
            assert isinstance(chunk, TextChunk)
            assert isinstance(chunk.text, str)
            assert len(chunk.text) > 0

    def test_process_document_empty_text(self):
        """Test that _process_document handles empty text."""
        doc = RawDocument(
            source_uri="vault://empty.md",
            content_hash="empty",
            text="",
            title="Empty",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = Chunker(max_chars=50, overlap=10)
        result = _process_document(doc, chunker)
        assert result == []


class TestProcessConversation:
    """Tests for _process_conversation."""

    def test_process_conversation_with_chunker(self):
        """Test _process_conversation with a valid chunker."""
        conv = RawConversation(
            source_uri="claude-code://proj/sess1",
            external_id="sess1",
            content_hash="conv123",
            title="Chat",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[
                RawMessage(
                    external_uuid="m1",
                    parent_uuid=None,
                    role="user",
                    content="Hello",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                ),
                RawMessage(
                    external_uuid="m2",
                    parent_uuid="m1",
                    role="assistant",
                    content="Hi!",
                    tool_calls=None,
                    tool_results=None,
                    ts=1001.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        chunker = MagicMock(spec=Chunker)
        chunker.chunk.return_value = [TextChunk(text="Hello"), TextChunk(text="Hi!")]

        result = _process_conversation(conv, chunker)
        assert len(result) == 2

    def test_process_conversation_fallback(self):
        """Test _process_conversation falls back to per-message chunking."""
        conv = RawConversation(
            source_uri="claude-code://proj/sess1",
            external_id="sess1",
            content_hash="conv123",
            title="Chat",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[
                RawMessage(
                    external_uuid="m1",
                    parent_uuid=None,
                    role="user",
                    content="Hello",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                ),
                RawMessage(
                    external_uuid="m2",
                    parent_uuid="m1",
                    role="assistant",
                    content="",
                    tool_calls=None,
                    tool_results=None,
                    ts=1001.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        # chunker without chunk method triggers fallback
        chunker = MagicMock(spec=[])

        result = _process_conversation(conv, chunker)
        assert len(result) == 2
        # First message should have chunks, second should be empty
        assert len(result[0]) >= 1
        assert result[1] == []

    def test_process_conversation_empty_messages(self):
        """Test _process_conversation with empty message list."""
        conv = RawConversation(
            source_uri="claude-code://proj/sess1",
            external_id="sess1",
            content_hash="conv123",
            title="Chat",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[],
            metadata={},
            labels=[],
        )
        chunker = MagicMock(spec=Chunker)

        result = _process_conversation(conv, chunker)
        assert result == []


class TestWriteEmbeddings:
    """Tests for _write_embeddings_for_chunks."""

    def test_write_embeddings_no_chunks_needed(self):
        """Test that no embeddings are written when chunks are up-to-date."""
        backend = MagicMock()
        backend.chunks_missing_embedding.return_value = []

        embedder = MagicMock()
        embedder.name = "test-embed"

        _write_embeddings_for_chunks(backend, 1, embedder)
        embedder.encode.assert_not_called()

    def test_write_embeddings_with_chunks(self):
        """Test that embeddings are written when chunks need them."""
        backend = MagicMock()
        # PR #81 — 3-tuple shape: (chunk_id, text, source_uri).
        backend.chunks_missing_embedding.return_value = [(1, "text1", ""), (2, "text2", "")]

        embedder = MagicMock()
        embedder.name = "test-embed"
        embedder.extensions = []  # catchall
        embedder.encode.return_value = [[0.1] * 384, [0.2] * 384]

        _write_embeddings_for_chunks(backend, 1, embedder)
        embedder.encode.assert_called_once()
        backend.write_embeddings.assert_called_once()


class TestGetActiveEmbedders:
    """Tests for get_active_embedders."""

    def test_get_active_embedders_filters_inactive(self):
        """Test that inactive embedders are filtered out."""
        config = MagicMock()
        config.embedders = [
            MagicMock(active=True, name="active1"),
            MagicMock(active=False, name="inactive1"),
            MagicMock(active=True, name="active2"),
        ]

        with patch("corpus_forge.ingest.registry") as mock_registry:
            m1 = MagicMock()
            m1.name = "active1"
            m2 = MagicMock()
            m2.name = "active2"
            mock_registry.register = MagicMock(side_effect=[m1, m2])
            result = get_active_embedders(config)
            assert len(result) == 2
            names = {e.name for e in result}
            assert "active1" in names
            assert "active2" in names
            assert "inactive1" not in names

    def test_get_active_embedders_all_inactive(self):
        """Test get_active_embedders with all inactive embedders."""
        config = MagicMock()
        config.embedders = [
            MagicMock(active=False, name="inactive1"),
            MagicMock(active=False, name="inactive2"),
        ]

        with patch("corpus_forge.ingest.registry") as mock_registry:
            mock_registry.register = MagicMock()
            result = get_active_embedders(config)
            assert result == []

    def test_get_active_embedders_does_not_pass_device_to_openai_provider(self):
        """Regression: ``OpenAIEmbedder.__init__()`` does not accept a
        ``device`` kwarg — its underlying HTTP transport has no
        local-accelerator concept. Earlier revisions of
        ``get_active_embedders`` unconditionally added ``device`` to
        every embedder's kwargs, blowing up first-run ingest against
        an Ollama-backed OpenAI-compatible endpoint with:

            TypeError: OpenAIEmbedder.__init__() got an unexpected
            keyword argument 'device'
        """
        config = MagicMock()
        e_cfg = MagicMock()
        e_cfg.active = True
        e_cfg.provider = "openai"
        e_cfg.name = "qwen3-2048"
        e_cfg.model_id = "qwen3-embedding:8b"
        e_cfg.dimension = 2048
        e_cfg.normalize = True
        e_cfg.distance = "cosine"
        e_cfg.api_key_env = "OPENAI_API_KEY"
        e_cfg.base_url = "http://localhost:11434/v1"
        config.embedders = [e_cfg]

        with patch("corpus_forge.ingest.registry") as mock_registry:
            mock_registry.register = MagicMock(return_value=MagicMock())
            get_active_embedders(config)

        call_kwargs = mock_registry.register.call_args.kwargs
        assert "device" not in call_kwargs, (
            f"openai-provider embedder must NOT receive `device=` "
            f"(OpenAIEmbedder rejects it). Got kwargs: {sorted(call_kwargs)}"
        )
        # api_key_env + base_url ARE expected on the openai path.
        assert call_kwargs.get("api_key_env") == "OPENAI_API_KEY"
        assert call_kwargs.get("base_url") == "http://localhost:11434/v1"

    def test_get_active_embedders_passes_device_to_sentence_transformers(self):
        """SentenceTransformersEmbedder DOES accept ``device``; the
        per-provider gate must let it through, not strip it
        universally."""
        config = MagicMock()
        e_cfg = MagicMock()
        e_cfg.active = True
        e_cfg.provider = "sentence_transformers"
        e_cfg.name = "st-base"
        e_cfg.model_id = "BAAI/bge-base-en-v1.5"
        e_cfg.dimension = 768
        e_cfg.normalize = True
        e_cfg.distance = "cosine"
        e_cfg.device = "auto"
        config.embedders = [e_cfg]

        with patch("corpus_forge.ingest.registry") as mock_registry:
            mock_registry.register = MagicMock(return_value=MagicMock())
            get_active_embedders(config)

        call_kwargs = mock_registry.register.call_args.kwargs
        assert call_kwargs.get("device") == "auto", (
            f"sentence_transformers provider must receive device=; got {call_kwargs}"
        )

    def test_get_active_embedders_does_not_pass_device_to_model2vec(self):
        """model2vec is CPU-only static embeddings; no device concept."""
        config = MagicMock()
        e_cfg = MagicMock()
        e_cfg.active = True
        e_cfg.provider = "model2vec"
        e_cfg.name = "potion"
        e_cfg.model_id = "minishlab/potion-code-16M"
        e_cfg.dimension = 256
        e_cfg.normalize = True
        e_cfg.distance = "cosine"
        config.embedders = [e_cfg]

        with patch("corpus_forge.ingest.registry") as mock_registry:
            mock_registry.register = MagicMock(return_value=MagicMock())
            get_active_embedders(config)

        call_kwargs = mock_registry.register.call_args.kwargs
        assert "device" not in call_kwargs, (
            f"model2vec provider must NOT receive `device=`; got {sorted(call_kwargs)}"
        )


class TestGetOrCreateDataset:
    """Tests for _get_or_create_dataset."""

    def test_get_existing_dataset(self):
        """Test getting an existing dataset."""
        backend = MagicMock()
        backend.get_or_create_dataset.return_value = 42

        dataset_config = MagicMock()
        dataset_config.name = "existing-dataset"
        dataset_config.kind = "text"
        dataset_config.description = ""

        result = _get_or_create_dataset(backend, dataset_config)
        assert result == 42
        backend.get_or_create_dataset.assert_called_once()

    def test_create_new_dataset(self):
        """Test creating a new dataset."""
        backend = MagicMock()
        backend.get_or_create_dataset.return_value = 99

        dataset_config = MagicMock()
        dataset_config.name = "new-dataset"
        dataset_config.kind = "text"
        dataset_config.description = "A new dataset"

        result = _get_or_create_dataset(backend, dataset_config)
        assert result == 99
        backend.get_or_create_dataset.assert_called_once_with(
            name="new-dataset", kind="text", description="A new dataset"
        )


class TestInstantiateSource:
    """Tests for _instantiate_source."""

    def test_instantiate_markdown_vault(self, temp_dir):
        """Test instantiating a markdown vault source."""
        source_config = MagicMock()
        source_config.plugin = "markdown_vault"
        source_config.vault_root = str(temp_dir)
        source_config.exclude_globs = []

        source = _instantiate_source(source_config)
        assert source.name == "markdown_vault"

    def test_instantiate_claude_code(self, temp_dir):
        """Test instantiating a Claude Code source."""
        source_config = MagicMock()
        source_config.plugin = "claude_code"
        source_config.projects_root = str(temp_dir)
        source_config.include_subagents = True

        source = _instantiate_source(source_config)
        assert source.name == "claude_code"

    def test_instantiate_opencode(self, temp_dir):
        """Test instantiating an OpenCode source."""
        source_config = MagicMock()
        source_config.plugin = "opencode"
        source_config.storage_root = str(temp_dir)

        source = _instantiate_source(source_config)
        assert source.name == "opencode"

    def test_instantiate_unknown_raises(self):
        """Test that unknown source plugin raises ValueError."""
        source_config = MagicMock()
        source_config.plugin = "unknown-plugin"

        with pytest.raises(ValueError, match="Unknown source plugin"):
            _instantiate_source(source_config)


class TestIngestOnceErrorHandling:
    """Tests for ingest_once error handling."""

    def test_ingest_once_unsupported_backend(self, temp_dir):
        """Test ingest_once with unsupported backend kind."""
        config_content = """
[backend]
kind = "duckdb"
dsn = "duckdb://memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

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
            mock_config.daemon.debounce_seconds = 2.0
            mock_config.daemon.log_level = "INFO"
            mock_config.daemon.log_format = "text"
            mock_config.datasets = []
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with pytest.raises(ValueError, match="Unsupported backend kind"):
                ingest_once(mock_config)

    def test_ingest_once_error_in_source_continues(self, temp_dir):
        """Test that errors in one source don't stop the entire ingest."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

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
            mock_config.daemon.debounce_seconds = 2.0
            mock_config.daemon.log_level = "INFO"
            mock_config.daemon.log_format = "text"

            mock_dataset = MagicMock()
            mock_dataset.name = "test"
            mock_dataset.kind = "text"
            mock_config.datasets = [mock_dataset]

            mock_source_config = MagicMock()
            mock_source_config.plugin = "markdown_vault"
            mock_source_config.vault_root = temp_dir
            mock_source_config.exclude_globs = []
            mock_source_config.chunker = "markdown"
            mock_source_config.chunker_config = {"max_chars": 1500, "overlap": 200}
            mock_dataset.sources = [mock_source_config]

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

            with patch("corpus_forge.ingest.registry") as mock_registry:
                mock_registry.register = MagicMock(return_value=mock_embedder)
                with patch("corpus_forge.backends.postgres.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend._execute.side_effect = [
                        [],  # dataset not found, create
                        [{"id": 1}],  # RETURNING id
                    ]
                    mock_backend_cls.return_value = mock_backend

                    # Should not raise
                    ingest_once(mock_config)


class TestMainFunction:
    """Tests for ingest.py main function."""

    def test_main_with_once_true(self, temp_dir):
        """Test main with once=True calls ingest_once."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

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
            mock_config.daemon.log_level = "INFO"
            mock_load.return_value = mock_config

            with patch("corpus_forge.ingest.ingest_once") as mock_ingest:
                main(once=True)
                mock_ingest.assert_called_once_with(
                    mock_config, resume=False, wait=False, max_scan_age=None
                )

    def test_main_with_max_scan_age_zero_preserves_zero(self, temp_dir):
        """Explicit max_scan_age=0.0 must be forwarded as 0.0, NOT converted to None.

        Regression pin for finding #9: the old truthy check ``max_scan_age if
        max_scan_age else None`` silently converted 0.0 to None, making it
        impossible to force "always rescan" via the API.  The new signature
        uses ``float | None = None`` and passes through directly.
        """
        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.daemon.log_level = "INFO"
            mock_load.return_value = mock_config

            with patch("corpus_forge.ingest.ingest_once") as mock_ingest:
                main(once=True, max_scan_age=0.0)
                mock_ingest.assert_called_once_with(
                    mock_config, resume=False, wait=False, max_scan_age=0.0
                )

    def test_main_with_once_false_logs_daemon_message(self, temp_dir):
        """Test main with once=False logs daemon message."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

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
            mock_config.daemon.log_level = "INFO"
            mock_load.return_value = mock_config

            # Phase L Wave 1: ``ingest.main`` no longer calls
            # ``logging.basicConfig``; it routes through the central
            # ``corpus_forge.logging_config.init_logging`` helper instead.
            # The symbol is imported at the top of ``ingest.py`` so the
            # patch must target the ``corpus_forge.ingest`` namespace.
            with patch("corpus_forge.ingest.init_logging") as mock_init:
                main(once=False)
                mock_init.assert_called_once()
                args, _ = mock_init.call_args
                # First positional is the component name.
                assert args and args[0] == "ingest"
