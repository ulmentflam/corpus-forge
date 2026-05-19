"""Phase M Wave 2 — `FilesystemSource.discover()` parity vs legacy walker.

The legacy implementation (replicated inline below) walks `rglob("*")`
and applies `_is_excluded(path, root, exclude_globs)` per file. The new
implementation translates `exclude_globs` into an `IgnoreStack` and runs
the unified `scanner.walker.walk` with descent-time pruning.

This is the highest-behavioral-risk piece of Wave 2: the exclude_globs ↔
IgnoreStack adapter MUST yield byte-identical file lists for every
fixture so production semantics don't drift.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_global_ignore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the user's `~/.config/corpus-forge/ignore` for the
    parity tests. `FilesystemSource.discover()` now reads it (so the CLI
    / MCP / estimate views share one source of truth), but the legacy
    reference walker doesn't, and developer-machine config would make
    parity flaky."""
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", "")


def _legacy_is_excluded(path: Path, root: Path, exclude_globs: list[str]) -> bool:
    """Verbatim copy of pre-Wave-2 `_is_excluded`."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    rel_str = str(rel)
    for pattern in exclude_globs:
        if fnmatch.fnmatch(rel_str, pattern):
            return True
        for part in rel.parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def _legacy_discover(root: Path, exclude_globs: list[str]) -> list[Path]:
    """Verbatim copy of pre-Wave-2 `FilesystemSource.discover()`."""
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _legacy_is_excluded(path, root, exclude_globs):
            continue
        out.append(path)
    return out


def _new_discover(
    root: Path, exclude_globs: list[str]
) -> tuple[list[Path], frozenset[str], frozenset[str]]:
    from corpus_forge.sources.filesystem import FilesystemSource

    src = FilesystemSource(root, exclude_globs=exclude_globs)
    paths = list(src.discover())
    return paths, src._registry_extensions(), src._registry_filenames()


def _compare(root: Path, exclude_globs: list[str]) -> None:
    """Parity check: legacy `_is_excluded`-filtered output, restricted
    to the same registry-extension/filename slice AND baseline-skip-dir
    pruning the new walker applies, must equal the new walker's output.

    Two behaviour changes that the new `FilesystemSource.discover` opts
    into (both are intentional Phase M Wave 2 improvements over legacy):

    1. Pre-stat short-circuit on `include_exts` + `include_filenames`
       to keep the hot path cheap. Legacy yielded every file; the
       registry rejected non-matches later.
    2. Baseline-skip directory pruning (`.git`, `node_modules`,
       `__pycache__`, ...). Legacy walked these too (the registry
       happened to reject most of their contents).

    Both behaviours change `discover()`'s output but NOT the
    end-to-end ingest output (since the rejected files would have been
    skipped by `parse()` anyway). The parity test masks both effects.
    """
    from corpus_forge.estimate import _SKIP_DIR_NAMES, _SKIP_FILE_NAMES

    ref_all = _legacy_discover(root, exclude_globs)
    new_paths, exts, filenames = _new_discover(root, exclude_globs)

    def _under_skip_dir(p: Path) -> bool:
        try:
            rel = p.relative_to(root)
        except ValueError:
            return False
        return any(part in _SKIP_DIR_NAMES for part in rel.parts)

    def _is_baseline_file(p: Path) -> bool:
        name = p.name
        return name in _SKIP_FILE_NAMES or name.startswith("._")

    ref_filtered = sorted(
        p.resolve()
        for p in ref_all
        if (p.suffix.lower() in exts or p.name in filenames)
        and not _under_skip_dir(p)
        and not _is_baseline_file(p)
    )
    new = sorted(p.resolve() for p in new_paths)
    assert new == ref_filtered, (
        f"\n  Symmetric difference: {set(new).symmetric_difference(set(ref_filtered))}\n"
        f"  ref={ref_filtered}\n  new={new}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


def _fixture_a(root: Path) -> None:
    """Glob patterns: *.tmp, .git/**, cache/."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.md").write_text("x")
    (root / "b.tmp").write_text("x")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("x")
    (root / ".git" / "objects").mkdir()
    (root / ".git" / "objects" / "deadbeef").write_text("x")
    (root / "cache").mkdir()
    (root / "cache" / "blob.bin").write_text("x")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("x")


def _fixture_b(root: Path) -> None:
    """Mixed exclude_globs with simple component patterns."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".hidden").write_text("x")
    (root / "notes.md").write_text("x")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lodash").mkdir()
    (root / "node_modules" / "lodash" / "index.js").write_text("x")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x")
    (root / "src" / "tmp.bak").write_text("x")


def _fixture_c(root: Path) -> None:
    """Deeply-nested target/ tree + .DS_Store."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".DS_Store").write_text("x")
    (root / "Cargo.toml").write_text("[package]")
    (root / "src").mkdir()
    (root / "src" / "main.rs").write_text("fn main() {}")
    (root / "target").mkdir()
    (root / "target" / "debug").mkdir()
    (root / "target" / "debug" / "build").mkdir()
    (root / "target" / "debug" / "build" / "artifact").write_text("blob")


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────


def test_parity_fixture_a(tmp_path: Path) -> None:
    _fixture_a(tmp_path)
    _compare(tmp_path, ["*.tmp", ".git/**", "cache/"])


def test_parity_fixture_b(tmp_path: Path) -> None:
    _fixture_b(tmp_path)
    _compare(tmp_path, [".hidden", "node_modules", "*.bak"])


def test_parity_fixture_c(tmp_path: Path) -> None:
    _fixture_c(tmp_path)
    _compare(tmp_path, ["target/**", ".DS_Store"])


def test_parity_no_excludes(tmp_path: Path) -> None:
    _fixture_a(tmp_path)
    _compare(tmp_path, [])


def test_parity_combined_globs(tmp_path: Path) -> None:
    """Realistic exclude_globs lifted from config.example.toml."""
    _fixture_a(tmp_path)
    _fixture_b(tmp_path / "nested")
    _compare(
        tmp_path,
        [
            ".git/**",
            "node_modules/**",
            "**/__pycache__/**",
            ".venv/**",
            "target/**",
            "build/**",
            "dist/**",
            "*.icloud",
            ".trash/**",
            ".DS_Store",
        ],
    )
