"""Integration test for the Phase J / J1 sync storage estimator.

Runs the estimator end-to-end against the heterogeneous
``tests/fixtures/multi_format_corpus/`` tree. No Docker / Ollama / model
calls — the estimator is a pure-function consult of the extractor
registry's constants table, so this test is fast (<2 s budget).

Marker: ``pytest.mark.integration`` so it lands in ``make
test-integration`` alongside the other end-to-end suites.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.estimate import estimate_sync

pytestmark = pytest.mark.integration


FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"


def _two_embedder_config() -> Config:
    return Config(
        backend=BackendConfig(kind="sqlite", dsn="sqlite:///:memory:"),
        daemon=DaemonConfig(),
        datasets=[
            DatasetConfig(
                name="d1",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="filesystem",
                        root="/tmp",
                        chunker="markdown",
                    )
                ],
            )
        ],
        embedders=[
            EmbedderConfig(
                name="bge-small-en",
                provider="sentence_transformers",
                model_id="fake/bge",
                dimension=384,
                active=True,
            ),
            EmbedderConfig(
                name="qwen3_8b",
                provider="sentence_transformers",
                model_id="fake/qwen",
                dimension=4096,
                active=True,
            ),
        ],
    )


def test_estimate_against_multi_format_fixture_tree() -> None:
    """End-to-end sanity check: walks the real fixture tree, asserts the
    estimator's per-extractor breakdown covers every class with at least
    one fixture, runs in under 2 seconds, and returns a positive total.
    """
    config = _two_embedder_config()
    assert FIXTURES_ROOT.is_dir(), f"fixture tree missing: {FIXTURES_ROOT}"

    start = time.perf_counter()
    est = estimate_sync(FIXTURES_ROOT, config)
    elapsed = time.perf_counter() - start

    # Time budget — pure-function consult; must be well under 2 s.
    assert elapsed < 2.0, f"estimator took {elapsed:.2f}s on the fixture tree"

    # File count matches a fresh manual walk of the fixture tree that
    # mirrors the estimator's skip policy. We re-import the policy from
    # the estimator module so any future expansion stays in sync.
    from corpus_forge.estimate import _SKIP_DIR_NAMES, _SKIP_FILE_NAMES

    def _walk_filtered(root: Path) -> int:
        count = 0
        stack: list[Path] = [root]
        while stack:
            current = stack.pop()
            for entry in current.iterdir():
                name = entry.name
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if name in _SKIP_DIR_NAMES:
                        continue
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                if name in _SKIP_FILE_NAMES or name.startswith("._"):
                    continue
                count += 1
        return count

    expected_files = _walk_filtered(FIXTURES_ROOT)
    assert est.file_count == expected_files, (
        f"file_count={est.file_count} but filtered walk found {expected_files}"
    )
    # Sanity: we should be counting at least 50 files — the fixture tree
    # has dozens of code samples + several PDFs + offices.
    assert est.file_count >= 50

    # Every extractor class with at least one fixture should appear in
    # the breakdown. The fixture tree contains markdown, pdf, code, csv,
    # structured (json/toml), subtitle (srt), image (png/jpg/webp),
    # notebook (ipynb), epub, office (docx/pptx/xlsx; all classed
    # "markdown" by the estimator's heuristic table), and html (also
    # "markdown" class).
    classes = {b.extractor_class for b in est.by_extractor}
    for required in (
        "markdown",
        "pdf",
        "code",
        "csv",
        "structured",
        "subtitle",
        "image",
        "notebook",
    ):
        assert required in classes, (
            f"expected '{required}' in by_extractor classes, got {sorted(classes)}"
        )

    # Both embedders should be summed (each "active=True").
    assert {e.name for e in est.embeddings} == {"bge-small-en", "qwen3_8b"}
    assert est.embedders_active == ["bge-small-en", "qwen3_8b"]

    # Total bytes is the sum of its parts (pinned by unit test #30; we
    # re-assert here against the real tree).
    embedding_total = sum(e.total_bytes for e in est.embeddings)
    assert est.total_bytes == (
        est.documents_bytes + est.chunks_bytes + embedding_total + est.btree_index_bytes
    )
    assert est.total_bytes > 0
    assert est.total_raw_bytes > 0
    assert est.schema_version == 1


def test_estimate_honors_corpusignore_against_multi_format_fixture(tmp_path: Path) -> None:
    """K1 integration: a real `.corpusignore` prunes the walker.

    Builds a small heterogeneous tree mirroring slices the estimator
    cares about (markdown, heic, a Backups dir, and the hard-coded
    baseline tells `.git/` + `node_modules/` to be skipped regardless),
    drops a `.corpusignore`, and asserts the predicted exclusions hold
    while the negation also lands.
    """
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack, load_local_ignore

    config = _two_embedder_config()

    # Tree shape
    #   tmp_path/
    #     notes.md           ← kept (no pattern)
    #     vacation.heic      ← ignored (`*.heic` pattern)
    #     Backups/big.bin    ← ignored (`Backups/` pattern)
    #     .git/HEAD          ← baseline-skipped (cannot be un-ignored)
    #     node_modules/foo.js  ← baseline-skipped
    (tmp_path / "notes.md").write_text("# notes\nbody")
    (tmp_path / "vacation.heic").write_bytes(b"fake heic bytes" * 100)
    (tmp_path / "Backups").mkdir()
    (tmp_path / "Backups" / "big.bin").write_bytes(b"\x00" * 4096)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "foo.js").write_text("module.exports = {};")

    # `.corpusignore` rules: Backups dir, all .heic; nothing un-ignored.
    (tmp_path / ".corpusignore").write_text(
        "\n".join(["Backups/", "*.heic", "!notes.md", ""]),
        encoding="utf-8",
    )

    # No-ignore baseline first — sentinel for the wiring change.
    baseline = estimate_sync(tmp_path, config)

    # With `.corpusignore`:
    local = load_local_ignore(tmp_path)
    stack = IgnoreStack((CorpusIgnore.empty(tmp_path), local))
    filtered = estimate_sync(tmp_path, config, ignore=stack)

    # file_count drops by at least 1 (`vacation.heic`). Phase M Wave 2:
    # `Backups/big.bin` has an unknown extension and is now short-circuited
    # BEFORE stat — it is absent from BOTH baseline and filtered runs and
    # does not contribute to the diff. `.git/` and `node_modules/` remain
    # baseline-skipped in both. `notes.md` is in neither set (the
    # `!notes.md` negation is a no-op because nothing was ignoring it).
    assert filtered.file_count <= baseline.file_count - 1
    assert filtered.total_raw_bytes < baseline.total_raw_bytes

    # Predicted-extractor classes after filtering: `markdown` only (heic +
    # Backups bytes are gone, and the baseline skips .git/node_modules).
    classes = {s.extractor_class for s in filtered.by_extractor if s.file_count > 0}
    assert "markdown" in classes
    assert "image" not in classes, "vacation.heic should have been pruned"
