"""``corpus-forge ignore ...`` admin verbs (Phase M Wave 3).

Seven Typer sub-commands and the matching reusable helper functions that
the MCP layer (``corpus_forge.mcp.server``) also delegates to:

- ``list [--local|--global|--all] [--path PATH]``
- ``add <pattern> [--local|--global] [--path PATH]``
- ``remove <pattern> [--local|--global] [--path PATH]``
- ``edit [--local|--global]``
- ``validate <path>``
- ``sync [--root PATH] [--also-global]``
- ``init [--root PATH] [--force]``

The module is intentionally written so the CLI verbs are thin shells
around module-level functions. The MCP dispatchers call those functions
directly — no Typer indirection — so the two surfaces share one
implementation.

Reuse map (see Wave 3 spec in ``.planning/tdd/phase_m_corpusignore_zotero.md``):

- :func:`corpus_forge.ignore._compile_pattern` for pattern validation.
- :data:`corpus_forge.ignore_defaults.MANAGED_START` / ``MANAGED_END`` +
  :func:`parse_managed_lines` for managed-region detection.
- :func:`corpus_forge.ignore_lifecycle.atomic_write_text` for safe writes.
- :func:`corpus_forge.ignore_lifecycle.write_corpusignore` /
  :func:`resync_all` for ``sync``.
- :mod:`corpus_forge.admin.config` for the editor resolver pattern.
- :mod:`corpus_forge.ui.console` / :mod:`corpus_forge.ui.agent` for output.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple

import typer

from corpus_forge.ignore import _compile_pattern
from corpus_forge.ignore_defaults import (
    MANAGED_END,
    MANAGED_START,
    parse_managed_lines,
    render_managed_block,
)
from corpus_forge.ignore_lifecycle import (
    atomic_write_text,
    discover_data_roots,
    write_corpusignore,
)
from corpus_forge.ui import agent as ui_agent
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok
from corpus_forge.ui.console import warn as ui_warn

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config

logger = logging.getLogger(__name__)

ignore_app = typer.Typer(
    help="Browse and edit ``.corpusignore`` files (local + global).",
    add_completion=False,
)


# ── path resolution ────────────────────────────────────────────────────


def resolve_local_path(root: Path | None = None) -> Path:
    """Return the on-disk path of the local ``.corpusignore`` under ``root``.

    Defaults to ``Path.cwd() / ".corpusignore"`` so the verbs Just Work
    when run from the project root.
    """

    if root is None:
        root = Path.cwd()
    return root / ".corpusignore"


def resolve_global_path() -> Path:
    """Resolve the path of the user-global ignore file.

    Honors ``CF_GLOBAL_IGNORE_FILE``; otherwise defaults to
    ``~/.config/corpus-forge/ignore`` (mirrors git's
    ``~/.config/git/ignore`` convention).
    """

    env_val = os.environ.get("CF_GLOBAL_IGNORE_FILE")
    if env_val:
        return Path(env_val).expanduser()
    return Path.home() / ".config" / "corpus-forge" / "ignore"


# ── named tuples ───────────────────────────────────────────────────────


class IgnoreEntry(NamedTuple):
    """One row in a parsed ignore file (matcher-independent)."""

    pattern: str
    source: str  # "local" | "global"
    managed: bool
    line: int  # 1-based


class AddResult(NamedTuple):
    """Outcome of :func:`add_pattern`."""

    path: Path
    pattern: str
    added: bool  # False on idempotent no-op (duplicate)


class RemoveResult(NamedTuple):
    """Outcome of :func:`remove_pattern`."""

    path: Path
    pattern: str
    removed: bool  # False on idempotent no-op (pattern absent)


class ValidationResult(NamedTuple):
    """Outcome of :func:`validate_file`.

    On failure ``ok=False`` and the offending ``line`` (1-based),
    ``pattern``, and human-readable ``reason`` are populated.
    """

    ok: bool
    line: int | None
    pattern: str | None
    reason: str | None


class SyncResult(NamedTuple):
    """Outcome of :func:`sync_managed`."""

    updated: list[Path]


# ── exceptions ─────────────────────────────────────────────────────────


class ManagedRegionProtected(Exception):
    """Raised when :func:`remove_pattern` is asked to touch a managed line."""

    def __init__(self, *, pattern: str, path: Path) -> None:
        self.pattern = pattern
        self.path = path
        super().__init__(
            f"Pattern {pattern!r} sits inside the managed block in {path} — "
            "use `corpus-forge ignore sync` to regenerate the managed region."
        )


class InvalidPattern(Exception):
    """Raised when a pattern fails the pre-insert validation."""

    def __init__(self, *, pattern: str, reason: str) -> None:
        self.pattern = pattern
        self.reason = reason
        super().__init__(f"Invalid ignore pattern {pattern!r}: {reason}")


# ── pattern validation ─────────────────────────────────────────────────


def _validate_pattern_or_raise(pattern: str) -> None:
    """Reject patterns whose translation cannot produce a valid regex.

    The base :func:`corpus_forge.ignore._compile_pattern` is forgiving by
    design (it escapes nearly every metachar), so we layer two extra
    checks before delegating to it:

    1. The stripped pattern must be non-empty after the
       comment/blank/negation-only short-circuit.
    2. Bracket balance: unbalanced ``[`` / ``(`` mark the input as
       structurally broken even if the resulting regex would still
       compile (because every char is escaped). Users have stronger
       expectations of these as character-class markers than gitignore
       semantics give them.
    """

    stripped = pattern.strip()
    if not stripped:
        raise InvalidPattern(pattern=pattern, reason="empty after strip")
    # Bracket balance — character-class and group sanity.
    if stripped.count("[") != stripped.count("]"):
        raise InvalidPattern(pattern=pattern, reason="unbalanced '[' / ']' character class")
    if stripped.count("(") != stripped.count(")"):
        raise InvalidPattern(pattern=pattern, reason="unbalanced parentheses")
    try:
        compiled = _compile_pattern(stripped)
    except re.error as exc:
        raise InvalidPattern(pattern=pattern, reason=f"regex compile failed: {exc}") from exc
    if compiled is None:
        raise InvalidPattern(
            pattern=pattern, reason="pattern reduces to no-op (only comment / negation)"
        )


# ── file parsing helpers ───────────────────────────────────────────────


def _parse_file(path: Path, *, source: str) -> list[IgnoreEntry]:
    """Walk ``path`` line-by-line and return :class:`IgnoreEntry` rows.

    Comment + blank lines are dropped; managed sentinels themselves are
    also dropped (they aren't user-visible patterns). Lines BETWEEN the
    sentinels are marked ``managed=True``.
    """

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    out: list[IgnoreEntry] = []
    in_managed = False
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        # Track managed sentinels first — these are NOT user-visible
        # patterns, so they don't get an entry.
        if line == MANAGED_START:
            in_managed = True
            continue
        if line == MANAGED_END:
            in_managed = False
            continue
        # Skip blanks and comments.
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(
            IgnoreEntry(
                pattern=stripped,
                source=source,
                managed=in_managed,
                line=i,
            )
        )
    return out


def _managed_line_set(path: Path) -> set[str]:
    """Return the set of pattern strings inside the managed block.

    Returns the empty set when the file does not exist or has no managed
    block. Comment lines (e.g. the ``# Generated <ts>`` header) are
    excluded — only data lines are reported.
    """

    if not path.exists():
        return set()
    body = parse_managed_lines(path.read_text(encoding="utf-8"))
    if body is None:
        return set()
    out: set[str] = set()
    for line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.add(stripped)
    return out


# ── list_patterns ──────────────────────────────────────────────────────


def list_patterns(
    scope: Literal["local", "global", "all"],
    *,
    path: Path | None = None,
) -> list[IgnoreEntry]:
    """Return parsed entries from the requested ignore file(s).

    For ``scope="all"`` the local file is read first then the global,
    each preserving its in-file order.
    """

    out: list[IgnoreEntry] = []
    if scope in ("local", "all"):
        local_path = resolve_local_path(path)
        out.extend(_parse_file(local_path, source="local"))
    if scope in ("global", "all"):
        global_path = resolve_global_path()
        out.extend(_parse_file(global_path, source="global"))
    return out


# ── add_pattern ────────────────────────────────────────────────────────


def _append_below_sentinel(text: str, pattern: str) -> str:
    """Insert ``pattern`` on its own line after the managed-block close.

    If there is no managed block, append at the end of the file. The
    returned text always ends with a newline.
    """

    if not text:
        return pattern + "\n"
    if not text.endswith("\n"):
        text = text + "\n"
    lines = text.splitlines()
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if line == MANAGED_END:
            end_idx = i
            break
    if end_idx is None:
        # No managed block — append at the very bottom.
        return text + pattern + "\n"
    before = lines[: end_idx + 1]
    after = lines[end_idx + 1 :]
    new_lines = [*before, pattern, *after]
    return "\n".join(new_lines) + "\n"


def add_pattern(
    pattern: str,
    scope: Literal["local", "global"],
    *,
    path: Path | None = None,
) -> AddResult:
    """Insert ``pattern`` into the requested ignore file (idempotent).

    The pattern is validated via :func:`_validate_pattern_or_raise`
    before any I/O. If the pattern is already present (anywhere in the
    file, managed or user region) the call is a no-op (``added=False``).
    """

    _validate_pattern_or_raise(pattern)
    target = resolve_local_path(path) if scope == "local" else resolve_global_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_text = target.read_text(encoding="utf-8") if target.exists() else ""
    # Idempotence — match against the stripped, raw-line value.
    existing_lines = [ln.strip() for ln in existing_text.splitlines()]
    if pattern.strip() in existing_lines:
        return AddResult(path=target, pattern=pattern, added=False)
    new_text = _append_below_sentinel(existing_text, pattern.strip())
    atomic_write_text(target, new_text)
    return AddResult(path=target, pattern=pattern, added=True)


# ── remove_pattern ─────────────────────────────────────────────────────


def remove_pattern(
    pattern: str,
    scope: Literal["local", "global"],
    *,
    path: Path | None = None,
) -> RemoveResult:
    """Remove ``pattern`` from the requested ignore file (user-region only).

    Touching a line inside the managed block raises
    :class:`ManagedRegionProtected` — callers should regenerate that
    region with :func:`sync_managed` instead.
    """

    target = resolve_local_path(path) if scope == "local" else resolve_global_path()
    if not target.exists():
        return RemoveResult(path=target, pattern=pattern, removed=False)
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    needle = pattern.strip()

    # Walk lines, tracking managed-block state, to decide if the target
    # sits inside the protected region. The ``in_managed`` flag flips at
    # the sentinels themselves; sentinel lines are NOT user-visible
    # patterns so they don't match removal requests.
    matches_in_managed: list[int] = []
    matches_in_user: list[int] = []
    in_managed = False
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if ln == MANAGED_START:
            in_managed = True
            continue
        if ln == MANAGED_END:
            in_managed = False
            continue
        if stripped == needle:
            (matches_in_managed if in_managed else matches_in_user).append(i)
    if matches_in_managed and not matches_in_user:
        raise ManagedRegionProtected(pattern=pattern, path=target)
    if not matches_in_user:
        return RemoveResult(path=target, pattern=pattern, removed=False)
    new_lines = [ln for i, ln in enumerate(lines) if i not in set(matches_in_user)]
    new_text = "\n".join(new_lines)
    if text.endswith("\n"):
        new_text = new_text + "\n"
    atomic_write_text(target, new_text)
    return RemoveResult(path=target, pattern=pattern, removed=True)


# ── validate_file ──────────────────────────────────────────────────────


def validate_file(path: Path) -> ValidationResult:
    """Walk ``path`` line-by-line and try compiling each pattern.

    Stops at the first failure; reports a clean :class:`ValidationResult`
    when every non-blank, non-comment, non-sentinel line compiles. Lines
    inside the managed block are skipped — they are trusted output.
    """

    if not path.exists():
        return ValidationResult(ok=False, line=None, pattern=None, reason=f"missing file: {path}")
    text = path.read_text(encoding="utf-8")
    in_managed = False
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if line == MANAGED_START:
            in_managed = True
            continue
        if line == MANAGED_END:
            in_managed = False
            continue
        if in_managed:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            _validate_pattern_or_raise(stripped)
        except InvalidPattern as exc:
            return ValidationResult(
                ok=False,
                line=i,
                pattern=stripped,
                reason=exc.reason,
            )
    return ValidationResult(ok=True, line=None, pattern=None, reason=None)


# ── sync_managed ───────────────────────────────────────────────────────


def sync_managed(
    root: Path | None = None,
    *,
    cfg: Config | None = None,
    also_global: bool = False,
) -> SyncResult:
    """Regenerate the managed block of one or more ``.corpusignore`` files.

    Args:
        root: When supplied, the only tree to sync.
        cfg: When ``root`` is None and ``cfg`` is provided, every FS-style
            data root in ``cfg`` is synced.
        also_global: When True, also resync the global ignore file at
            :func:`resolve_global_path`.

    The features dict used for rendering is derived from ``cfg`` (when
    present) via :func:`feature_flags_from_config`; otherwise a
    conservative "everything off" preset is used so the call is safe
    even without a loaded config.
    """

    from corpus_forge.ignore_defaults import feature_flags_from_config

    if cfg is not None:
        features = feature_flags_from_config(cfg)
    else:
        features = {
            "whisper": False,
            "image_extractor": False,
            "code_enricher": False,
            "vlm": False,
        }
    written: list[Path] = []
    if root is not None:
        result = write_corpusignore(root, features)
        written.append(result.path)
    elif cfg is not None:
        for r in discover_data_roots(cfg):
            result = write_corpusignore(r, features)
            written.append(result.path)
    if also_global:
        gp = resolve_global_path()
        gp.parent.mkdir(parents=True, exist_ok=True)
        from corpus_forge.ignore_lifecycle import (
            ManagedBlockCorrupted,
            splice_managed_block,
        )

        existing = gp.read_text(encoding="utf-8") if gp.exists() else ""
        try:
            new_text = splice_managed_block(existing, render_managed_block(features))
        except ManagedBlockCorrupted:
            new_text = render_managed_block(features)
        atomic_write_text(gp, new_text)
        written.append(gp)
    return SyncResult(updated=written)


# ── init_file ──────────────────────────────────────────────────────────


_STARTER_BLOCK_TRAILER = (
    "\n"
    "# Add your own patterns below.\n"
    "# Anything after the closing managed sentinel survives `ignore sync`.\n"
)


def init_file(root: Path | None = None, *, force: bool = False) -> Path:
    """Create a starter ``.corpusignore`` under ``root``.

    The starter contains:
      * The Wave 1 managed block (always-on preamble) rendered with
        all heavy extractors OFF (most conservative defaults).
      * A short comment trailer telling the user where to add patterns.

    Raises :class:`FileExistsError` when the target already exists,
    unless ``force=True``.
    """

    if root is None:
        root = Path.cwd()
    target = resolve_local_path(root)
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists — pass --force to overwrite.")
    features = {
        "whisper": False,
        "image_extractor": False,
        "code_enricher": False,
        "vlm": False,
    }
    text = render_managed_block(features) + _STARTER_BLOCK_TRAILER
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, text)
    return target


# ── editor (reuses corpus_forge.admin.config._resolve_editor) ──────────


def _resolve_editor() -> list[str] | None:
    """Return argv for ``$VISUAL`` / ``$EDITOR`` / platform fallback.

    Mirrors :func:`corpus_forge.admin.config._resolve_editor` exactly —
    duplicated here so we can monkeypatch ``admin.ignore._resolve_editor``
    in tests without affecting the config verbs.
    """

    for env_var in ("VISUAL", "EDITOR"):
        value = os.environ.get(env_var)
        if value:
            parts = value.split()
            if parts and shutil.which(parts[0]):
                return parts
    candidates = ["notepad.exe"] if sys.platform.startswith("win") else ["vim", "vi", "nano"]
    for cand in candidates:
        which = shutil.which(cand)
        if which:
            return [which]
    return None


def edit_file(
    scope: Literal["local", "global"],
    *,
    path: Path | None = None,
) -> int:
    """Open ``$EDITOR`` on the requested ignore file; validate on save.

    Returns the editor's exit code on success; returns 1 (and restores
    the file from a sibling ``.tmp`` backup) when the saved buffer fails
    :func:`validate_file`.
    """

    target = resolve_local_path(path) if scope == "local" else resolve_global_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Seed empty so the editor has something to open.
        atomic_write_text(target, "")
    editor = _resolve_editor()
    if editor is None:
        ui_error("Could not find an editor — set $EDITOR (or $VISUAL) to your preferred editor.")
        return 1
    backup = target.with_suffix(target.suffix + ".bak")
    shutil.copyfile(target, backup)
    rc = subprocess.call([*editor, str(target)])
    if rc != 0:
        backup.unlink(missing_ok=True)
        return rc
    validation = validate_file(target)
    if not validation.ok:
        ui_error(
            f"Saved file is invalid (line {validation.line}: {validation.reason}) — rolling back."
        )
        shutil.copyfile(backup, target)
        backup.unlink(missing_ok=True)
        return 1
    backup.unlink(missing_ok=True)
    return 0


# ── confirm-scope helper (TTY vs agent mode) ───────────────────────────


_CMD_NAME = "ignore"


def _resolve_scope_interactive(
    *,
    cmd: str,
    local_flag: bool,
    global_flag: bool,
    pattern: str,
) -> Literal["local", "global"]:
    """Resolve which scope to operate on when neither flag is set.

    Under agent mode we refuse to prompt: emit a structured ``error``
    JSONL event with ``kind="ambiguous_scope"`` and raise
    ``typer.Exit(code=2)``.
    """

    if local_flag and global_flag:
        ui_error("--local and --global are mutually exclusive.")
        raise typer.Exit(code=2)
    if local_flag:
        return "local"
    if global_flag:
        return "global"
    # Neither flag — need to disambiguate.
    if ui_agent.is_agent_mode():
        ui_agent.emit(
            "error",
            cmd=f"{_CMD_NAME} {cmd}",
            kind="ambiguous_scope",
            msg=(f"`ignore {cmd} {pattern!r}` requires --local or --global under agent mode."),
        )
        raise typer.Exit(code=2)
    from corpus_forge.ui.prompts import Prompt

    answer = Prompt.ask(
        f"Apply to which scope? (local/global/cancel) [{pattern}]",
        choices=["local", "global", "cancel"],
        default="local",
    )
    if answer == "cancel":
        raise typer.Exit(code=2)
    return answer  # type: ignore[return-value]


# ── Typer verbs ────────────────────────────────────────────────────────


def _emit_entry(entry: IgnoreEntry) -> None:
    """Print one entry with provenance prefix."""

    prefix = f"[{entry.source}:managed]" if entry.managed else f"[{entry.source}]"
    print(f"{prefix} {entry.pattern}")


@ignore_app.command("list")
def cmd_list(
    local: Annotated[  # noqa: ARG001 — surfaced for --help; `local` is the default scope
        bool, typer.Option("--local", help="List patterns from the local file.")
    ] = False,
    global_: Annotated[
        bool, typer.Option("--global", help="List patterns from the global file.")
    ] = False,
    all_: Annotated[
        bool, typer.Option("--all", help="List patterns from both local and global.")
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Project root containing .corpusignore (defaults to cwd).",
        ),
    ] = None,
) -> None:
    """Print every pattern in the requested ignore file(s)."""

    if all_:
        scope: Literal["local", "global", "all"] = "all"
    elif global_:
        scope = "global"
    else:
        scope = "local"
    entries = list_patterns(scope, path=path)
    for e in entries:
        _emit_entry(e)


@ignore_app.command("add")
def cmd_add(
    pattern: Annotated[str, typer.Argument(help="Pattern to insert.")],
    local: Annotated[
        bool, typer.Option("--local", help="Operate on the local .corpusignore.")
    ] = False,
    global_: Annotated[
        bool, typer.Option("--global", help="Operate on the global ignore file.")
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Project root containing .corpusignore (defaults to cwd).",
        ),
    ] = None,
) -> None:
    """Append ``pattern`` to the chosen ignore file (idempotent)."""

    scope = _resolve_scope_interactive(
        cmd="add", local_flag=local, global_flag=global_, pattern=pattern
    )
    try:
        result = add_pattern(pattern, scope=scope, path=path)
    except InvalidPattern as exc:
        ui_error(str(exc))
        raise typer.Exit(code=1) from exc
    if result.added:
        ui_ok(f"Added {pattern!r} to {result.path}")
    else:
        ui_info(f"{pattern!r} already present in {result.path} — no-op")


@ignore_app.command("remove")
def cmd_remove(
    pattern: Annotated[str, typer.Argument(help="Pattern to remove.")],
    local: Annotated[
        bool, typer.Option("--local", help="Operate on the local .corpusignore.")
    ] = False,
    global_: Annotated[
        bool, typer.Option("--global", help="Operate on the global ignore file.")
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Project root containing .corpusignore (defaults to cwd).",
        ),
    ] = None,
) -> None:
    """Remove ``pattern`` from the chosen ignore file (user region only)."""

    scope = _resolve_scope_interactive(
        cmd="remove", local_flag=local, global_flag=global_, pattern=pattern
    )
    try:
        result = remove_pattern(pattern, scope=scope, path=path)
    except ManagedRegionProtected as exc:
        ui_error(
            f"managed_block_protected: {pattern!r} sits in the managed block "
            f"of {exc.path}. Use `corpus-forge ignore sync` to regenerate."
        )
        raise typer.Exit(code=3) from exc
    if result.removed:
        ui_ok(f"Removed {pattern!r} from {result.path}")
    else:
        ui_info(f"{pattern!r} not present in {result.path} — no-op")


@ignore_app.command("edit")
def cmd_edit(
    local: Annotated[bool, typer.Option("--local", help="Edit the local .corpusignore.")] = False,
    global_: Annotated[bool, typer.Option("--global", help="Edit the global ignore file.")] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Project root containing .corpusignore (defaults to cwd).",
        ),
    ] = None,
) -> None:
    """Open ``$EDITOR`` on the chosen file; validate + rollback on save."""

    if local and global_:
        ui_error("--local and --global are mutually exclusive.")
        raise typer.Exit(code=2)
    scope: Literal["local", "global"] = "global" if global_ else "local"
    rc = edit_file(scope, path=path)
    raise typer.Exit(code=rc)


@ignore_app.command("validate")
def cmd_validate(
    path: Annotated[Path, typer.Argument(help="Path to the ignore file to validate.")],
) -> None:
    """Validate every pattern in ``path``; exit 1 on first failure."""

    result = validate_file(path)
    if result.ok:
        ui_ok(f"{path} validated — every pattern compiles cleanly.")
        return
    ui_error(f"Invalid ignore pattern at line {result.line}: {result.pattern!r} ({result.reason})")
    raise typer.Exit(code=1)


@ignore_app.command("sync")
def cmd_sync(
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Tree whose .corpusignore to resync."),
    ] = None,
    also_global: Annotated[
        bool,
        typer.Option("--also-global", help="Also resync the global ignore file."),
    ] = False,
) -> None:
    """Regenerate the managed block(s); preserve user lines."""

    # Best-effort config load — sync is callable from places where no
    # config exists yet (e.g. first-run), so we fall back to the
    # conservative defaults preset.
    cfg: Config | None = None
    try:
        from corpus_forge.config import Config as _Config

        cfg = _Config.load()
    except Exception:  # pragma: no cover — defensive
        cfg = None
    result = sync_managed(root=root, cfg=cfg, also_global=also_global)
    if not result.updated:
        ui_warn("No FS-style data roots found in config — nothing to sync.")
        return
    for p in result.updated:
        ui_ok(f"Synced {p}")


@ignore_app.command("init")
def cmd_init(
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Directory under which to create the .corpusignore."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Create a starter ``.corpusignore`` under ``--root``."""

    try:
        target = init_file(root=root, force=force)
    except FileExistsError as exc:
        ui_error(str(exc))
        raise typer.Exit(code=3) from exc
    ui_ok(f"Created {target}")


# ── tempfile helper (unused; reserved for future hardening) ────────────
#
# A future improvement could move add/remove away from
# atomic_write_text in favour of an explicit ``tempfile.NamedTemporaryFile``
# + ``Path.replace`` pair so a mid-write crash is even easier to detect
# from outside. For now atomic_write_text already gives us the contract
# the test suite asserts.

_ = tempfile  # silence "imported but unused" — kept for forward compat


__all__ = [
    "AddResult",
    "IgnoreEntry",
    "InvalidPattern",
    "ManagedRegionProtected",
    "RemoveResult",
    "SyncResult",
    "ValidationResult",
    "add_pattern",
    "cmd_add",
    "cmd_edit",
    "cmd_init",
    "cmd_list",
    "cmd_remove",
    "cmd_sync",
    "cmd_validate",
    "edit_file",
    "ignore_app",
    "init_file",
    "list_patterns",
    "remove_pattern",
    "resolve_global_path",
    "resolve_local_path",
    "sync_managed",
    "validate_file",
]
