"""Phase M Wave 3 — MCP ``ignore_*`` tools.

Five new tools wired through ``build_server`` in
:mod:`corpus_forge.mcp.server`:

- ``list_ignore`` / ``validate_ignore``        — always available.
- ``add_ignore_pattern`` / ``remove_ignore_pattern`` / ``sync_ignore`` —
  gated by ``writes_enabled``.

Drives the in-process MCP request handlers directly (mirrors
``tests/unit/test_mcp_curation_tools.py``).

Note on location: the Wave 3 plan called for this file under
``tests/mcp/``, but creating ``tests/mcp/__init__.py`` shadows the
third-party ``mcp`` package at import time and breaks every other test
in this file. Co-locating under ``tests/unit/`` matches the established
convention used by ``test_mcp_curation_tools.py`` /
``test_mcp_estimate.py`` / ``test_mcp_writes_dispatch.py`` etc.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

from corpus_forge.ignore_defaults import (  # noqa: E402
    MANAGED_END,
    MANAGED_START,
)

# ── helpers ────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _list_tools(server) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _tool(server, name: str) -> mcp_types.Tool:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    for t in root.tools:
        if t.name == name:
            return t
    raise KeyError(name)


def _call(server, name: str, arguments: dict[str, Any]):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(result.content[0].text)


class _FakeRetriever:
    def __init__(self) -> None:
        self.backend = MagicMock()
        self.backend.list_datasets.return_value = []


def _build(*, writes_enabled: bool = False):
    from corpus_forge.mcp.server import build_server

    return build_server(
        retriever_builder=lambda: _FakeRetriever(),  # noqa: PLW0108
        writes_enabled=writes_enabled,
    )


def _render_managed(lines: list[str]) -> str:
    body = "\n".join(lines)
    return f"{MANAGED_START}\n{body}\n{MANAGED_END}\n"


def _seed(root: Path, *, managed: list[str], user: list[str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / ".corpusignore"
    text = _render_managed(managed)
    if user:
        text = text + "\n".join(user) + "\n"
    target.write_text(text, encoding="utf-8")
    return target


# ── tool registration / gating ─────────────────────────────────────────


def test_list_and_validate_ignore_always_available() -> None:
    for we in (False, True):
        names = _list_tools(_build(writes_enabled=we))
        assert "list_ignore" in names, f"we={we}"
        assert "validate_ignore" in names, f"we={we}"


def test_write_tools_gated_on_writes_enabled() -> None:
    off = _list_tools(_build(writes_enabled=False))
    for name in ("add_ignore_pattern", "remove_ignore_pattern", "sync_ignore"):
        assert name not in off, name
    on = _list_tools(_build(writes_enabled=True))
    for name in ("add_ignore_pattern", "remove_ignore_pattern", "sync_ignore"):
        assert name in on, name


def test_list_ignore_schema_rejects_extra_args() -> None:
    schema = _tool(_build(), "list_ignore").inputSchema
    assert schema.get("additionalProperties") is False
    assert "scope" in schema["properties"]


def test_add_ignore_pattern_schema_rejects_extra_args() -> None:
    schema = _tool(_build(writes_enabled=True), "add_ignore_pattern").inputSchema
    assert schema.get("additionalProperties") is False
    assert "pattern" in schema["properties"]
    assert "scope" in schema["properties"]


# ── list_ignore dispatch ───────────────────────────────────────────────


def test_list_ignore_returns_provenance_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate from the developer's global ignore — point CF_GLOBAL_IGNORE_FILE
    # at a known-empty file so the assertions below don't trip over the
    # user's own ~/.config/corpus-forge/ignore content.
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(tmp_path / "no-such-global"))
    _seed(tmp_path, managed=["*.lock"], user=["my-secret.json"])
    server = _build()
    result = _call(server, "list_ignore", {"scope": "all", "path": str(tmp_path)})
    assert not result.isError, result
    payload = _payload(result)
    assert "patterns" in payload
    assert payload["count"] == len(payload["patterns"])
    pats = {p["pattern"] for p in payload["patterns"]}
    assert {"*.lock", "my-secret.json"}.issubset(pats)
    # Managed/user split surfaced.
    by_pat = {(p["pattern"], p["source"]): p for p in payload["patterns"]}
    assert by_pat[("*.lock", "local")]["managed"] is True
    assert by_pat[("my-secret.json", "local")]["managed"] is False
    # line + source fields present.
    for p in payload["patterns"]:
        assert "source" in p
        assert "line" in p


# ── validate_ignore dispatch ───────────────────────────────────────────


def test_validate_ignore_clean(tmp_path: Path) -> None:
    target = _seed(tmp_path, managed=["*.lock"])
    server = _build()
    result = _call(server, "validate_ignore", {"path": str(target)})
    assert not result.isError, result
    payload = _payload(result)
    assert payload["ok"] is True


def test_validate_ignore_bad_line(tmp_path: Path) -> None:
    target = tmp_path / ".corpusignore"
    target.write_text("ok\nok\n[(broken\n", encoding="utf-8")
    server = _build()
    result = _call(server, "validate_ignore", {"path": str(target)})
    assert not result.isError, result
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["line"] == 3


# ── add_ignore_pattern dispatch ────────────────────────────────────────


def test_add_ignore_pattern_mutates_file(tmp_path: Path) -> None:
    target = _seed(tmp_path, managed=["*.lock"])
    server = _build(writes_enabled=True)
    r1 = _call(
        server,
        "add_ignore_pattern",
        {"pattern": "*.tmp", "scope": "local", "path": str(tmp_path)},
    )
    assert not r1.isError, r1
    p1 = _payload(r1)
    assert p1["added"] is True
    # Pattern lives below the closing sentinel.
    text = target.read_text(encoding="utf-8")
    end_idx = text.index(MANAGED_END)
    assert "*.tmp" in text[end_idx:]

    # Second call is idempotent no-op.
    r2 = _call(
        server,
        "add_ignore_pattern",
        {"pattern": "*.tmp", "scope": "local", "path": str(tmp_path)},
    )
    assert not r2.isError, r2
    assert _payload(r2)["added"] is False


# ── remove_ignore_pattern dispatch ─────────────────────────────────────


def test_remove_ignore_pattern_managed_returns_error(tmp_path: Path) -> None:
    target = _seed(tmp_path, managed=["*.lock"])
    before = target.read_text(encoding="utf-8")
    server = _build(writes_enabled=True)
    result = _call(
        server,
        "remove_ignore_pattern",
        {"pattern": "*.lock", "scope": "local", "path": str(tmp_path)},
    )
    assert result.isError, result
    # The error payload mentions the managed_block_protected kind.
    rendered = result.content[0].text if result.content else ""
    assert "managed_block_protected" in rendered
    # File untouched.
    assert target.read_text(encoding="utf-8") == before


def test_remove_ignore_pattern_user_region_succeeds(tmp_path: Path) -> None:
    target = _seed(tmp_path, managed=["*.lock"], user=["*.tmp"])
    server = _build(writes_enabled=True)
    result = _call(
        server,
        "remove_ignore_pattern",
        {"pattern": "*.tmp", "scope": "local", "path": str(tmp_path)},
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["removed"] is True
    end_idx = target.read_text(encoding="utf-8").index(MANAGED_END)
    tail = target.read_text(encoding="utf-8")[end_idx:]
    assert "*.tmp" not in tail


# ── sync_ignore dispatch ───────────────────────────────────────────────


def test_sync_ignore_regenerates(tmp_path: Path) -> None:
    target = _seed(tmp_path, managed=["stale-only"], user=["keep-me"])
    server = _build(writes_enabled=True)
    result = _call(server, "sync_ignore", {"root": str(tmp_path)})
    assert not result.isError, result
    payload = _payload(result)
    assert "updated" in payload
    text = target.read_text(encoding="utf-8")
    assert "stale-only" not in text
    assert "keep-me" in text
