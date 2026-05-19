"""Phase M Wave 2 — unified filesystem walker.

`scanner.walker.walk` replaces the two divergent slow walkers in
`corpus_forge.estimate._walk` and
`corpus_forge.sources.filesystem.FilesystemSource.discover`. It uses
`os.scandir` (single stat per entry), prunes excluded directories
during descent, and short-circuits files by extension BEFORE statting.

Re-exports:

- :func:`walk`
- :class:`WalkEntry`
- :class:`ScanConfig`  (re-exported from `corpus_forge.config`)
"""

from __future__ import annotations

from corpus_forge.config import ScanConfig

from .walker import WalkEntry, WalkStats, walk

__all__ = ["ScanConfig", "WalkEntry", "WalkStats", "walk"]
