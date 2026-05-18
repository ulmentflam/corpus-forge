"""``.corpusignore`` — gitignore-subset matcher (Phase K1).

Two ignore sources are honored by the estimator's walker:

- **Local**: ``<root>/.corpusignore`` at the scan root, or an explicit
  path passed via ``--ignore-file`` / the MCP ``ignore_file`` arg.
- **Global**: ``~/.config/corpus-forge/ignore`` (mirrors git's
  ``~/.config/git/ignore`` convention), overridable via the
  ``CF_GLOBAL_IGNORE_FILE`` env var.

Patterns are a subset of gitignore syntax. The hard-coded
``_SKIP_DIR_NAMES`` baseline in :mod:`corpus_forge.estimate` runs
*before* the ignore stack — negations in any ``.corpusignore`` cannot
un-skip a baseline entry.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "CorpusIgnore",
    "IgnoreStack",
    "load_global_ignore",
    "load_local_ignore",
]


# ── pattern compilation ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _Pattern:
    """One compiled gitignore-subset pattern."""

    pattern_str: str  # original source line (sans newline)
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool
    anchored: bool


def _compile_pattern(line: str) -> _Pattern | None:
    """Compile one source line into a `_Pattern`.

    Returns ``None`` for lines that should be ignored (blank, comment).
    Caller is responsible for stripping the trailing newline.
    """
    raw = line
    # Strip leading/trailing whitespace per gitignore semantics.
    # Trailing spaces can be escaped; we don't support that yet — it's
    # a degenerate corner case.
    stripped = raw.rstrip("\r\n").strip()
    if not stripped:
        return None
    # A `#` at the *very start* is a comment. `\#` escapes a literal `#`.
    if stripped.startswith("#"):
        return None
    # `\#` and `\!` un-escape the leading char.
    negate = False
    if stripped.startswith("\\#"):
        stripped = "#" + stripped[2:]
    elif stripped.startswith("\\!"):
        stripped = "!" + stripped[2:]
    elif stripped.startswith("!"):
        stripped = stripped[1:]
        negate = True

    # Strip trailing slash → directory-only.
    dir_only = stripped.endswith("/")
    if dir_only:
        stripped = stripped[:-1]

    # Leading `/` anchors.
    anchored = stripped.startswith("/")
    if anchored:
        stripped = stripped[1:]

    if not stripped:
        # Degenerate input (e.g. "/" or "!" alone). Skip.
        return None

    # Translate the glob into a regex.
    regex_str = _glob_to_regex(stripped, anchored=anchored, dir_only=dir_only)
    return _Pattern(
        pattern_str=raw.rstrip("\r\n"),
        regex=re.compile(regex_str),
        negate=negate,
        dir_only=dir_only,
        anchored=anchored,
    )


def _glob_to_regex(pat: str, *, anchored: bool, dir_only: bool) -> str:  # noqa: ARG001
    """Translate a gitignore-subset glob into a Python regex string.

    Matches against POSIX-style relative paths.
    """
    # Walk the pattern char-by-char to handle ** vs * vs ?.
    parts: list[str] = []
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            # Check for `**`.
            if i + 1 < n and pat[i + 1] == "*":
                # `**` consumes any number of path components.
                parts.append(".*")
                i += 2
                # Skip a following `/` if present (so `**/foo` works).
                if i < n and pat[i] == "/":
                    i += 1
            else:
                # Single `*` — match within one path component.
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c in ".+(){}|^$":
            # Regex metachars that need escaping.
            parts.append(re.escape(c))
            i += 1
        elif c == "\\":
            # Preserve escape semantics — next char is literal.
            if i + 1 < n:
                parts.append(re.escape(pat[i + 1]))
                i += 2
            else:
                parts.append(re.escape(c))
                i += 1
        else:
            parts.append(re.escape(c))
            i += 1

    pattern_body = "".join(parts)

    # Anchor decisions:
    # - If `anchored` (leading `/` was present) → match must start at
    #   the relative-path root.
    # - Otherwise → match at any depth, i.e. either at root or after a `/`.
    # - Trailing `$` is added but allows an optional trailing `/` to let
    #   directory-only matches succeed against paths the caller passes
    #   as bare strings (we let the caller decide is_dir).
    regex = "^" + pattern_body + r"(?:/|$)" if anchored else r"(?:^|/)" + pattern_body + r"(?:/|$)"
    return regex


# ── core data class ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorpusIgnore:
    """One parsed ignore file — matcher only, no I/O after construction."""

    root: Path
    patterns: tuple[_Pattern, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls, root: Path) -> CorpusIgnore:
        return cls(root=root, patterns=())

    @classmethod
    def from_lines(cls, lines: Iterable[str], *, root: Path) -> CorpusIgnore:
        compiled: list[_Pattern] = []
        for line in lines:
            p = _compile_pattern(line)
            if p is not None:
                compiled.append(p)
        return cls(root=root, patterns=tuple(compiled))

    @classmethod
    def from_file(cls, path: Path, *, root: Path | None = None) -> CorpusIgnore:
        """Parse from a file. Raises ``FileNotFoundError`` and ``OSError``."""
        text = path.read_text(encoding="utf-8")
        return cls.from_lines(text.splitlines(), root=root if root is not None else path.parent)

    def matches(self, path: Path, *, is_dir: bool) -> bool:
        """True iff ``path`` (under ``self.root``) is ignored by this set.

        The path must be absolute and under ``self.root``; otherwise
        ``ValueError`` is raised. The matcher computes the POSIX-relative
        path internally.
        """
        if not self.patterns:
            return False
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            raise ValueError(f"path {path!r} is not under ignore-file root {self.root!r}") from None
        # POSIX-normalise (Windows path separators → forward slashes).
        rel_str = rel.as_posix()

        # gitignore semantics: the *last* matching pattern wins.
        ignored = False
        for p in self.patterns:
            if p.dir_only and not is_dir:
                continue
            if p.regex.search(rel_str) is not None:
                ignored = not p.negate
        return ignored


# ── stack composition ────────────────────────────────────────────────────


@dataclass(frozen=True)
class IgnoreStack:
    """Ordered stack of ``CorpusIgnore`` sets, consulted earliest-first.

    Later sets can un-ignore earlier matches via ``!``. The hard-coded
    ``_SKIP_DIR_NAMES`` baseline runs *before* this stack, so it cannot
    be un-ignored.
    """

    sets: tuple[CorpusIgnore, ...] = field(default_factory=tuple)

    def matches(self, path: Path, *, is_dir: bool) -> bool:
        if not self.sets:
            return False
        # Walk every set; the last one that has a decision (ignore OR
        # negate) wins. Sets that don't match at all leave the running
        # decision untouched.
        ignored = False
        for s in self.sets:
            for p in s.patterns:
                if p.dir_only and not is_dir:
                    continue
                # `matches` here uses the *set's* root for relative
                # computation. We can't reuse `CorpusIgnore.matches`
                # directly because we need per-pattern visibility to
                # honor negation order across sets.
                try:
                    rel = path.relative_to(s.root)
                except ValueError:
                    raise ValueError(
                        f"path {path!r} is not under ignore-file root {s.root!r}"
                    ) from None
                if p.regex.search(rel.as_posix()) is not None:
                    ignored = not p.negate
        return ignored


# ── lookup helpers ───────────────────────────────────────────────────────


_DEFAULT_GLOBAL_IGNORE = Path.home() / ".config" / "corpus-forge" / "ignore"
_GLOBAL_IGNORE_ENV = "CF_GLOBAL_IGNORE_FILE"


def load_global_ignore() -> CorpusIgnore:
    """Look up the user-global ignore file.

    Resolution order:
      1. ``CF_GLOBAL_IGNORE_FILE`` env var.
         - Empty string → empty CorpusIgnore.
         - Non-empty → load that path; missing → empty (do not raise).
      2. ``~/.config/corpus-forge/ignore`` if it exists.
      3. Empty CorpusIgnore.
    """
    env_val = os.environ.get(_GLOBAL_IGNORE_ENV)
    if env_val is not None:
        if env_val == "":
            return CorpusIgnore.empty(Path.home())
        env_path = Path(env_val).expanduser()
        if not env_path.exists():
            return CorpusIgnore.empty(Path.home())
        try:
            return CorpusIgnore.from_file(env_path, root=env_path.parent)
        except (FileNotFoundError, OSError):
            return CorpusIgnore.empty(Path.home())

    default = _DEFAULT_GLOBAL_IGNORE.expanduser()
    if default.exists():
        try:
            return CorpusIgnore.from_file(default, root=default.parent)
        except (FileNotFoundError, OSError):
            return CorpusIgnore.empty(Path.home())
    return CorpusIgnore.empty(Path.home())


def load_local_ignore(root: Path, *, override: Path | None = None) -> CorpusIgnore:
    """Look up the per-tree ignore file.

    - ``override`` (from ``--ignore-file``) takes precedence and **must
      exist** — missing path raises ``FileNotFoundError``.
    - With no override, auto-detect ``<root>/.corpusignore``; missing →
      empty CorpusIgnore (silent).
    """
    if override is not None:
        return CorpusIgnore.from_file(override, root=root)
    auto = root / ".corpusignore"
    if not auto.exists():
        return CorpusIgnore.empty(root)
    return CorpusIgnore.from_file(auto, root=root)
