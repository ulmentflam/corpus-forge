"""I1 — Drop-in MCP config for OpenCode (`examples/mcp-config/opencode-client.mcp.json`).

Pins the shape of the JSON artefact that users copy-paste to wire
``corpus-forge`` into OpenCode.

The wire format is the well-known ``mcpServers.<name> = { command, args, env }``
shape used by MCP clients today. Tests fail loudly if the example drifts
away from the ``corpus-forge mcp serve`` launch contract.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "mcp-config"
OPENCODE_JSON = EXAMPLES_DIR / "opencode-client.mcp.json"


# ── File exists ───────────────────────────────────────────────────────────


def test_opencode_config_exists() -> None:
    """OpenCode drop-in config lives at the documented path."""
    assert OPENCODE_JSON.is_file(), f"missing {OPENCODE_JSON}"


# ── JSON parses ──────────────────────────────────────────────────────────


def test_opencode_config_parses_as_json() -> None:
    """Drop-in JSON must round-trip through ``json.loads`` cleanly."""
    data = json.loads(OPENCODE_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ── mcpServers.corpus-forge launch contract ──────────────────────────────


def _server_entry() -> dict:
    data = json.loads(OPENCODE_JSON.read_text(encoding="utf-8"))
    assert "mcpServers" in data, f"{OPENCODE_JSON.name} missing top-level 'mcpServers'"
    servers = data["mcpServers"]
    assert "corpus-forge" in servers, (
        f"{OPENCODE_JSON.name} must declare a 'corpus-forge' server (got keys={list(servers)})"
    )
    entry = servers["corpus-forge"]
    assert isinstance(entry, dict), f"{OPENCODE_JSON.name} 'corpus-forge' entry not an object"
    return entry


def test_opencode_uses_corpus_forge_command() -> None:
    """OpenCode drop-in launches the published ``corpus-forge`` console script."""
    entry = _server_entry()
    assert entry.get("command") == "corpus-forge", (
        f"expected command=='corpus-forge'; got {entry.get('command')!r}"
    )


def test_opencode_args_contain_mcp_serve() -> None:
    """``args`` must invoke the stdio MCP server (launch contract)."""
    entry = _server_entry()
    args = entry.get("args")
    assert isinstance(args, list), f"args not a list; got {args!r}"
    assert args[:2] == ["mcp", "serve"], (
        f"expected args to start with ['mcp', 'serve']; got {args!r}"
    )


def test_opencode_declares_corpus_forge_config_env() -> None:
    """``env.CORPUS_FORGE_CONFIG`` must be present so the user knows the knob exists."""
    entry = _server_entry()
    env = entry.get("env")
    assert isinstance(env, dict), f"env block missing or not an object; got {env!r}"
    val = env.get("CORPUS_FORGE_CONFIG")
    assert isinstance(val, str) and val, (
        f"CORPUS_FORGE_CONFIG must be a non-empty string; got {val!r}"
    )
