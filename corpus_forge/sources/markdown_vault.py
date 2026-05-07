"""Markdown vault source plugin."""

from collections.abc import Iterator
from pathlib import Path

from .base import RawDocument, WatchedSource


class MarkdownVaultSource(WatchedSource):
    """Port of current Obsidian logic; subclasses WatchedSource,
    only overrides discover() and parse().
    """

    name = "markdown_vault"
    dataset_kind = "text"

    def __init__(self, vault_root: Path, exclude_globs: list[str] | None = None, **kwargs):
        super().__init__(vault_root, **kwargs)
        self.exclude_globs = exclude_globs or [".obsidian/**", ".trash/**", ".*"]

    def discover(self) -> Iterator[Path]:
        """Yield markdown files, respecting exclude patterns."""
        # In a real implementation, we'd use proper globbing with exclude patterns
        # For now, yield all .md files recursively
        for path in self.root.rglob("*.md"):
            # Simple exclude check (would be more robust in practice)
            if any(pattern in str(path) for pattern in self.exclude_globs):
                continue
            yield path

    def parse(self, path: Path) -> RawDocument:
        """Parse a single markdown file into RawDocument."""
        content = path.read_text(encoding="utf-8")
        modified_at = path.stat().st_mtime

        # Extract title from first line or filename
        title = None
        first_line = content.split("\n")[0] if content else ""
        if first_line.startswith("# "):
            title = first_line[2:].strip()
        elif not title:
            title = path.stem

        return RawDocument(
            source_uri=f"vault://{self.root.name}/{path.relative_to(self.root)}",
            content_hash=self.file_content_hash(path),
            text=content,
            title=title,
            modified_at=modified_at,
            metadata={},
            labels=[],  # Would extract from frontmatter in full implementation
        )
