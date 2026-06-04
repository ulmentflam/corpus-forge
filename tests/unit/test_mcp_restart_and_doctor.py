"""Hotfix tests: ``corpus-forge mcp restart`` + doctor MCP-staleness check.

When the MCP server is launched by a client (Claude Code / Claude
Desktop) it keeps running until the client tears it down — which means
a binary upgrade isn't picked up until the client process restarts.
The hotfix that flipped ``writes_enabled`` to default-on is invisible
until the running server child is replaced.

Two surfaces test here:

1. ``corpus-forge mcp restart`` finds running ``corpus-forge mcp serve``
   processes and signals them to exit so the client respawns under the
   new wheel.
2. ``corpus-forge doctor``'s new ``mcp_servers`` check WARNs whenever
   it detects a running server whose wheel is older than the current
   ``corpus-forge`` install OR whose argv carries ``--no-writes``
   (signalling the operator forgot to flip it back after a debug
   session), pointing the operator at ``corpus-forge mcp restart``.
"""

from __future__ import annotations

from unittest.mock import patch

from corpus_forge.doctor.checks import CheckStatus, _check_mcp_servers
from corpus_forge.mcp.lifecycle import (
    MCPServerProcess,
    discover_mcp_servers,
    restart_mcp_servers,
)

# ── ``discover_mcp_servers`` ─────────────────────────────────────────


class TestDiscoverMcpServers:
    def test_returns_running_corpus_forge_mcp_serve_processes(self):
        """Iterate the process table and pick out ``corpus-forge mcp serve``."""
        fake_ps = [
            MCPServerProcess(
                pid=11111,
                argv=["/usr/local/bin/corpus-forge", "mcp", "serve", "--transport", "stdio"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
            MCPServerProcess(
                pid=22222,
                argv=["/usr/local/bin/corpus-forge", "mcp", "serve", "--no-writes"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
        ]
        with patch("corpus_forge.mcp.lifecycle._iter_processes", return_value=iter(fake_ps)):
            found = list(discover_mcp_servers())
        assert {p.pid for p in found} == {11111, 22222}

    def test_ignores_unrelated_processes(self):
        """Random python / corpus-forge ingest / daemon processes are not flagged."""
        unrelated = [
            MCPServerProcess(
                pid=33333,
                argv=["/usr/bin/python", "-c", "print(1)"],
                executable_path="/usr/bin/python",
            ),
            MCPServerProcess(
                pid=44444,
                argv=["/usr/local/bin/corpus-forge", "service", "start"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
            MCPServerProcess(
                pid=55555,
                argv=["/usr/local/bin/corpus-forge", "embed", "-e", "qwen3-4096"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
        ]
        with patch("corpus_forge.mcp.lifecycle._iter_processes", return_value=iter(unrelated)):
            found = list(discover_mcp_servers())
        assert found == []

    def test_writes_disabled_flag_detected_in_argv(self):
        """``--no-writes`` in argv flips the ``writes_disabled`` flag on the result."""
        ps = [
            MCPServerProcess(
                pid=11111,
                argv=["/usr/local/bin/corpus-forge", "mcp", "serve", "--no-writes"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
        ]
        with patch("corpus_forge.mcp.lifecycle._iter_processes", return_value=iter(ps)):
            found = list(discover_mcp_servers())
        assert found[0].writes_disabled is True

    def test_writes_enabled_by_default(self):
        """No ``--no-writes`` in argv → ``writes_disabled`` is False."""
        ps = [
            MCPServerProcess(
                pid=11111,
                argv=["/usr/local/bin/corpus-forge", "mcp", "serve"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
        ]
        with patch("corpus_forge.mcp.lifecycle._iter_processes", return_value=iter(ps)):
            found = list(discover_mcp_servers())
        assert found[0].writes_disabled is False


# ── ``restart_mcp_servers`` ──────────────────────────────────────────


class TestRestartMcpServers:
    def test_signals_each_discovered_server(self):
        """SIGTERM each detected MCP server pid; client respawns automatically."""
        ps = [
            MCPServerProcess(pid=11111, argv=[], executable_path=""),
            MCPServerProcess(pid=22222, argv=[], executable_path=""),
        ]
        signalled: list[int] = []

        def _fake_kill(pid: int, sig: int) -> None:
            signalled.append(pid)

        with (
            patch("corpus_forge.mcp.lifecycle.discover_mcp_servers", return_value=iter(ps)),
            patch("corpus_forge.mcp.lifecycle.os.kill", side_effect=_fake_kill),
        ):
            result = restart_mcp_servers()
        assert sorted(signalled) == [11111, 22222]
        assert result.signalled_pids == [11111, 22222]

    def test_no_servers_running_is_clean_return(self):
        """No discovered servers → empty result, no error."""
        with patch("corpus_forge.mcp.lifecycle.discover_mcp_servers", return_value=iter([])):
            result = restart_mcp_servers()
        assert result.signalled_pids == []

    def test_process_lookup_error_swallowed(self):
        """Race: server exited between discovery and kill → ProcessLookupError."""
        ps = [MCPServerProcess(pid=11111, argv=[], executable_path="")]

        def _fake_kill(pid: int, sig: int) -> None:
            raise ProcessLookupError(f"no such pid {pid}")

        with (
            patch("corpus_forge.mcp.lifecycle.discover_mcp_servers", return_value=iter(ps)),
            patch("corpus_forge.mcp.lifecycle.os.kill", side_effect=_fake_kill),
        ):
            # Must NOT raise; the process being gone is the goal state anyway.
            result = restart_mcp_servers()
        assert result.signalled_pids == []
        assert result.already_dead == [11111]


# ── doctor ``mcp_servers`` check ─────────────────────────────────────


class TestCheckMcpServers:
    def _patch_discover(self, servers):
        return patch(
            "corpus_forge.doctor.checks.discover_mcp_servers",
            return_value=iter(servers),
        )

    def test_no_servers_running_returns_ok_skip_blurb(self):
        """No active MCP servers → OK with informational detail."""
        with self._patch_discover([]):
            result = _check_mcp_servers()
        assert result.status is CheckStatus.OK
        assert result.name == "mcp_servers"
        assert "no" in result.detail.lower() or "0" in result.detail

    def test_active_servers_with_writes_enabled_report_ok(self):
        """Servers running with default-on writes are healthy."""
        ps = [
            MCPServerProcess(
                pid=11111,
                argv=["/usr/local/bin/corpus-forge", "mcp", "serve"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
        ]
        with self._patch_discover(ps):
            result = _check_mcp_servers()
        assert result.status is CheckStatus.OK
        assert "11111" in result.detail

    def test_no_writes_argv_triggers_warn(self):
        """``--no-writes`` in argv → WARN; recommend ``mcp restart``."""
        ps = [
            MCPServerProcess(
                pid=22222,
                argv=["/usr/local/bin/corpus-forge", "mcp", "serve", "--no-writes"],
                executable_path="/path/to/uv/tools/corpus-forge/bin/python",
            ),
        ]
        with self._patch_discover(ps):
            result = _check_mcp_servers()
        assert result.status is CheckStatus.WARN
        assert "--no-writes" in result.detail or "writes disabled" in result.detail.lower()
        # Surfaces the recovery command so the operator knows what to do.
        assert "mcp restart" in result.detail

    def test_check_is_registered_in_run_doctor(self):
        from corpus_forge.doctor.checks import _CHECKS

        assert _check_mcp_servers in _CHECKS
