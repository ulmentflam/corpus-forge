"""CS-05 — Skill <-> MCP server tool-name contract test (rot-detector).

This test pins the contract between two artefacts that ship together:

- ``.claude/skills/corpus-forge-search/SKILL.md`` — what Claude Code
  *thinks* the server exposes (via the ``allowed-tools`` frontmatter
  list, with the ``mcp__corpus-forge__`` prefix).
- ``corpus_forge/mcp/server.py`` — what the server *actually* exposes
  (via the live ``tools/list`` MCP reply).

If a future change renames a tool on the server side without updating
SKILL.md (or vice versa), this test fails loudly.

Pattern follows :mod:`tests.smoke.test_mcp_stdio` — same subprocess
launch, same seed-corpus skip, same env-var plumbing.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
yaml = pytest.importorskip("yaml")

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

pytestmark = pytest.mark.smoke


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "corpus-forge-search" / "SKILL.md"
_TOOL_PREFIX = "mcp__corpus-forge__"

_SEED_DB = Path("/tmp/corpus-forge-test.db")


# ── Helpers (cloned from test_mcp_stdio.py to keep the contract self-contained) ──


def _seed_corpus_available() -> tuple[bool, str]:
    if not _SEED_DB.exists():
        return False, (
            f"seed corpus missing at {_SEED_DB}; run "
            "`uv run python scripts/vectorize_repo_sqlite.py` first"
        )
    try:
        conn = sqlite3.connect(_SEED_DB)
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        emb_row = conn.execute(
            "SELECT name, provider, model_id, dimension FROM embedders LIMIT 1"
        ).fetchone()
        conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"seed corpus unreadable: {exc}"
    if n == 0:
        return False, "seed corpus has zero chunks"
    if emb_row is None:
        return False, "seed corpus has zero embedders"
    return True, ""


def _read_embedder_row() -> tuple[str, str, str, int]:
    conn = sqlite3.connect(_SEED_DB)
    row = conn.execute(
        "SELECT name, provider, model_id, dimension FROM embedders LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0], row[1], row[2], int(row[3])


def _write_config_toml(
    path: Path, embedder_name: str, provider: str, model_id: str, dim: int
) -> None:
    toml = f"""
datasets = []

[backend]
kind = "sqlite"
dsn = "{_SEED_DB}"

[daemon]
debounce_seconds = 1.0
log_level = "WARNING"
log_format = "text"

[[embedders]]
name = "{embedder_name}"
provider = "{provider}"
model_id = "{model_id}"
dimension = {dim}
normalize = true
distance = "cosine"
active = true
batch_size = 32
device = "cpu"
"""
    path.write_text(toml.lstrip(), encoding="utf-8")


async def _list_server_tools(server_params: StdioServerParameters) -> list[str]:
    """Connect, list tools, return the tool names advertised by the server."""
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools_response = await session.list_tools()
        return [t.name for t in tools_response.tools]


def _skill_allowed_tools() -> list[str]:
    """Parse SKILL.md frontmatter, return the raw ``allowed-tools`` list."""
    raw = SKILL_PATH.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.DOTALL)
    assert m, f"{SKILL_PATH} missing YAML frontmatter"
    fm = yaml.safe_load(m.group(1))
    tools = fm.get("allowed-tools")
    assert isinstance(tools, list), f"allowed-tools must be a YAML list; got {tools!r}"
    return tools


# ── Contract test ────────────────────────────────────────────────────────


def test_skill_tools_match_mcp_server_tools(tmp_path: Path) -> None:
    """Every ``mcp__corpus-forge__<tool>`` declared in the skill must be a real server tool.

    The skill file declares the tools Claude Code is *permitted* to invoke via
    the prefix ``mcp__corpus-forge__<tool>``.  Strip the prefix and confirm
    each remaining bare tool name appears in the server's live ``tools/list``
    reply.  Breaks loudly if a tool is renamed on either side without the
    other side being updated.
    """
    ok, reason = _seed_corpus_available()
    if not ok:
        pytest.skip(reason)

    # ── 1) Pull the live tool list from a freshly-spawned server ──
    name, provider, model_id, dim = _read_embedder_row()
    config_path = tmp_path / "config.toml"
    _write_config_toml(config_path, name, provider, model_id, dim)

    env = os.environ.copy()
    env["CORPUS_FORGE_CONFIG"] = str(config_path)
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "corpus_forge.cli", "mcp", "serve"],
        env=env,
    )
    server_tools = set(asyncio.run(_list_server_tools(server_params)))
    assert server_tools, "MCP server advertised no tools — server boot must be broken"

    # ── 2) Parse the skill's allowed-tools list ──
    declared = _skill_allowed_tools()
    assert declared, f"{SKILL_PATH} declared no allowed-tools — skill is unusable"

    # Every declared tool MUST carry the mcp__corpus-forge__ prefix; strip it
    # to compare against the server's bare names.
    expected_bare_names: set[str] = set()
    for entry in declared:
        assert isinstance(entry, str), f"allowed-tools entry not a string: {entry!r}"
        assert entry.startswith(_TOOL_PREFIX), (
            f"skill entry {entry!r} missing required prefix {_TOOL_PREFIX!r}"
        )
        expected_bare_names.add(entry[len(_TOOL_PREFIX) :])

    # ── 3) Cross-check ──
    missing = expected_bare_names - server_tools
    assert not missing, (
        f"Skill declares MCP tools the server does not advertise (rot detector): "
        f"missing={sorted(missing)}; server_tools={sorted(server_tools)}; "
        f"skill_entries={sorted(declared)}"
    )
