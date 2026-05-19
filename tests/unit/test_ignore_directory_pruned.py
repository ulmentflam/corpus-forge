"""Phase M Wave 2 — IgnoreStack.directory_pruned conservative-negation tests.

The walker calls `IgnoreStack.directory_pruned(rel_path)` to decide whether
a directory subtree can be skipped during descent without consulting any
file inside it. The algorithm is intentionally CONSERVATIVE: if *any*
pattern anywhere in the stack is a negation (``!foo``), the method must
return False — we can't prove no file under the candidate directory would
be re-included by a negation, so we must descend and let the per-file
`matches()` path make the decision.

Baseline `_SKIP_DIR_NAMES` (e.g. `.git`, `node_modules`) is enforced by
the walker itself, independent of this method.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.ignore import CorpusIgnore, IgnoreStack


def _stack(*lines_groups: list[str], root: Path | None = None) -> IgnoreStack:
    root = root if root is not None else Path("/tmp/fake-root")
    sets = tuple(CorpusIgnore.from_lines(lines, root=root) for lines in lines_groups)
    return IgnoreStack(sets=sets)


def test_empty_stack_returns_false() -> None:
    stack = IgnoreStack()
    assert stack.directory_pruned("anything") is False
    assert stack.directory_pruned("a/b/c") is False


def test_single_dir_pattern_matches_name() -> None:
    stack = _stack(["node_modules/"])
    assert stack.directory_pruned("node_modules") is True


def test_single_dir_pattern_does_not_match_unrelated_dir() -> None:
    stack = _stack(["node_modules/"])
    assert stack.directory_pruned("src") is False
    assert stack.directory_pruned("sub/src") is False


def test_dir_pattern_matches_at_any_depth_when_unanchored() -> None:
    stack = _stack(["build/"])
    assert stack.directory_pruned("build") is True
    assert stack.directory_pruned("packages/foo/build") is True


def test_anchored_dir_pattern_only_matches_root() -> None:
    stack = _stack(["/.cache/"])
    assert stack.directory_pruned(".cache") is True
    assert stack.directory_pruned("sub/.cache") is False


def test_negation_anywhere_disables_pruning() -> None:
    # Even if the same set contains a clearly-matching dir pattern, the
    # presence of any negation flips the conservative switch off.
    stack = _stack(["build/", "!build/dist/keep.txt"])
    assert stack.directory_pruned("build") is False


def test_unrelated_negation_also_disables_pruning_conservatively() -> None:
    # The conservative algorithm doesn't try to reason about whether the
    # negation can reach inside the candidate directory — any negation,
    # anywhere in the stack, returns False.
    stack = _stack(["build/", "!unrelated/keep.txt"])
    assert stack.directory_pruned("build") is False


def test_pattern_without_trailing_slash_still_counts_for_directory() -> None:
    # gitignore patterns without trailing slash CAN match directories.
    # The walker passes a directory path; `directory_pruned` must honor
    # both `build/` (dir_only) and `build` (any) patterns.
    stack = _stack(["build"])
    assert stack.directory_pruned("build") is True


def test_multiple_sets_in_stack_any_match_prunes() -> None:
    # Global stack on top, local underneath, neither containing a negation.
    stack = _stack(["dist/"], ["target/"])
    assert stack.directory_pruned("dist") is True
    assert stack.directory_pruned("target") is True
    assert stack.directory_pruned("src") is False


def test_negation_in_either_set_disables_pruning() -> None:
    # Negation in the SECOND set still disables pruning for the WHOLE stack.
    stack = _stack(["dist/"], ["!keep/foo.txt"])
    assert stack.directory_pruned("dist") is False
