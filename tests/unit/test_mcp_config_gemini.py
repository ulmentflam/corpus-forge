"""I2 — Gemini CLI MCP config drop-in (`examples/mcp-config/gemini-cli.mcp.json`).

Pins the shape of the JSON snippet users merge into ``~/.gemini/settings.json``
to wire corpus-forge into the Gemini CLI.  Mirrors the Claude Code config
rot-detector in ``test_mcp_config_examples.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GEMINI_MCP_JSON = REPO_ROOT / "examples" / "mcp-config" / "gemini-cli.mcp.json"


# ── File exists ───────────────────────────────────────────────────────────


def test_gemini_mcp_config_exists() -> None:
    """Gemini CLI drop-in config lives at the documented path."""
    assert GEMINI_MCP_JSON.is_file(), f"missing {GEMINI_MCP_JSON}"


# ── JSON parses ──────────────────────────────────────────────────────────


def test_gemini_mcp_config_parses_as_json() -> None:
    """Drop-in JSON must round-trip through ``json.loads`` cleanly."""
    data = json.loads(GEMINI_MCP_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ── mcpServers.corpus-forge launch contract ──────────────────────────────


def _server_entry() -> dict:
    data = json.loads(GEMINI_MCP_JSON.read_text(encoding="utf-8"))
    assert "mcpServers" in data, "gemini-cli.mcp.json missing top-level 'mcpServers'"
    servers = data["mcpServers"]
    assert "corpus-forge" in servers, (
        f"gemini-cli.mcp.json must declare a 'corpus-forge' server (got keys={list(servers)})"
    )
    entry = servers["corpus-forge"]
    assert isinstance(entry, dict), "gemini-cli.mcp.json 'corpus-forge' entry not an object"
    return entry


def test_gemini_mcp_uses_corpus_forge_command() -> None:
    """Gemini CLI drop-in launches the published ``corpus-forge`` console script."""
    entry = _server_entry()
    assert entry.get("command") == "corpus-forge", (
        f"expected command=='corpus-forge'; got {entry.get('command')!r}"
    )


def test_gemini_mcp_args_contain_mcp_serve() -> None:
    """``args`` must invoke the stdio MCP server (R5 launch contract)."""
    entry = _server_entry()
    args = entry.get("args")
    assert isinstance(args, list), f"args not a list; got {args!r}"
    assert args[:2] == ["mcp", "serve"], (
        f"expected args to start with ['mcp', 'serve']; got {args!r}"
    )


def test_gemini_mcp_declares_corpus_forge_config_env() -> None:
    """``env.CORPUS_FORGE_CONFIG`` must be present so the user knows the knob exists."""
    entry = _server_entry()
    env = entry.get("env")
    assert isinstance(env, dict), f"env block missing or not an object; got {env!r}"
    val = env.get("CORPUS_FORGE_CONFIG")
    assert isinstance(val, str) and val, (
        f"CORPUS_FORGE_CONFIG must be a non-empty string; got {val!r}"
    )
