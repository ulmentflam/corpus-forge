# Phase K — `.corpusignore` (K1: estimator scope)

**Motivation:** users running `corpus-forge estimate <large-tree>` get inflated numbers from directories they'd never sync (vendor backups, generated artifacts, downloads of binaries). The estimator already hard-codes a small `_SKIP_DIR_NAMES` set (`.git`, `node_modules`, `__pycache__`, etc.) but has no user-side way to add `Backups/`, `*.heic`, `tmp/`, or anything else specific to that user's tree.

This phase ships a `.corpusignore` file — gitignore-style — that the estimator honors. **K1** is the estimator slice only (the user's immediate need). **K2** (follow-up) wires the same matcher into `FilesystemSource` and `MarkdownVaultSource` so ingest behavior matches the estimate.

**Target release:** `0.1.0b3` (small feature drop after 0.1.0b2). Beta line continues.

**Status:** planning → execution. Workflow: tdd-principal owns it; orchestrator (this session) commits.

---

## K1 — `.corpusignore` honored by `corpus-forge estimate`

### Goal

Two complementary ignore sources — **local** (per-tree) and **global** (user-scoped) — combined into a single matcher consulted by the estimator's walk. Pattern syntax is a gitignore subset, so users don't have to learn a new mini-language.

**Local file:** `<root>/.corpusignore` at the root of the scanned tree, or `--ignore-file PATH` override.

**Global file:** `~/.config/corpus-forge/ignore` (mirrors git's `~/.config/git/ignore` convention). Applies to *every* corpus-forge invocation on this machine. No file extension; the directory itself is where the user's `config.toml` already lives.

**Composition:** global patterns are loaded first, then local. Negations (`!pattern`) in either file can un-ignore matches from earlier patterns (gitignore semantics: later wins). The hard-coded `_SKIP_DIR_NAMES` remain absolute — neither file can un-skip them.

**Override knob:** environment variable `CF_GLOBAL_IGNORE_FILE` (path; empty string disables the global file). Mostly for tests and CI; the user shouldn't need it.

### Pattern syntax (subset of gitignore)

- One pattern per line.
- Lines starting with `#` are comments. Trailing `#` is *not* a comment-start (matches gitignore behavior).
- Blank lines are ignored.
- Leading `/` anchors the pattern to the ignore-file root. Without it, the pattern matches at any depth.
- Trailing `/` makes the pattern directory-only (skips matching directories *and* prunes the walk; does not match files of the same name).
- `*` matches any chars except `/`. `**` matches any number of path components. `?` matches one char.
- `!pattern` negates an earlier ignore (un-ignore). Order matters; later patterns win.
- Patterns are matched against the path **relative to the ignore-file root**, using POSIX separators (`/`) regardless of platform.

### CLI surface

```bash
corpus-forge estimate <path>
# Honors <path>/.corpusignore by default if it exists.

corpus-forge estimate <path> --ignore-file ~/.config/corpus-forge/global.corpusignore
# Override the default lookup. Passing a non-existent path is an error.

corpus-forge estimate <path> --no-ignore-file
# Disable .corpusignore entirely. Useful for raw worst-case sizing.
```

The MCP `estimate_sync_size` tool gains an `ignore_file` arg (optional string) that mirrors `--ignore-file`. Absent → default lookup. Empty string → disabled.

### Module surface

New `corpus_forge/ignore.py`:

```python
@dataclass(frozen=True)
class CorpusIgnore:
    """One parsed ignore file — matcher only, no I/O after construction."""
    root: Path
    patterns: tuple[_Pattern, ...]  # parsed patterns in source order

    def matches(self, path: Path, *, is_dir: bool) -> bool:
        """True iff `path` (relative to self.root) should be skipped under
        this set's patterns alone (negations within this set apply)."""
        ...

    @classmethod
    def empty(cls, root: Path) -> "CorpusIgnore": ...

    @classmethod
    def from_file(cls, path: Path, *, root: Path | None = None) -> "CorpusIgnore":
        """Parse from a file. Raises FileNotFoundError; OSError on permission."""
        ...

    @classmethod
    def from_lines(cls, lines: Iterable[str], *, root: Path) -> "CorpusIgnore":
        """Parse from an iterable of strings (for tests / piped input)."""
        ...


@dataclass(frozen=True)
class IgnoreStack:
    """Ordered stack of CorpusIgnore sets, consulted earliest-first.

    Order is [global, local] — later sets can un-ignore earlier matches.
    The hard-coded `_SKIP_DIR_NAMES` in `estimate.py` are applied
    *before* this stack, so they cannot be un-ignored.
    """
    sets: tuple[CorpusIgnore, ...]

    def matches(self, path: Path, *, is_dir: bool) -> bool:
        """True iff `path` is ignored after composing every set in order."""
        ...


def load_global_ignore() -> CorpusIgnore:
    """Look up the user-global ignore file.

    Resolution order:
      1. `CF_GLOBAL_IGNORE_FILE` env var. Empty string → empty CorpusIgnore.
      2. `~/.config/corpus-forge/ignore`.
      3. Empty CorpusIgnore.
    """
    ...


def load_local_ignore(root: Path, *, override: Path | None = None) -> CorpusIgnore:
    """Look up the per-tree ignore file.

    `override` (from `--ignore-file`) takes precedence and is required-to-
    exist. With no override, auto-detect `<root>/.corpusignore`; if missing,
    return empty.
    """
    ...
```

The `_Pattern` is an internal `frozen` dataclass holding the original glob, a compiled regex, and the `negate` / `dir_only` / `anchored` flags. The compilation logic is the gitignore-subset translator described above.

### Wiring into the estimator

`corpus_forge/estimate.py::_walk_tree` accepts an additional `ignore: IgnoreStack | None` parameter. When non-None:
- Before recursing into a child dir, call `ignore.matches(child, is_dir=True)` — if True, prune (do not recurse).
- For each candidate file, call `ignore.matches(child, is_dir=False)` — if True, skip.

The existing `_SKIP_DIR_NAMES` / `_SKIP_FILE_NAMES` short-circuit stays as the *baseline*; the `IgnoreStack` is *additive* (it can only exclude more, not un-exclude something the baseline skipped). **Exception**: a `!pattern` negation in any ignore file *cannot* re-include a `_SKIP_DIR_NAMES` entry; the hard-coded skips remain absolute. Document this.

### CLI implementation notes

In `corpus_forge/cli.py::estimate`:
- Add `--ignore-file PATH | None` (default: None → "auto-detect at `<path>/.corpusignore`").
- Add `--no-ignore-file` flag (Typer bool; mutually exclusive with `--ignore-file` — affects the *local* file only).
- Add `--no-global-ignore` flag (Typer bool; disables the global file for this invocation).
- Resolution order (each leg independent):
  - **Local:**
    1. `--no-ignore-file` → empty local set.
    2. `--ignore-file PATH` → `load_local_ignore(root, override=PATH)`. Missing file is an error.
    3. Neither flag → auto-detect `root / ".corpusignore"`; if exists, parse; else empty.
  - **Global:**
    1. `--no-global-ignore` → empty global set.
    2. Otherwise → `load_global_ignore()` (honors `CF_GLOBAL_IGNORE_FILE` env var).
- Compose: `IgnoreStack((global_set, local_set))` — global first, local last so local wins ties.
- Pass the stack into `estimate_sync()` via a new keyword arg.

The MCP tool dispatch (`_dispatch_estimate_sync_size`) gets the same handling. New optional args: `ignore_file` (string; same semantics as `--ignore-file`; empty string disables local) and `disable_global_ignore` (bool; default False).

### Tests

`tests/unit/test_corpusignore.py` (new) — ~25 cases covering:
- Empty / comments-only / blank-only files.
- Single glob (`*.heic`).
- Anchored vs unanchored (`/Backups/` vs `Backups/`).
- Directory-only (`Backups/`).
- Negation order (`*.log` + `!important.log`).
- `**` recursive globs.
- `?` single-char glob.
- Pattern escapes (`\#` to match a literal hash, `\!` to match a literal bang at start).
- POSIX separator semantics on Windows-style input (gitignore behavior).
- `from_file` IOError → FileNotFoundError raised; permission error → OSError propagated (don't swallow).
- Round-trip: parse → serialise pattern back to its `pattern_str` field (helpful for debug).

`tests/unit/test_cli_estimate.py` (additions) — 5 cases:
- `--ignore-file PATH` is honored.
- `--no-ignore-file` is honored.
- Auto-detect `.corpusignore` at root when neither flag.
- `--ignore-file` pointing at a missing path errors with a clear message.
- The mutually-exclusive guard rejects `--ignore-file X --no-ignore-file`.

`tests/unit/test_mcp_estimate.py` (additions) — 3 cases:
- `ignore_file` arg honored.
- Empty string disables.
- Missing field falls back to auto-detect.

`tests/integration/test_estimate_real_tree.py` (additions) — 1 case: walk a fixture tree with a real `.corpusignore`, assert pruning is correct.

### Done criteria

- [x] `corpus_forge/ignore.py` exists with `CorpusIgnore`, `IgnoreStack`, `load_global_ignore`, `load_local_ignore` per the surfaces above — shipped.
- [x] `corpus-forge estimate` accepts `--ignore-file` / `--no-ignore-file` / `--no-global-ignore` — shipped (`corpus_forge/cli.py:2833`).
- [x] MCP `estimate_sync_size` accepts `ignore_file` + `disable_global_ignore` args — shipped (`corpus_forge/mcp/server.py:182,190,1542`).
- [x] Auto-detect at `<root>/.corpusignore` works — shipped.
- [x] Global ignore loaded from `~/.config/corpus-forge/ignore` by default; overridable via `CF_GLOBAL_IGNORE_FILE` env var — shipped.
- [x] Hard-coded `_SKIP_DIR_NAMES` remain absolute (negations cannot un-skip them) — shipped.
- [x] Unit + integration tests green, coverage ≥90% on `corpus_forge/ignore.py` — passing post-PR #54.
- [x] `make ci` green — verified at PR #54 merge gate.
- [x] `.corpusignore.example` lands at repo root with sensible defaults (Apple metadata, Photos library, large media, common backup dirs, etc.) — shipped.
- [x] CHANGELOG `[Unreleased]` adds a "Phase K — .corpusignore" subhead with the flags, MCP args, paths, and the example file — shipped (`#### Phase K — .corpusignore`).
- [x] CLAUDE.md / GEMINI.md / AGENTS.md briefly reference `.corpusignore` (local + global) in the "First-run sanity" section — shipped.

---

## K2 — Honor `.corpusignore` in `FilesystemSource` + `MarkdownVaultSource` (follow-up)

Out of scope for this slice. Ship K1 first so the user's iCloud estimate is unblocked; K2 closes the loop so estimate and ingest agree on what's in/out.

---

## Hard constraints (carried from Phase J)

1. **Workers stage; orchestrator commits.** Subagents cannot sign commits.
2. **Verify N-files/+X/-Y summary before pushing** (iCloud sync race).
3. **No drive-by refactors.** K1 surface is bounded to: new `ignore.py`, new tests file, plus targeted additions to `estimate.py`, `cli.py`, `mcp/server.py`, and `tests/unit/test_cli_estimate.py` / `tests/unit/test_mcp_estimate.py` / `tests/integration/test_estimate_real_tree.py`. CHANGELOG + the example file. Do NOT touch sources / sync code (K2's job).
4. **CHANGELOG `[Unreleased]`** — add an "Added" entry under a new "Phase K — .corpusignore" subhead.
