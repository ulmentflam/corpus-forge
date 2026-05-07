"""Unit tests for ingest module helper functions."""

from corpus_forge.ingest import _instantiate_source
from corpus_forge.sources.claude_code import ClaudeCodeSource
from corpus_forge.sources.markdown_vault import MarkdownVaultSource
from corpus_forge.sources.opencode import OpenCodeSource


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

        class MockConfig:
            datasets: list = [type("MockDataset", (), {"sources": [MockSourceConfig()]})()]  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert isinstance(source, MarkdownVaultSource)
        assert source.root == vault_dir

    def test_instantiate_claude_code(self, temp_dir):
        """Test instantiating a ClaudeCodeSource."""
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

        class MockConfig:
            datasets: list = [type("MockDataset", (), {"sources": [MockSourceConfig()]})()]  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert isinstance(source, ClaudeCodeSource)
        assert source.root == projects_dir
        assert source.include_subagents is True

    def test_instantiate_opencode(self, temp_dir):
        """Test instantiating an OpenCodeSource."""
        storage_dir = temp_dir / "storage"
        storage_dir.mkdir()

        class MockSourceConfig:
            plugin = "opencode"
            storage_root = storage_dir
            chunker = "conversation"
            chunker_config: dict = {"mode": "sliding_window"}  # noqa: RUF012

        source = _instantiate_source(MockSourceConfig())
        assert isinstance(source, OpenCodeSource)
        assert source.root == storage_dir
