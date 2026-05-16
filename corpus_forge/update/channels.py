"""Install-channel detection + dispatch for ``corpus-forge update``.

Six install channels are detected by inspecting ``sys.executable`` +
env hints. Each maps to a single shell command the update subcommand
delegates to, so users learn one command (``corpus-forge update``)
regardless of how they installed.

Channel detection is best-effort: the user can always pin the channel
explicitly with ``corpus-forge update --channel <name>`` if auto-
detection misses (the dispatcher honours the override). Unknown
channels fall through to ``pip`` since that's the lowest-common-
denominator installer.

Cross-channel order of preference (when multiple matches are
plausible — e.g. uv tool installs also leave a pip-style entry in the
PATH): ``source > docker > uv-tool > pipx > brew > pip``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Order matters: probes run top-to-bottom; the first match wins.
Channel = Literal["source", "docker", "uv-tool", "pipx", "brew", "pip"]
_CHANNELS: tuple[Channel, ...] = ("source", "docker", "uv-tool", "pipx", "brew", "pip")


@dataclass(frozen=True)
class UpgradeResult:
    """Outcome of a single ``run_update`` invocation."""

    channel: Channel
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


def detect_channel(*, executable: str | None = None, env: dict[str, str] | None = None) -> Channel:
    """Best-effort install-channel detection.

    Args:
        executable: ``sys.executable``-equivalent override (test hook).
        env: ``os.environ``-equivalent override (test hook).

    Returns the detected :data:`Channel`. Falls back to ``"pip"`` on
    no-clear-signal — the safest delegated upgrade command across
    every Linux distro / macOS / Windows install layout.
    """
    exe = Path(executable or sys.executable).resolve()
    e = env if env is not None else os.environ

    # 1. ``source``: ``git rev-parse --is-inside-work-tree`` succeeds
    #    from the venv's parent. Only flagged when the venv lives
    #    INSIDE a git checkout (typical for ``setup-corpus-forge.sh`` /
    #    ``uv sync``). Cheap subprocess; fail-closed on any error.
    venv_dir = exe.parent.parent  # .venv/bin/python → .venv → repo
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=venv_dir.parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return "source"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # git not installed / hung — fall through.
        pass

    # 2. ``docker``: container env vars set by Docker / Podman, plus the
    #    ``/.dockerenv`` filesystem sentinel.
    if (
        e.get("DOCKER_CONTAINER")
        or e.get("container") == "docker"
        or Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
    ):
        return "docker"

    # 3. ``uv-tool``: ``uv tool install`` puts the venv under
    #    ``$XDG_DATA_HOME/uv/tools/<pkg>/`` (or ``~/.local/share/uv``).
    exe_str = str(exe)
    if "/uv/tools/" in exe_str or "\\uv\\tools\\" in exe_str:
        return "uv-tool"

    # 4. ``pipx``: standard layout is ``~/.local/pipx/venvs/<pkg>/``.
    if "/pipx/venvs/" in exe_str or "\\pipx\\venvs\\" in exe_str:
        return "pipx"

    # 5. ``brew``: Homebrew installs land under Cellar/.
    if "/Cellar/" in exe_str:
        return "brew"

    # 6. ``pip``: everything else.
    return "pip"


def _upgrade_command(channel: Channel) -> tuple[str, ...]:
    """The shell command ``run_update`` will exec for ``channel``."""
    match channel:
        case "uv-tool":
            return ("uv", "tool", "upgrade", "corpus-forge")
        case "pipx":
            return ("pipx", "upgrade", "corpus-forge")
        case "brew":
            return ("brew", "upgrade", "corpus-forge")
        case "docker":
            # We can't self-upgrade a running image; print the command
            # the user needs to run on the host.
            return ("docker", "pull", "ghcr.io/ulmentflam/corpus-forge:latest")
        case "source":
            # ``git pull`` + ``uv sync`` keeps the contributor install
            # current. Tests on a git mirror; the user's branch may need
            # rebasing manually.
            return ("git", "pull", "--ff-only")
        case "pip":
            return (sys.executable, "-m", "pip", "install", "-U", "corpus-forge")


def run_update(
    *,
    channel: Channel | None = None,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
) -> UpgradeResult:
    """Detect the install channel (or use ``channel``) and run the upgrade.

    Args:
        channel: Force a specific channel; bypasses :func:`detect_channel`.
        dry_run: Don't run; just return the command we would have run.
        env: ``os.environ``-equivalent override (test hook).

    Note: this function intentionally does NOT chain ``corpus-forge
    migrate`` + ``doctor`` — the Typer subcommand in :mod:`corpus_forge.cli`
    does that orchestration. Keeping :func:`run_update` pure makes
    channel-detection + dispatch testable without spawning side
    effects.
    """
    if channel is None:
        channel = detect_channel(env=env)
    cmd = _upgrade_command(channel)

    if dry_run:
        return UpgradeResult(
            channel=channel,
            command=cmd,
            returncode=0,
            stdout=f"(dry run) would exec: {' '.join(cmd)}",
            stderr="",
        )

    if channel not in _CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}; expected one of {_CHANNELS}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        # Channel-required binary not on PATH (e.g. ``brew`` on a non-
        # Homebrew host). Report cleanly instead of crashing.
        return UpgradeResult(
            channel=channel,
            command=cmd,
            returncode=127,
            stdout="",
            stderr=f"{exc}",
        )
    return UpgradeResult(
        channel=channel,
        command=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
