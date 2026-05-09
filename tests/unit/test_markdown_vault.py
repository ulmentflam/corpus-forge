"""Unit tests for MarkdownVault source plugin."""

from pathlib import Path

from corpus_forge.sources.base import RawDocument
from corpus_forge.sources.markdown_vault import MarkdownVaultSource


class TestMarkdownVaultSource:
    """Tests for MarkdownVaultSource class."""

    def test_discover_returns_md_files(self, sample_vault_dir):
        """Test that discover yields markdown files."""
        source = MarkdownVaultSource(vault_root=sample_vault_dir)
        files = list(source.discover())
        assert len(files) == 2
        file_names = {f.name for f in files}
        assert "note1.md" in file_names
        assert "note2.md" in file_names

    def test_discover_excludes_obsidian(self, sample_vault_dir):
        """Test that .obsidian directory is excluded."""
        source = MarkdownVaultSource(vault_root=sample_vault_dir)
        files = list(source.discover())
        for f in files:
            assert ".obsidian" not in str(f)

    def test_discover_excludes_dotfiles(self, sample_vault_dir):
        """Test that dotfiles are excluded when '.*' is in the exclude_globs."""
        (sample_vault_dir / ".hidden.md").write_text("# Hidden")
        source = MarkdownVaultSource(vault_root=sample_vault_dir, exclude_globs=[".*"])
        files = list(source.discover())
        file_names = {f.name for f in files}
        # .hidden.md starts with '.' and matches the exclude glob '.*'
        assert ".hidden.md" not in file_names

    def test_parse_returns_raw_document(self, sample_vault_dir):
        """Test that parse returns a RawDocument."""
        source = MarkdownVaultSource(vault_root=sample_vault_dir)
        note_path = sample_vault_dir / "note1.md"
        doc = source.parse(note_path)
        assert isinstance(doc, RawDocument)
        assert doc.source_uri.startswith("vault://")
        assert doc.content_hash
        assert doc.text == "# Note 1\n\nThis is the first note."
        assert doc.title == "Note 1"
        assert doc.modified_at > 0

    def test_parse_title_from_filename(self, temp_dir):
        """Test title extraction from filename when no heading."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()
        (vault_dir / "no_heading.md").write_text("Just plain text, no heading.")
        source = MarkdownVaultSource(vault_root=vault_dir)
        doc = source.parse(vault_dir / "no_heading.md")
        assert doc.title == "no_heading"

    def test_parse_title_from_frontmatter(self, temp_dir):
        """Test title extraction from first H1 heading."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()
        (vault_dir / "test.md").write_text("# My Custom Title\n\nContent here.")
        source = MarkdownVaultSource(vault_root=vault_dir)
        doc = source.parse(vault_dir / "test.md")
        assert doc.title == "My Custom Title"

    def test_parse_empty_file(self, temp_dir):
        """Test parsing an empty file."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()
        (vault_dir / "empty.md").write_text("")
        source = MarkdownVaultSource(vault_root=vault_dir)
        doc = source.parse(vault_dir / "empty.md")
        assert doc.text == ""
        assert doc.title == "empty"

    def test_name_and_kind(self):
        """Test source name and dataset kind."""
        source = MarkdownVaultSource(vault_root=Path("/tmp/test"))
        assert source.name == "markdown_vault"
        assert source.dataset_kind == "text"

    def test_exclude_globs_custom(self, temp_dir):
        """Test custom exclude globs."""
        vault_dir = temp_dir / "vault"
        vault_dir.mkdir()
        (vault_dir / "note.md").write_text("# Note")
        (vault_dir / "trash.md").write_text("# Trash")
        source = MarkdownVaultSource(
            vault_root=vault_dir, exclude_globs=[".obsidian/**", ".trash/**"]
        )
        files = list(source.discover())
        file_names = {f.name for f in files}
        assert "note.md" in file_names
        assert "trash.md" in file_names

    def test_file_content_hash(self, sample_vault_dir):
        """Test file_content_hash method."""
        source = MarkdownVaultSource(vault_root=sample_vault_dir)
        note_path = sample_vault_dir / "note1.md"
        hash1 = source.file_content_hash(note_path)
        # Same file should produce same hash
        hash2 = source.file_content_hash(note_path)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest length

    def test_identity(self, sample_vault_dir):
        """Test identity method returns resolved path."""
        source = MarkdownVaultSource(vault_root=sample_vault_dir)
        identity = source.identity()
        assert identity == str(sample_vault_dir.resolve())
