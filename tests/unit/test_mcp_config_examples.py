"""CS-01 — Drop-in MCP config examples (Claude Code + Claude Desktop).

These tests pin the shape of the three artefacts that users copy-paste to
wire ``corpus-forge`` into Claude Code / Claude Desktop:

- ``examples/mcp-config/claude-code.mcp.json``
- ``examples/mcp-config/claude-desktop.json``
- ``examples/mcp-config/README.md`` (install steps for both surfaces)

The wire format is the well-known ``mcpServers.<name> = { command, args, env }``
shape used by the Anthropic clients today (mirrors `mcp.json` semantics from
the MCP spec).  Tests fail loudly if the example drifts away from the
``corpus-forge mcp serve`` launch contract pinned in R5.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "mcp-config"
CLAUDE_CODE_JSON = EXAMPLES_DIR / "claude-code.mcp.json"
CLAUDE_DESKTOP_JSON = EXAMPLES_DIR / "claude-desktop.json"
EXAMPLES_README = EXAMPLES_DIR / "README.md"


# ── Files exist ──────────────────────────────────────────────────────────


def test_claude_code_config_exists() -> None:
    """Claude Code drop-in config lives at the documented path."""
    assert CLAUDE_CODE_JSON.is_file(), f"missing {CLAUDE_CODE_JSON}"


def test_claude_desktop_config_exists() -> None:
    """Claude Desktop drop-in config lives at the documented path."""
    assert CLAUDE_DESKTOP_JSON.is_file(), f"missing {CLAUDE_DESKTOP_JSON}"


def test_examples_readme_exists() -> None:
    """Install instructions for both surfaces sit next to the JSON files."""
    assert EXAMPLES_README.is_file(), f"missing {EXAMPLES_README}"


# ── JSON parses ──────────────────────────────────────────────────────────


def test_claude_code_config_parses_as_json() -> None:
    """Drop-in JSON must round-trip through ``json.loads`` cleanly."""
    data = json.loads(CLAUDE_CODE_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_claude_desktop_config_parses_as_json() -> None:
    """Drop-in JSON must round-trip through ``json.loads`` cleanly."""
    data = json.loads(CLAUDE_DESKTOP_JSON.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ── mcpServers.corpus-forge launch contract ──────────────────────────────


def _server_entry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "mcpServers" in data, f"{path.name} missing top-level 'mcpServers'"
    servers = data["mcpServers"]
    assert "corpus-forge" in servers, (
        f"{path.name} must declare a 'corpus-forge' server (got keys={list(servers)})"
    )
    entry = servers["corpus-forge"]
    assert isinstance(entry, dict), f"{path.name} 'corpus-forge' entry not an object"
    return entry


def test_claude_code_uses_corpus_forge_command() -> None:
    """Claude Code drop-in launches the published ``corpus-forge`` console script."""
    entry = _server_entry(CLAUDE_CODE_JSON)
    assert entry.get("command") == "corpus-forge", (
        f"expected command=='corpus-forge'; got {entry.get('command')!r}"
    )


def test_claude_desktop_uses_corpus_forge_command() -> None:
    """Claude Desktop drop-in launches the published ``corpus-forge`` console script."""
    entry = _server_entry(CLAUDE_DESKTOP_JSON)
    assert entry.get("command") == "corpus-forge", (
        f"expected command=='corpus-forge'; got {entry.get('command')!r}"
    )


def test_claude_code_args_contain_mcp_serve() -> None:
    """``args`` must invoke the stdio MCP server (R5 launch contract)."""
    entry = _server_entry(CLAUDE_CODE_JSON)
    args = entry.get("args")
    assert isinstance(args, list), f"args not a list; got {args!r}"
    assert args[:2] == ["mcp", "serve"], (
        f"expected args to start with ['mcp', 'serve']; got {args!r}"
    )


def test_claude_desktop_args_contain_mcp_serve() -> None:
    """``args`` must invoke the stdio MCP server (R5 launch contract)."""
    entry = _server_entry(CLAUDE_DESKTOP_JSON)
    args = entry.get("args")
    assert isinstance(args, list), f"args not a list; got {args!r}"
    assert args[:2] == ["mcp", "serve"], (
        f"expected args to start with ['mcp', 'serve']; got {args!r}"
    )


def test_claude_code_declares_corpus_forge_config_env() -> None:
    """``env.CORPUS_FORGE_CONFIG`` must be present so the user knows the knob exists."""
    entry = _server_entry(CLAUDE_CODE_JSON)
    env = entry.get("env")
    assert isinstance(env, dict), f"env block missing or not an object; got {env!r}"
    val = env.get("CORPUS_FORGE_CONFIG")
    assert isinstance(val, str) and val, (
        f"CORPUS_FORGE_CONFIG must be a non-empty string; got {val!r}"
    )


def test_claude_desktop_declares_corpus_forge_config_env() -> None:
    """``env.CORPUS_FORGE_CONFIG`` must be present so the user knows the knob exists."""
    entry = _server_entry(CLAUDE_DESKTOP_JSON)
    env = entry.get("env")
    assert isinstance(env, dict), f"env block missing or not an object; got {env!r}"
    val = env.get("CORPUS_FORGE_CONFIG")
    assert isinstance(val, str) and val, (
        f"CORPUS_FORGE_CONFIG must be a non-empty string; got {val!r}"
    )


# ── README hand-holding ──────────────────────────────────────────────────


def test_examples_readme_mentions_pip_extras() -> None:
    """Install steps must instruct the user to enable the ``[mcp]`` extra."""
    body = EXAMPLES_README.read_text(encoding="utf-8")
    # Allow ``corpus-forge[sqlite,mcp]`` or just ``[mcp]`` for the matrix install.
    assert re.search(r"corpus-forge\[[a-z,]*mcp[a-z,]*\]", body), (
        "README must show `pip install corpus-forge[...mcp...]` somewhere"
    )


def test_examples_readme_mentions_both_surfaces() -> None:
    """README must spell out which file goes where for Claude Code + Desktop."""
    body = EXAMPLES_README.read_text(encoding="utf-8").lower()
    assert "claude code" in body, "README missing 'Claude Code' surface"
    assert "claude desktop" in body, "README missing 'Claude Desktop' surface"
    # macOS desktop config path is load-bearing for a working drop-in install.
    assert "claude_desktop_config.json" in body, (
        "README must reference 'claude_desktop_config.json' install path"
    )


def test_examples_readme_mentions_warmup_commands() -> None:
    """README must walk through `migrate` + `ingest` before the user expects search hits."""
    body = EXAMPLES_README.read_text(encoding="utf-8")
    assert "corpus-forge migrate" in body, "README missing 'corpus-forge migrate'"
    assert "corpus-forge ingest" in body, "README missing 'corpus-forge ingest'"
