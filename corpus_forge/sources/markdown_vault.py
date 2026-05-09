"""Markdown vault source plugin."""

import fnmatch
from collections.abc import Iterator
from pathlib import Path

from .base import RawDocument, WatchedSource


class MarkdownVaultSource(WatchedSource):
    """Port of current Obsidian logic; subclasses WatchedSource,
    only overrides discover() and parse().
    """

    name = "markdown_vault"
    dataset_kind = "text"

    def __init__(self, vault_root: Path | str, exclude_globs: list[str] | None = None, **kwargs):
        super().__init__(Path(vault_root), **kwargs)
        self.exclude_globs = exclude_globs or [".obsidian/**", ".trash/**"]

    def _is_excluded(self, path: Path) -> bool:
        """Return True if *path* matches any exclude glob pattern.

        Patterns are matched against the relative path from the vault root and
        against each individual path component, so that:
          - ``.*``        excludes hidden files/dirs (any component starting with ``.``)
          - ``.trash/**`` excludes anything under ``.trash``
        """
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            rel = path

        rel_str = str(rel)
        for pattern in self.exclude_globs:
            # Match against the full relative path string (handles dir/** patterns)
            if fnmatch.fnmatch(rel_str, pattern):
                return True
            # Match each individual path component (handles simple patterns like ".*")
            for part in rel.parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def discover(self) -> Iterator[Path]:
        """Yield markdown files, respecting exclude patterns."""
        for path in self.root.rglob("*.md"):
            if self._is_excluded(path):
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
