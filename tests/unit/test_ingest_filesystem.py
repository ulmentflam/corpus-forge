"""Unit tests for D-15: `_instantiate_source(plugin="filesystem", ...)`.

Wave 2 of the multi-format milestone. The new branch:

* Reads ``source_config.root`` (new optional ``ExpandedPath`` on
  ``DatasetSourceConfig``).
* Reads ``source_config.extraction`` (already optional in Wave 0 D-06),
  defaulting to ``ExtractionConfig()`` when None.
* Bakes extractor tunables (``csv_max_rows``, ``code_chunker_config``)
  into the registry at construction time via
  ``register_default_extractors(extraction)``.
* Passes ``exclude_globs`` straight through.

Tests use a temporary directory for ``root`` so the constructor's
``Path`` coercion is exercised.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from corpus_forge.config import DatasetSourceConfig, ExtractionConfig, ScanConfig
from corpus_forge.ingest import _instantiate_source
from corpus_forge.sources.filesystem import FilesystemSource


def test_instantiate_filesystem_returns_filesystem_source(tmp_path: Path):
    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
    )
    src = _instantiate_source(cfg)
    assert isinstance(src, FilesystemSource)
    assert src.root == tmp_path


def test_instantiate_filesystem_defaults_extraction_when_none(tmp_path: Path):
    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
        extraction=None,
    )
    src = _instantiate_source(cfg)
    assert isinstance(src.extraction, ExtractionConfig)
    # All flags default True.
    assert src.extraction.enable_code is True


def test_instantiate_filesystem_passes_extraction_through(tmp_path: Path):
    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
        extraction=ExtractionConfig(enable_pdf=False, csv_max_rows=42),
    )
    src = _instantiate_source(cfg)
    assert src.extraction.enable_pdf is False
    assert src.extraction.csv_max_rows == 42


def test_instantiate_filesystem_passes_exclude_globs(tmp_path: Path):
    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
        exclude_globs=["*.bak", ".git/**"],
    )
    src = _instantiate_source(cfg)
    assert "*.bak" in src.exclude_globs
    assert ".git/**" in src.exclude_globs


def test_instantiate_filesystem_threads_scan_config_from_supplied_config(tmp_path: Path):
    """When the enclosing ``config`` is supplied, ``config.scan`` must be
    threaded into ``FilesystemSource`` so ``config.scan.workers`` reaches
    the directory walk (the per-PR-#69 follow-up).

    Without this wiring, the ``filesystem`` source plugin silently fell
    back to ``ScanConfig()`` (workers=1, serial) regardless of what the
    user put in their config — only ``CF_SCAN_WORKERS`` env override
    would engage concurrency.
    """
    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
    )
    # Stub Config: real ScanConfig (so the resolver gets a real int when
    # the source's discover() calls it), minimal MagicMock for the rest.
    enclosing = MagicMock()
    enclosing.scan = ScanConfig(workers=4)
    src = _instantiate_source(cfg, config=enclosing)
    assert isinstance(src, FilesystemSource)
    assert src.scan_config.workers == 4


def test_instantiate_filesystem_defaults_scan_config_when_config_is_none(tmp_path: Path):
    """Legacy call shape (``config=None``) must continue to give the
    source a default ``ScanConfig`` (workers=1, serial) — no regression
    for callers that never pass ``config``."""
    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
    )
    src = _instantiate_source(cfg)  # no config=
    assert isinstance(src, FilesystemSource)
    assert isinstance(src.scan_config, ScanConfig)
    assert src.scan_config.workers == 1  # ScanConfig default


def test_instantiate_filesystem_csv_max_rows_baked_into_extractor(tmp_path: Path):
    """The registry built for this source must have a CsvExtractor with
    ``max_rows`` matching the config — not the class default."""
    from corpus_forge.extractors.csv import CsvExtractor

    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
        extraction=ExtractionConfig(csv_max_rows=42),
    )
    src = _instantiate_source(cfg)
    # The source owns its registry.
    reg = src._registry
    csv_ex = reg.get_for(Path("dummy.csv"))
    assert isinstance(csv_ex, CsvExtractor)
    assert csv_ex.max_rows == 42


def test_instantiate_filesystem_code_chunker_config_baked_into_extractor(tmp_path: Path):
    """The CodeExtractor wired into the registry should carry the
    ``code_chunker_config`` chosen on ``ExtractionConfig``."""
    from corpus_forge.extractors.code import CodeExtractor

    cfg = DatasetSourceConfig(
        plugin="filesystem",
        root=str(tmp_path),
        chunker="markdown",
        extraction=ExtractionConfig(
            code_chunker_config={"max_chars": 800, "min_chars": 50, "overlap": 50}
        ),
    )
    src = _instantiate_source(cfg)
    code_ex = src._registry.get_for(Path("x.py"))
    assert isinstance(code_ex, CodeExtractor)
    # The CodeExtractor constructor must accept code_chunker_config and
    # expose it (so CodeChunker downstream picks it up).
    assert code_ex.code_chunker_config == {"max_chars": 800, "min_chars": 50, "overlap": 50}


def test_instantiate_unknown_plugin_still_raises():
    """The new ``filesystem`` branch must NOT swallow the unknown-plugin
    fallback for arbitrary other plugins."""
    cfg = DatasetSourceConfig(
        plugin="not_a_real_plugin",
        chunker="markdown",
    )
    with pytest.raises(ValueError, match="Unknown source plugin"):
        _instantiate_source(cfg)


def test_instantiate_markdown_vault_still_works(tmp_path: Path):
    """Backwards compatibility: existing plugins keep working."""
    from corpus_forge.sources.markdown_vault import MarkdownVaultSource

    cfg = DatasetSourceConfig(
        plugin="markdown_vault",
        vault_root=str(tmp_path),
        chunker="markdown",
    )
    src = _instantiate_source(cfg)
    assert isinstance(src, MarkdownVaultSource)
