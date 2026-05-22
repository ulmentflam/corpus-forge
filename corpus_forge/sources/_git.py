"""Resolve git provenance (commit SHA + branch) for a path.

Used by source plugins (`FilesystemSource`, `ClaudeCodeSource`) at scan
time so chunks can carry the captured commit/branch and downstream
features — self-distillation feedback, live source navigation — can
attach signal to the file *at that commit* rather than the current
HEAD.

Single public entry point: :func:`git_context`. Returns
``(commit, branch)`` for a path, or ``(None, None)`` when the path
isn't inside a git work tree, `git` isn't on PATH, or any subprocess
call fails. Failures are never raised — callers want this to be a
best-effort enrichment, not a hard dependency.

Backs the second task of RFC ``rfc-source-provenance-git-and-lines``
(P0). The rest of that RFC — wiring this into source plugins,
extending chunkers, schema migration, MCP tool — comes in subsequent
PRs that build on this helper.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Short timeout — this is a per-scan call, never per-file. A hung
# subprocess shouldn't block ingest.
_GIT_TIMEOUT_SECONDS = 2


def git_context(path: Path | str) -> tuple[str | None, str | None]:
    """Return ``(commit_sha, branch)`` for the git work tree containing *path*.

    The path itself does not need to exist on disk — only its enclosing
    directory matters to git. If *path* is a file, the surrounding
    directory is used as the subprocess CWD. If *path* is a directory,
    the directory itself.

    Returns ``(None, None)`` (and never raises) when:

    - The path is not inside a git work tree.
    - The path's enclosing directory does not exist on disk.
    - ``git`` is not on PATH (``FileNotFoundError`` from ``subprocess``).
    - Any ``git`` subprocess times out or fails for any other reason.

    Detached-HEAD state is handled by mapping ``HEAD`` (what
    ``git rev-parse --abbrev-ref HEAD`` returns when detached) to
    ``None`` for the branch — the commit SHA is still returned because
    that's the actually-useful provenance bit; "detached HEAD" is not
    a branch.
    """
    p = Path(path).expanduser()
    cwd = p if p.is_dir() else p.parent
    if not cwd.is_dir():
        return (None, None)

    if not _is_inside_work_tree(cwd):
        return (None, None)

    commit = _run_git(cwd, "rev-parse", "HEAD")
    branch = _run_git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    # `git rev-parse --abbrev-ref HEAD` returns the literal string "HEAD"
    # when the work tree is in detached-HEAD state. That's not a branch
    # name — surface it as None so callers don't pin "HEAD" as the
    # captured branch.
    if branch == "HEAD":
        branch = None
    return (commit, branch)


def _is_inside_work_tree(cwd: Path) -> bool:
    """Cheap probe — `git rev-parse --is-inside-work-tree` returns 'true'."""
    out = _run_git(cwd, "rev-parse", "--is-inside-work-tree")
    return out == "true"


def _run_git(cwd: Path, *args: str) -> str | None:
    """Run ``git <args>`` in *cwd*; return stripped stdout or None on failure.

    Catches the full set of subprocess failure modes — missing binary,
    timeout, non-zero exit, malformed output — and maps them all to
    ``None`` so callers don't need to wrap individual calls in try
    blocks.
    """
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
