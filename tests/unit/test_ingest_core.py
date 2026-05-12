"""Unit tests for ingest module core logic."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.chunkers.base import MarkdownChunker
from corpus_forge.ingest import (
    _get_or_create_dataset,
    _instantiate_source,
    _process_conversation,
    _process_document,
    get_active_embedders,
    get_chunker_for_source,
    ingest_one,
)
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage


class TestProcessDocument:
    """Tests for _process_document function."""

    def test_process_document_single_chunk(self):
        """Test processing a document that fits in one chunk."""
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc123",
            text="Short text",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_document(doc, chunker)
        assert len(result) == 1
        assert result[0][1] == "Short text"

    def test_process_document_multiple_chunks(self):
        """Test processing a document that spans multiple chunks."""
        long_text = "A" * 5000
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc123",
            text=long_text,
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1000)
        result = _process_document(doc, chunker)
        assert len(result) > 1
        for _heading, text in result:
            assert len(text) <= chunker.max_chars + chunker.overlap

    def test_process_document_empty(self):
        """Test processing an empty document."""
        doc = RawDocument(
            source_uri="test://empty.md",
            content_hash="abc123",
            text="",
            title=None,
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_document(doc, chunker)
        assert result == []

    def test_process_document_with_heading(self):
        """Test that chunk headings are preserved."""
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc123",
            text="# Title\n\nContent here.",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_document(doc, chunker)
        assert len(result) == 1
        assert "# Title" in result[0][1]


class TestProcessConversation:
    """Tests for _process_conversation function."""

    def test_process_conversation_single_message(self):
        """Test processing a conversation with one message."""
        conv = RawConversation(
            source_uri="test://conv.jsonl",
            external_id="conv1",
            content_hash="abc123",
            title="Test",
            started_at=1000.0,
            ended_at=1001.0,
            messages=[
                RawMessage(
                    external_uuid="msg1",
                    parent_uuid=None,
                    role="user",
                    content="Hello",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                )
            ],
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_conversation(conv, chunker)
        assert len(result) == 1

    def test_process_conversation_multiple_messages(self):
        """Test processing a conversation with multiple messages."""
        conv = RawConversation(
            source_uri="test://conv.jsonl",
            external_id="conv1",
            content_hash="abc123",
            title="Test",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[
                RawMessage(
                    external_uuid="msg1",
                    parent_uuid=None,
                    role="user",
                    content="Hello",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                ),
                RawMessage(
                    external_uuid="msg2",
                    parent_uuid="msg1",
                    role="assistant",
                    content="Hi there!",
                    tool_calls=None,
                    tool_results=None,
                    ts=1001.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_conversation(conv, chunker)
        assert len(result) == 2

    def test_process_conversation_empty_messages(self):
        """Test processing a conversation with empty messages."""
        conv = RawConversation(
            source_uri="test://conv.jsonl",
            external_id="conv1",
            content_hash="abc123",
            title="Test",
            started_at=1000.0,
            ended_at=1001.0,
            messages=[
                RawMessage(
                    external_uuid="msg1",
                    parent_uuid=None,
                    role="user",
                    content="",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_conversation(conv, chunker)
        assert len(result) == 1


class TestGetChunkerForSource:
    """Tests for get_chunker_for_source function."""

    def test_get_markdown_chunker(self, temp_dir):
        """Test getting a markdown chunker."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()

        class MockSourceConfig:
            plugin = "markdown_vault"
            vault_root = vault_dir
            exclude_globs: list[str] = [".obsidian/**", ".trash/**", ".*"]  # noqa: RUF012
            chunker = "markdown"
            chunker_config: dict = {}  # noqa: RUF012

        class MockSource:
            root = vault_dir
            name = "markdown_vault"

        class MockDataset:
            sources = [MockSourceConfig()]  # noqa: RUF012

        class MockConfig:
            datasets = [MockDataset()]  # noqa: RUF012

        source = MockSource()
        config = MockConfig()
        chunker = get_chunker_for_source(source, config)
        assert isinstance(chunker, MarkdownChunker)

    def test_get_conversation_chunker(self, temp_dir):
        """Test getting a conversation chunker."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()

        class MockSourceConfig:
            plugin = "claude_code"
            projects_root = projects_dir
            include_subagents = True
            chunker = "conversation"
            chunker_config: dict = {"mode": "per_message"}  # noqa: RUF012

        class MockSource:
            root = projects_dir
            name = "claude_code"

        class MockDataset:
            sources = [MockSourceConfig()]  # noqa: RUF012

        class MockConfig:
            datasets = [MockDataset()]  # noqa: RUF012

        source = MockSource()
        config = MockConfig()
        chunker = get_chunker_for_source(source, config)
        assert chunker.mode == "per_message"

    def test_get_chunker_unknown_type_raises(self, temp_dir):
        """Test that unknown chunker type raises ValueError."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()

        class MockSourceConfig:
            plugin = "claude_code"
            projects_root = projects_dir
            chunker = "unknown_chunker"
            chunker_config: dict = {}  # noqa: RUF012

        class MockSource:
            root = projects_dir
            name = "claude_code"

        class MockDataset:
            sources = [MockSourceConfig()]  # noqa: RUF012

        class MockConfig:
            datasets = [MockDataset()]  # noqa: RUF012

        source = MockSource()
        config = MockConfig()
        with pytest.raises(ValueError, match="Unknown chunker type"):
            get_chunker_for_source(source, config)

    def test_get_chunker_default_fallback(self):
        """Test that unknown source falls back to MarkdownChunker."""

        class MockSource:
            name = "completely_unknown_source"

        class MockSourceConfig:
            plugin = "markdown_vault"

        class MockDataset:
            sources = [MockSourceConfig()]  # noqa: RUF012

        class MockConfig:
            datasets = [MockDataset()]  # noqa: RUF012

        source = MockSource()
        config = MockConfig()
        chunker = get_chunker_for_source(source, config)
        assert isinstance(chunker, MarkdownChunker)


class TestInstantiateSource:
    """Tests for _instantiate_source function."""

    def test_instantiate_markdown_vault(self, temp_dir):
        """Test instantiating a MarkdownVaultSource."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()

        class MockSourceConfig:
            plugin = "markdown_vault"
            vault_root = vault_dir
            exclude_globs: list[str] = [".obsidian/**"]  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert source.name == "markdown_vault"

    def test_instantiate_claude_code(self, temp_dir):
        """Test instantiating a ClaudeCodeSource."""
        projects_dir = temp_dir / "projects"
        projects_dir.mkdir()

        class MockSourceConfig:
            plugin = "claude_code"
            projects_root = projects_dir
            include_subagents = True

        source = _instantiate_source(MockSourceConfig())
        assert source.name == "claude_code"
        assert source.include_subagents is True

    def test_instantiate_opencode(self, temp_dir):
        """Test instantiating an OpenCodeSource."""
        storage_dir = temp_dir / "storage"
        storage_dir.mkdir()

        class MockSourceConfig:
            plugin = "opencode"
            storage_root = storage_dir

        source = _instantiate_source(MockSourceConfig())
        assert source.name == "opencode"

    def test_instantiate_unknown_raises(self):
        """Test that unknown plugin raises ValueError."""

        class MockSourceConfig:
            plugin = "unknown_plugin"

        with pytest.raises(ValueError, match="Unknown source plugin"):
            _instantiate_source(MockSourceConfig())


class TestGetActiveEmbedders:
    """Tests for get_active_embedders function."""

    def test_get_active_embedders_filters_inactive(self):
        """Test that inactive embedders are filtered out."""
        mock_active = MagicMock()
        mock_active.name = "active-embedder"
        mock_active.provider = "sentence_transformers"
        mock_active.model_id = "test-model"
        mock_active.dimension = 384
        mock_active.normalize = True
        mock_active.distance = "cosine"
        mock_active.active = True
        mock_active.batch_size = 32
        mock_active.device = "auto"
        mock_active.api_key_env = "OPENAI_API_KEY"

        mock_inactive = MagicMock()
        mock_inactive.name = "inactive-embedder"
        mock_inactive.active = False

        class MockConfig:
            embedders = [mock_active, mock_inactive]  # noqa: RUF012

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_embedder = MagicMock()
            mock_embedder.name = "active-embedder"
            mock_register.return_value = mock_embedder
            result = get_active_embedders(MockConfig())
            assert len(result) == 1
            assert result[0].name == "active-embedder"

    def test_get_active_embedders_all_inactive(self):
        """Test with all embedders inactive."""
        mock_inactive = MagicMock()
        mock_inactive.name = "inactive"
        mock_inactive.active = False

        class MockConfig:
            embedders = [mock_inactive]  # noqa: RUF012

        result = get_active_embedders(MockConfig())
        assert result == []


class TestGetOrCreateDataset:
    """Tests for _get_or_create_dataset function."""

    def test_get_existing_dataset(self):
        """Test getting an existing dataset."""
        mock_backend = MagicMock()
        mock_backend.get_or_create_dataset.return_value = 42

        class MockDatasetConfig:
            name = "test-dataset"
            kind = "text"
            description = ""

        result = _get_or_create_dataset(mock_backend, MockDatasetConfig())
        assert result == 42
        mock_backend.get_or_create_dataset.assert_called_once_with(
            name="test-dataset", kind="text", description=""
        )

    def test_create_new_dataset(self):
        """Test creating a new dataset."""
        mock_backend = MagicMock()
        mock_backend.get_or_create_dataset.return_value = 99

        class MockDatasetConfig:
            name = "new-dataset"
            kind = "text"
            description = "A test dataset"

        result = _get_or_create_dataset(mock_backend, MockDatasetConfig())
        assert result == 99
        mock_backend.get_or_create_dataset.assert_called_once_with(
            name="new-dataset", kind="text", description="A test dataset"
        )


class TestIngestOneEmbedderIds:
    """Tests for ingest_one embedder_ids resolution and pass-through (P0-07)."""

    def test_register_embedder_called_for_each_embedder(self):
        """ingest_one calls backend.register_embedder for each active embedder."""
        backend = MagicMock()
        backend.get_hash.return_value = None
        backend.register_embedder.return_value = 42
        backend.chunks_missing_embedding.return_value = []
        embedder_a = MagicMock()
        embedder_b = MagicMock()
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc",
            text="hello",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.heading = "h1"
        mock_chunk.text = "chunk text"
        chunker.chunk.return_value = [mock_chunk]

        ingest_one(backend, doc, chunker, [embedder_a, embedder_b], dataset_id=1)

        assert backend.register_embedder.call_count == 4
        backend.register_embedder.assert_any_call(embedder_a)
        backend.register_embedder.assert_any_call(embedder_b)

    def test_register_embedder_not_called_when_no_embedders(self):
        """ingest_one does not call register_embedder when the embedders list is empty."""
        backend = MagicMock()
        backend.get_hash.return_value = None
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc",
            text="hello",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.heading = "h1"
        mock_chunk.text = "chunk text"
        chunker.chunk.return_value = [mock_chunk]

        ingest_one(backend, doc, chunker, [], dataset_id=1)

        backend.register_embedder.assert_not_called()

    def test_upsert_document_receives_embedder_ids_when_embedders_present(self):
        """upsert_document is called with embedder_ids when embedders are present."""
        backend = MagicMock()
        backend.get_hash.return_value = None
        backend.register_embedder.return_value = 42
        backend.chunks_missing_embedding.return_value = []
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc",
            text="hello",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.heading = "h1"
        mock_chunk.text = "chunk text"
        chunker.chunk.return_value = [mock_chunk]

        ingest_one(backend, doc, chunker, [MagicMock()], dataset_id=1)

        backend.upsert_document.assert_called_once_with(
            1, doc, [("h1", "chunk text")], embedder_ids=[42]
        )

    def test_upsert_document_receives_embedder_ids_none_when_no_embedders(self):
        """upsert_document is called with embedder_ids=None when no embedders."""
        backend = MagicMock()
        backend.get_hash.return_value = None
        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="abc",
            text="hello",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.heading = "h1"
        mock_chunk.text = "chunk text"
        chunker.chunk.return_value = [mock_chunk]

        ingest_one(backend, doc, chunker, [], dataset_id=1)

        backend.upsert_document.assert_called_once_with(
            1, doc, [("h1", "chunk text")], embedder_ids=None
        )
