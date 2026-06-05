"""CS-05 / F-05 — Skill <-> MCP server tool-name contract test (rot-detector).

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

--- F-05 additions ---

``test_server_exposes_15_tools_when_writes_enabled`` — in-process
``build_server(writes_enabled=True)`` must advertise all 15 tools
(5 read + 10 write).  This is a load-bearing pin that does NOT require
Docker or a seed corpus; it exercises only the server registration code.

``test_skill_tools_match_mcp_server_tools`` — relaxed in F-05 to a
SUBSET check (``server_tools ⊇ skill_tools``).  The SKILL.md currently
declares 3 tools (CS-05 era); the server now exposes 11 when
``writes_enabled=True``.  Phase I will update the skill assets to list
all 11.  Until then we assert the skill doesn't declare anything the
server lacks (rot detector), NOT that the server matches the skill
exactly.  The server may legally expose MORE tools than the skill
declares.  This is the "phase-gated" relaxation.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

mcp = pytest.importorskip("mcp")
yaml = pytest.importorskip("yaml")

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

pytestmark = pytest.mark.smoke

# ── Expected write tool names (F-03 / F-05 / G-03 / H-02 / J4 / Phase M Wave 3) ──
_WRITE_TOOLS = {
    "add_label",
    "remove_label",
    "set_metadata",
    "set_description",
    "list_labels",
    "append_conversation",
    "append_message",
    "add_feedback",
    # G-03 write tool
    "register_template",
    # H-02 write tool
    "register_session",
    # J4 write tool
    "commit_curation",
    # Phase M Wave 3 — .corpusignore write tools
    "add_ignore_pattern",
    "remove_ignore_pattern",
    "sync_ignore",
    # Phase M Wave 4 — Zotero ingest tool
    "zotero_sync",
    # Phase P Wave 2 — search result rating tool
    "rate_search_result",
    # Phase Q Wave 1 — SDFT demonstration capture tool
    "record_demonstration",
}
# G-03: render_conversation + list_chat_templates are always-available read tools.
# J1:   estimate_sync_size is an always-available read tool (no backend writes).
# J4:   next_curation_target / next_curation_batch are always-available read tools.
# Phase M Wave 3: list_ignore + validate_ignore are always-available read tools.
# Phase O Wave 4: analyze_corpus / find_duplicates / cluster_topics / score_quality.
_READ_TOOLS = {
    "search",
    "get_chunk",
    "list_datasets",
    "render_conversation",
    "list_chat_templates",
    "estimate_sync_size",
    "next_curation_target",
    "next_curation_batch",
    "list_ignore",
    "validate_ignore",
    "analyze_corpus",
    "find_duplicates",
    "cluster_topics",
    "score_quality",
    # PR #83 — agent-friendly chunk navigation (read-only)
    "chunk_neighbors",
    "get_document",
    # RFC version-update-awareness — update check (read-only)
    "check_update",
}
_ALL_TOOLS = _READ_TOOLS | _WRITE_TOOLS


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


def _list_in_process_server_tools(writes_enabled: bool) -> set[str]:
    """Build an in-process MCP server and return its registered tool names.

    Uses an SQLite in-memory backend + LexicalRetriever stub so no Docker
    or seed corpus is required.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.mcp.server import build_server

    backend = SQLiteBackend(path=":memory:")
    backend.migrate()

    class _StubRetriever:
        def __init__(self) -> None:
            self.backend = backend

        def search(self, query: str, options: Any) -> list:
            return []

    retriever = _StubRetriever()
    server = build_server(retriever_builder=lambda: retriever, writes_enabled=writes_enabled)

    async def _run() -> list[str]:
        from mcp.types import ListToolsRequest

        handler = server.request_handlers.get(ListToolsRequest)
        assert handler is not None, "ListToolsRequest handler not registered"
        request = ListToolsRequest(method="tools/list")
        result = await handler(request)
        root = result.root if hasattr(result, "root") else result
        return [t.name for t in root.tools]

    return set(asyncio.run(_run()))


def _skill_allowed_tools() -> list[str]:
    """Parse SKILL.md frontmatter, return the raw ``allowed-tools`` list."""
    raw = SKILL_PATH.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.DOTALL)
    assert m, f"{SKILL_PATH} missing YAML frontmatter"
    fm = yaml.safe_load(m.group(1))
    tools = fm.get("allowed-tools")
    assert isinstance(tools, list), f"allowed-tools must be a YAML list; got {tools!r}"
    return tools


# ── F-05 in-process contract test (no Docker / seed corpus needed) ────────


def test_server_exposes_all_tools_when_writes_enabled() -> None:
    """build_server(writes_enabled=True) must advertise every read + write tool.

    J4 update: 8 read tools + 11 write tools = 19 total.
    (J1 added estimate_sync_size; J4 added next_curation_target /
    next_curation_batch as read tools and commit_curation as a write tool.)
    """
    tools = _list_in_process_server_tools(writes_enabled=True)
    missing = _ALL_TOOLS - tools
    extra = tools - _ALL_TOOLS
    assert not missing, (
        f"Server with writes_enabled=True is missing expected tools: {sorted(missing)}. "
        f"Registered tools: {sorted(tools)}"
    )
    assert not extra, (
        f"Server with writes_enabled=True registered unexpected extra tools: {sorted(extra)}. "
        f"Expected exactly: {sorted(_ALL_TOOLS)}"
    )


def test_server_exposes_only_read_tools_when_writes_disabled() -> None:
    """build_server(writes_enabled=False) must advertise only the read tools.

    J4 update: 8 read tools (J1's estimate_sync_size + J4's
    next_curation_target / next_curation_batch joined the existing 5).
    """
    tools = _list_in_process_server_tools(writes_enabled=False)
    assert tools == _READ_TOOLS, (
        f"Server with writes_enabled=False must expose only {sorted(_READ_TOOLS)}; "
        f"got: {sorted(tools)}"
    )


# ── CS-05 subprocess contract test ────────────────────────────────────────


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

    # ── 3) Cross-check (phase-gated subset relaxation) ──
    #
    # F-05 relaxation: assert skill_tools ⊆ server_tools (NOT strict equality).
    # The SKILL.md currently lists only 3 tools (CS-05 era).  The server now
    # exposes 11 when writes_enabled=True.  Phase I will update the skill
    # assets to declare all 11.  Until then we only check that the skill
    # doesn't declare tools the server lacks (rot detector).  The server is
    # legally allowed to expose MORE tools than the skill declares.
    #
    # NOTE: the subprocess server is launched without writes_enabled=True
    # (default=False), so it will expose 3 tools — matching the skill.  When
    # Phase I updates the skill to 11 tools AND wires writes_enabled=True into
    # the CLI default, this check will catch any mismatch between the two.
    missing = expected_bare_names - server_tools
    assert not missing, (
        f"Skill declares MCP tools the server does not advertise (rot detector): "
        f"missing={sorted(missing)}; server_tools={sorted(server_tools)}; "
        f"skill_entries={sorted(declared)}"
    )
    # Document the intentional gap for Phase I trackers.
    not_in_skill = server_tools - expected_bare_names
    if not_in_skill:
        import warnings

        warnings.warn(
            f"Phase I gap: server exposes {len(not_in_skill)} tools not yet declared "
            f"in SKILL.md: {sorted(not_in_skill)}. Update SKILL.md in Phase I.",
            stacklevel=2,
        )
