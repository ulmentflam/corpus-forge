"""Phase M Wave 2 — `corpus_forge.scanner.walker.walk` unit tests.

The walker is an `os.scandir`-based iterative walker that:

  - prunes baseline `_SKIP_DIR_NAMES` (e.g. `.git`, `node_modules`) before
    descending,
  - calls `IgnoreStack.directory_pruned` to prune ignore-driven subtrees,
  - short-circuits on `include_exts` / `include_filenames` BEFORE `stat()`,
  - skips symlinks unless `follow_symlinks=True`,
  - sorts per-directory entries when `sort=True`.

These tests pin every one of those behaviours.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _make_tree(root: Path, layout: dict[str, str | None]) -> None:
    """Materialise a sparse tree under ``root``.

    ``layout`` maps POSIX-relative paths to either:
      - ``None`` — create a directory
      - ``str``  — create a file with that text content
    """
    for rel, content in layout.items():
        p = root / rel
        if content is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# Baseline skip-dir
# ─────────────────────────────────────────────────────────────────────────


def test_baseline_skip_dirs_never_descended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "ok.md": "x",
            "src/main.py": "x",
            ".git/HEAD": "ref: refs/heads/main",
            ".git/objects/aa/bbbbbb": "x",
            "node_modules/foo/index.js": "x",
            "node_modules/foo/package.json": "{}",
            "__pycache__/main.cpython-313.pyc": "x",
        },
    )

    visited_dirs: list[str] = []
    real_scandir = os.scandir

    def _spy_scandir(path):  # type: ignore[no-untyped-def]
        visited_dirs.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr("corpus_forge.scanner.walker.os.scandir", _spy_scandir)

    files = [e.path for e in walk(tmp_path)]
    rels = sorted(str(Path(p).relative_to(tmp_path).as_posix()) for p in files)
    assert "ok.md" in rels
    assert "src/main.py" in rels
    assert all(not r.startswith(".git/") for r in rels)
    assert all(not r.startswith("node_modules/") for r in rels)
    assert all(not r.startswith("__pycache__/") for r in rels)

    # The walker must NEVER have scandir'd inside the baseline-skip dirs.
    for visited in visited_dirs:
        assert ".git" not in Path(visited).parts
        assert "node_modules" not in Path(visited).parts
        assert "__pycache__" not in Path(visited).parts


def test_ignorestack_dir_pattern_prunes_descent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "keep.md": "x",
            # Use a directory name NOT in the baseline-skip set so the
            # ignore-stack prune is the discriminator.
            "wasm/output.txt": "x",
            "wasm/sub/more.txt": "x",
            "src/main.py": "x",
        },
    )

    ig = IgnoreStack(sets=(CorpusIgnore.from_lines(["wasm/"], root=tmp_path),))

    visited_dirs: list[str] = []
    real_scandir = os.scandir

    def _spy_scandir(path):  # type: ignore[no-untyped-def]
        visited_dirs.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr("corpus_forge.scanner.walker.os.scandir", _spy_scandir)

    files = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix()) for e in walk(tmp_path, ignore=ig)
    )
    assert files == ["keep.md", "src/main.py"]

    # No descent under `wasm/`.
    for visited in visited_dirs:
        assert "wasm" not in Path(visited).parts


def test_negation_in_directory_pruned_disables_fast_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`directory_pruned` is conservative when negations exist — but
    the legacy `matches(is_dir=True)` fallback still applies.

    The combination of `wasm/` (excludes the dir) AND
    `!wasm/keep.txt` (intends to re-include a child) preserves strict
    gitignore semantics: an excluded parent CANNOT be re-included by
    a negation pointing inside it. The conservative `directory_pruned`
    fast path returns False, but the fallback `matches(is_dir=True)`
    call still prunes the subtree.

    This test asserts that `directory_pruned` was CALLED and returned
    False (the fast path correctly identified that a negation exists),
    and that the walker still relied on the per-call `matches` to
    prune `wasm/`. (`build/` would be baseline-pruned without ever
    invoking `directory_pruned`, so we use a non-baseline name.)
    """
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "wasm/output.txt": "x",
            "src/main.py": "x",
        },
    )

    ig = IgnoreStack(sets=(CorpusIgnore.from_lines(["wasm/", "!wasm/keep.txt"], root=tmp_path),))

    calls: list[tuple[str, bool]] = []
    real = IgnoreStack.directory_pruned

    def _spy(self, rel: str):  # type: ignore[no-untyped-def]
        out = real(self, rel)
        calls.append((rel, out))
        return out

    monkeypatch.setattr(IgnoreStack, "directory_pruned", _spy)

    files = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix()) for e in walk(tmp_path, ignore=ig)
    )
    assert "src/main.py" in files
    # The conservative fast path was called and returned False for `wasm`.
    wasm_calls = [(r, o) for r, o in calls if r == "wasm"]
    assert wasm_calls, f"directory_pruned never called for `wasm`; calls={calls}"
    assert all(out is False for _, out in wasm_calls), (
        f"directory_pruned should be False (negation present); calls={wasm_calls}"
    )
    # `wasm/output.txt` MUST NOT be yielded: strict gitignore — the
    # parent dir is excluded by `matches(is_dir=True)` regardless of
    # the negation pointing inside.
    assert "wasm/output.txt" not in files


def test_negation_without_parent_dir_exclusion_re_includes(tmp_path: Path) -> None:
    """When the parent dir is NOT excluded as a unit, the negation works.

    Pattern set `*.txt` + `!keep.txt`: the dir isn't pruned because no
    pattern matches `wasm` itself — so the walker descends. The
    per-file `matches` then ignores `output.txt` and re-includes
    `keep.txt`.
    """
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "wasm/output.txt": "x",
            "wasm/keep.txt": "x",
            "src/main.py": "x",
        },
    )

    ig = IgnoreStack(sets=(CorpusIgnore.from_lines(["*.txt", "!keep.txt"], root=tmp_path),))

    files = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix()) for e in walk(tmp_path, ignore=ig)
    )
    assert "src/main.py" in files
    assert "wasm/keep.txt" in files
    assert "wasm/output.txt" not in files


# ─────────────────────────────────────────────────────────────────────────
# include_exts short-circuit
# ─────────────────────────────────────────────────────────────────────────


def test_include_exts_short_circuits_before_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """include_exts must reject the entry BEFORE `entry.stat()` runs."""
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "a.md": "x",
            "b.iso": "x",
            "c.dmg": "x",
            "d.md": "x",
        },
    )

    stat_calls: list[str] = []
    real_dirent_stat = os.DirEntry.stat  # type: ignore[attr-defined]

    def _spy_stat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        stat_calls.append(self.name)
        return real_dirent_stat(self, *args, **kwargs)

    monkeypatch.setattr(os.DirEntry, "stat", _spy_stat)

    yielded = sorted(Path(e.path).name for e in walk(tmp_path, include_exts=frozenset({".md"})))
    assert yielded == ["a.md", "d.md"]

    # Stat must NOT have been called for the .iso / .dmg files.
    assert "b.iso" not in stat_calls
    assert "c.dmg" not in stat_calls


def test_include_filenames_matches_extension_less(tmp_path: Path) -> None:
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "Makefile": "all:\n\techo hi\n",
            "Dockerfile": "FROM alpine\n",
            "notes.md": "x",
            "weird-no-ext": "skip me",
        },
    )

    yielded = sorted(
        Path(e.path).name
        for e in walk(
            tmp_path,
            include_exts=frozenset({".md"}),
            include_filenames=frozenset({"Makefile", "Dockerfile"}),
        )
    )
    assert yielded == ["Dockerfile", "Makefile", "notes.md"]


# ─────────────────────────────────────────────────────────────────────────
# Symlinks
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows CI")
def test_follow_symlinks_false_skips_symlinked_dir(tmp_path: Path) -> None:
    from corpus_forge.scanner import walk

    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.md").write_text("x")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    (tmp_path / "top.md").write_text("y")

    files = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix())
        for e in walk(tmp_path, follow_symlinks=False)
    )
    # The real path is yielded; the symlinked one is NOT.
    assert "top.md" in files
    assert "real/inside.md" in files
    assert all(not f.startswith("linked/") for f in files)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows CI")
def test_follow_symlinks_false_skips_broken_symlink(tmp_path: Path) -> None:
    from corpus_forge.scanner import walk

    (tmp_path / "real.md").write_text("x")
    (tmp_path / "broken").symlink_to(tmp_path / "does_not_exist")

    files = sorted(Path(e.path).name for e in walk(tmp_path, follow_symlinks=False))
    assert files == ["real.md"]


# ─────────────────────────────────────────────────────────────────────────
# Ordering + handle hygiene
# ─────────────────────────────────────────────────────────────────────────


def test_sort_true_yields_posix_sorted_within_each_directory(tmp_path: Path) -> None:
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "z.md": "x",
            "a.md": "x",
            "m.md": "x",
        },
    )

    files = [Path(e.path).name for e in walk(tmp_path, sort=True)]
    assert files == sorted(files)


def test_scandir_context_handles_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every `os.scandir` iterator must be entered as a context manager.

    We verify by wrapping the real scandir and asserting `__exit__` was
    called on every iterator (count of closes == count of opens).
    """
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            "a.md": "x",
            "sub/b.md": "x",
            "sub/nest/c.md": "x",
        },
    )

    opens = 0
    closes = 0
    real_scandir = os.scandir

    class _TrackedIterator:
        def __init__(self, inner) -> None:
            self._inner = inner

        def __iter__(self):
            return iter(self._inner)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *exc):
            nonlocal closes
            closes += 1
            return self._inner.__exit__(*exc)

    def _tracked_scandir(path):  # type: ignore[no-untyped-def]
        nonlocal opens
        opens += 1
        return _TrackedIterator(real_scandir(path))

    monkeypatch.setattr("corpus_forge.scanner.walker.os.scandir", _tracked_scandir)

    list(walk(tmp_path))
    assert opens > 0
    assert opens == closes, f"opens={opens} closes={closes} — handles leaked"


# ─────────────────────────────────────────────────────────────────────────
# workers > 1 not yet implemented
# ─────────────────────────────────────────────────────────────────────────


def test_workers_greater_than_one_raises(tmp_path: Path) -> None:
    from corpus_forge.scanner import walk

    (tmp_path / "x.md").write_text("hi")
    with pytest.raises(NotImplementedError):
        list(walk(tmp_path, workers=2))


# ─────────────────────────────────────────────────────────────────────────
# AppleDouble + skip-files
# ─────────────────────────────────────────────────────────────────────────


def test_baseline_skip_files_filtered(tmp_path: Path) -> None:
    from corpus_forge.scanner import walk

    _make_tree(
        tmp_path,
        {
            ".DS_Store": "junk",
            "._weird": "junk",
            "real.md": "ok",
        },
    )

    files = sorted(Path(e.path).name for e in walk(tmp_path))
    assert files == ["real.md"]
