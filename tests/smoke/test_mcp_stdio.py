"""R5-05 — `corpus-forge mcp serve` stdio smoke.

End-to-end: spawn ``uv run corpus-forge mcp serve`` as a subprocess,
drive it via :mod:`mcp.client.stdio`, and assert:

1. ``initialize`` succeeds.
2. ``tools/list`` returns exactly the three tool names:
   ``search`` / ``get_chunk`` / ``list_datasets``.
3. ``tools/call search`` against the seed corpus returns at least one
   hit (non-empty ``hits`` array).

The test points the subprocess at the seeded SQLite corpus at
``/tmp/corpus-forge-test.db`` via ``CORPUS_FORGE_CONFIG=<tmp>/config.toml``
— same pattern used by ``test_eval_smoke.py``.  Skips when the seed
corpus is missing.

Gated by ``pytest.importorskip("mcp")`` so the test silently disappears
when the optional ``[mcp]`` extra isn't installed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

pytestmark = pytest.mark.smoke


_SEED_DB = Path("/tmp/corpus-forge-test.db")


def _seed_corpus_available() -> tuple[bool, str]:
    """Returns ``(ok, reason)``.  ok=False ⇒ pytest.skip with reason."""
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
    """Pull the first embedder's name/provider/model_id/dim from the seed db."""
    conn = sqlite3.connect(_SEED_DB)
    row = conn.execute(
        "SELECT name, provider, model_id, dimension FROM embedders LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0], row[1], row[2], int(row[3])


def _write_config_toml(
    path: Path, embedder_name: str, provider: str, model_id: str, dim: int
) -> None:
    """Write a minimal config.toml pointing at the seed SQLite corpus."""
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


async def _drive_mcp_session(server_params: StdioServerParameters) -> dict:
    """Connect to the server, list tools, call search; return captured state."""
    captured: dict = {}
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools_response = await session.list_tools()
        captured["tools"] = [t.name for t in tools_response.tools]
        search_response = await session.call_tool("search", {"query": "lock_source", "k": 5})
        captured["search_isError"] = bool(getattr(search_response, "isError", False))
        captured["search_content"] = [
            {"type": c.type, "text": getattr(c, "text", None)} for c in search_response.content
        ]
        captured["search_structured"] = getattr(search_response, "structuredContent", None)
    return captured


def test_mcp_stdio_smoke(tmp_path: Path) -> None:
    """`corpus-forge mcp serve` subprocess answers initialize + list_tools + call_tool."""
    ok, reason = _seed_corpus_available()
    if not ok:
        pytest.skip(reason)

    name, provider, model_id, dim = _read_embedder_row()
    config_path = tmp_path / "config.toml"
    _write_config_toml(config_path, name, provider, model_id, dim)

    # Build StdioServerParameters with CORPUS_FORGE_CONFIG pointing at our
    # synthesised config.  The subprocess MUST honour this env var (Wave 4
    # adds the lookup to Config.load()).
    import os

    env = os.environ.copy()
    env["CORPUS_FORGE_CONFIG"] = str(config_path)
    # Keep HF offline so the subprocess doesn't try to redownload weights —
    # the seed corpus's embedder model is already in the local HF cache.
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "corpus_forge.cli", "mcp", "serve"],
        env=env,
    )

    captured = asyncio.run(_drive_mcp_session(server_params))

    # ── Assertions ────────────────────────────────────────────────────
    # G-03: render_conversation + list_chat_templates are always-available read tools.
    # J1:   estimate_sync_size is an always-available read tool.
    # J4:   next_curation_target + next_curation_batch are always-available read tools.
    # Phase M Wave 3: list_ignore + validate_ignore are always-available read tools.
    _expected_read_tools = {
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
        # Phase O Wave 4 — analyze read tools
        "analyze_corpus",
        "find_duplicates",
        "cluster_topics",
        "score_quality",
    }
    assert set(captured["tools"]) == _expected_read_tools, (
        f"Expected {len(_expected_read_tools)} read tools; got {captured['tools']}"
    )
    assert not captured["search_isError"], (
        f"search returned isError=True; content={captured['search_content']}"
    )
    # Payload comes back either as structuredContent (dict) or JSON text.
    payload = captured["search_structured"]
    if payload is None:
        import json

        text = captured["search_content"][0]["text"] or "{}"
        payload = json.loads(text)
    assert isinstance(payload, dict), f"search payload not a dict: {payload!r}"
    assert "hits" in payload, f"search payload missing 'hits'; got keys={list(payload)}"
    # Real seed corpus + a sensible query → at least one hit.
    assert len(payload["hits"]) >= 1, (
        f"search returned empty hits against the seed corpus; payload={payload}"
    )
