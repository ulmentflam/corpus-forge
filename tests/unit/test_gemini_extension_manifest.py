"""I2 — Gemini CLI extension manifest (`examples/gemini-extension/gemini-extension.json`).

Pins the shape of the extension manifest that users copy into
``~/.gemini/extensions/corpus-forge-search/``.  Asserts the required keys
per the Gemini CLI extension schema (name, version, description, mcpServers,
contextFileName) and the corpus-forge MCP server launch contract.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = REPO_ROOT / "examples" / "gemini-extension"
MANIFEST_PATH = EXTENSION_DIR / "gemini-extension.json"


# ── File exists ───────────────────────────────────────────────────────────


def test_gemini_extension_manifest_exists() -> None:
    """Extension manifest lives at the documented path."""
    assert MANIFEST_PATH.is_file(), f"missing {MANIFEST_PATH}"


# ── JSON parses ──────────────────────────────────────────────────────────


def test_gemini_extension_manifest_parses_as_json() -> None:
    """Manifest must round-trip through ``json.loads`` cleanly."""
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ── Required top-level keys ──────────────────────────────────────────────


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_gemini_extension_manifest_has_name() -> None:
    """``name`` is required by the Gemini CLI extension schema."""
    m = _manifest()
    assert "name" in m, f"manifest missing 'name' key (keys={list(m)})"
    assert isinstance(m["name"], str) and m["name"], "name must be a non-empty string"


def test_gemini_extension_manifest_has_version() -> None:
    """``version`` is required by the Gemini CLI extension schema."""
    m = _manifest()
    assert "version" in m, f"manifest missing 'version' key (keys={list(m)})"
    assert isinstance(m["version"], str) and m["version"], "version must be a non-empty string"


def test_gemini_extension_manifest_has_description() -> None:
    """``description`` is required by the Gemini CLI extension schema."""
    m = _manifest()
    assert "description" in m, f"manifest missing 'description' key (keys={list(m)})"
    assert isinstance(m["description"], str) and m["description"], (
        "description must be a non-empty string"
    )


def test_gemini_extension_manifest_has_context_file_name() -> None:
    """``contextFileName`` points the CLI at the system-instruction markdown file."""
    m = _manifest()
    assert "contextFileName" in m, f"manifest missing 'contextFileName' key (keys={list(m)})"
    assert m["contextFileName"] == "GEMINI.md", (
        f"expected contextFileName=='GEMINI.md'; got {m['contextFileName']!r}"
    )


def test_gemini_extension_manifest_has_mcp_servers() -> None:
    """``mcpServers`` declares the corpus-forge stdio server."""
    m = _manifest()
    assert "mcpServers" in m, f"manifest missing 'mcpServers' key (keys={list(m)})"
    servers = m["mcpServers"]
    assert isinstance(servers, dict), f"mcpServers must be an object; got {servers!r}"
    assert "corpus-forge" in servers, (
        f"mcpServers must contain 'corpus-forge' entry (got keys={list(servers)})"
    )


def test_gemini_extension_manifest_server_launch_contract() -> None:
    """The corpus-forge server entry must satisfy the R5 launch contract."""
    m = _manifest()
    entry = m["mcpServers"]["corpus-forge"]
    assert isinstance(entry, dict), f"server entry not an object: {entry!r}"
    assert entry.get("command") == "corpus-forge", (
        f"expected command=='corpus-forge'; got {entry.get('command')!r}"
    )
    args = entry.get("args")
    assert isinstance(args, list) and args[:2] == ["mcp", "serve"], (
        f"expected args to start with ['mcp', 'serve']; got {args!r}"
    )
    env = entry.get("env", {})
    assert isinstance(env, dict) and "CORPUS_FORGE_CONFIG" in env, (
        "env block must contain CORPUS_FORGE_CONFIG"
    )


def test_gemini_extension_manifest_name_matches_expected_slug() -> None:
    """``name`` must match the corpus-forge-search slug to avoid drift."""
    m = _manifest()
    assert m["name"] == "corpus-forge-search", (
        f"expected name=='corpus-forge-search'; got {m['name']!r}"
    )
