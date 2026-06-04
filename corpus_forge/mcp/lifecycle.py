"""Lifecycle helpers for the MCP server: discovery + restart.

The MCP server is normally launched by a client process (Claude Code,
Claude Desktop, Anthropic SDK Managed Agent, …) over stdio.  Once
running, the server child holds whatever wheel was current at spawn
time — so a ``uv tool install --force`` of a fixed wheel doesn't
reach the client's already-running child.  These helpers let
``corpus-forge mcp restart`` SIGTERM the orphaned children so the
client respawns them under the new wheel.

Two surfaces:

* :func:`discover_mcp_servers` — scrape the OS process table for
  ``corpus-forge mcp serve`` invocations.  Returns lightweight
  dataclasses with the pid, argv, and executable path; doesn't open
  the process for any kind of introspection.
* :func:`restart_mcp_servers` — SIGTERM every discovered server.
  Catches ``ProcessLookupError`` because the discovery → kill window
  can race (the client may have torn it down between calls).
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = [
    "MCPServerProcess",
    "RestartResult",
    "discover_mcp_servers",
    "restart_mcp_servers",
]

# Minimum argv length required to recognise a ``corpus-forge mcp serve``
# invocation — three tokens (``corpus-forge``, ``mcp``, ``serve``).
_MCP_SERVE_MIN_ARGV = 3


@dataclass(frozen=True)
class MCPServerProcess:
    """Snapshot of a running ``corpus-forge mcp serve`` process."""

    pid: int
    argv: list[str]
    executable_path: str

    @property
    def writes_disabled(self) -> bool:
        """True iff ``--no-writes`` (or its hidden equivalent) is in argv."""
        return "--no-writes" in self.argv


@dataclass(frozen=True)
class RestartResult:
    """Outcome of a ``restart_mcp_servers`` call."""

    signalled_pids: list[int] = field(default_factory=list)
    already_dead: list[int] = field(default_factory=list)


def _iter_processes() -> Iterator[MCPServerProcess]:
    """Yield every visible process as an ``MCPServerProcess``.

    Uses ``ps -eo pid,comm,args`` so we don't need ``psutil`` as a
    dependency.  Tests patch this directly to inject synthetic
    process tables — keeping the real shellout off the test critical
    path.
    """
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return
    if completed.returncode != 0:
        return
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            pid_str, rest = line.split(None, 1)
            pid = int(pid_str)
        except ValueError:
            continue
        argv = rest.split()
        if not argv:
            continue
        yield MCPServerProcess(pid=pid, argv=argv, executable_path=argv[0])


def discover_mcp_servers() -> Iterator[MCPServerProcess]:
    """Yield every running ``corpus-forge mcp serve`` process.

    Matches on the argv shape (``[*"corpus-forge", "mcp", "serve", *_]``)
    rather than on the executable path so this works regardless of
    install method (``uv tool``, ``pip install --user``, Homebrew tap,
    …).  Skips our own ``corpus-forge mcp restart`` invocation by
    rejecting any argv whose third token is not ``serve``.
    """
    for proc in _iter_processes():
        # Strip the path so a brew-installed ``/opt/homebrew/bin/corpus-forge``
        # and a uv-installed ``~/.local/bin/corpus-forge`` both match the
        # same trailing pattern.
        argv = proc.argv
        if len(argv) < _MCP_SERVE_MIN_ARGV:
            continue
        if not argv[0].endswith("corpus-forge"):
            continue
        if argv[1] != "mcp" or argv[2] != "serve":
            continue
        yield proc


def restart_mcp_servers() -> RestartResult:
    """SIGTERM every discovered ``corpus-forge mcp serve`` process.

    The MCP *client* (Claude Code / Desktop / Anthropic SDK Managed
    Agent) re-spawns the server on the next stdio request, which
    picks up the latest installed wheel automatically — that's how
    the hotfix reaches the operator without restarting the client
    itself.

    Catches ``ProcessLookupError`` because the discovery → kill
    window races against the client's own teardown loop.  A pid
    that's gone is the goal state anyway, so we count it as
    ``already_dead`` rather than an error.
    """
    signalled: list[int] = []
    already_dead: list[int] = []
    for proc in discover_mcp_servers():
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            already_dead.append(proc.pid)
            continue
        signalled.append(proc.pid)
    return RestartResult(signalled_pids=signalled, already_dead=already_dead)
