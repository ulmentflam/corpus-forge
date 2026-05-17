"""Unit tests for the Phase J / J1 MCP tool ``estimate_sync_size``.

Pattern matches ``test_mcp_server.py``: build a server with a fake
retriever, call the tool dispatch via the registered request handler,
parse the resulting JSON payload. No subprocess / stdio.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402,I001


# ─────────────────────────────────────────────────────────────────────────
# Helpers (mirrors test_mcp_server.py)
# ─────────────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _list_tools_via_handler(server) -> list[mcp_types.Tool]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return list(root.tools)


def _call_tool_via_handler(server, name: str, arguments: dict[str, Any]):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _extract_payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    block = result.content[0]
    return json.loads(block.text)


class _FakeRetriever:
    def __init__(self) -> None:
        self.backend = MagicMock()
        self.backend.list_datasets.return_value = []


def _write_config(tmp_path: Path) -> Path:
    """Minimal SQLite-backed config — the MCP estimator tool loads
    Config.load() on first call, just like the CLI."""
    db_path = tmp_path / "corpus.db"
    cfg = textwrap.dedent(
        f"""
        [backend]
        kind = "sqlite"
        dsn  = "{db_path.as_posix()}"

        [daemon]

        [[datasets]]
        name = "demo"
        kind = "text"
        sources = [{{plugin = "filesystem", root = "/tmp", chunker = "markdown"}}]

        [[embedders]]
        name      = "fake"
        provider  = "sentence_transformers"
        model_id  = "fake-1"
        dimension = 384
        """
    )
    p = tmp_path / "config.toml"
    p.write_text(cfg, encoding="utf-8")
    return p


def _scan_dir(tmp_path: Path) -> Path:
    scan = tmp_path / "scan"
    scan.mkdir(parents=True, exist_ok=True)
    (scan / "a.md").write_bytes(b"x" * 4096)
    (scan / "b.pdf").write_bytes(b"y" * 8192)
    return scan


def _build_server():
    from corpus_forge.mcp.server import build_server

    return build_server(retriever_builder=_FakeRetriever)


# ─────────────────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_sync_size_in_list_tools_always() -> None:
    """The tool is read-only — present regardless of writes_enabled."""
    from corpus_forge.mcp.server import build_server

    s1 = build_server(retriever_builder=_FakeRetriever, writes_enabled=False)
    s2 = build_server(retriever_builder=_FakeRetriever, writes_enabled=True)
    for s in (s1, s2):
        names = {t.name for t in _list_tools_via_handler(s)}
        assert "estimate_sync_size" in names


def test_estimate_sync_size_schema_required_path() -> None:
    server = _build_server()
    tools = {t.name: t for t in _list_tools_via_handler(server)}
    schema = tools["estimate_sync_size"].inputSchema
    assert schema["type"] == "object"
    assert "path" in schema["properties"]
    assert "path" in schema.get("required", [])


def test_estimate_sync_size_schema_advertises_optional_fields() -> None:
    server = _build_server()
    tools = {t.name: t for t in _list_tools_via_handler(server)}
    schema = tools["estimate_sync_size"].inputSchema
    props = schema["properties"]
    for field in ("dataset", "embedders", "compression_ratio"):
        assert field in props, f"missing {field}"


def test_estimate_sync_size_schema_rejects_extra_args() -> None:
    server = _build_server()
    tools = {t.name: t for t in _list_tools_via_handler(server)}
    schema = tools["estimate_sync_size"].inputSchema
    assert schema.get("additionalProperties") is False


# ─────────────────────────────────────────────────────────────────────────
# Dispatch — happy path
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_sync_size_returns_estimate_under_estimate_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_config(tmp_path)
    scan = _scan_dir(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))
    server = _build_server()
    result = _call_tool_via_handler(server, "estimate_sync_size", {"path": str(scan)})
    assert not result.isError, f"unexpected error: {result}"
    payload = _extract_payload(result)
    assert "estimate" in payload
    est = payload["estimate"]
    assert est["schema_version"] == 1
    assert est["file_count"] == 2


def test_estimate_sync_size_passthrough_args_to_estimate_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify args make it from MCP into estimate_sync.

    The MCP dispatcher imports the estimate module lazily as
    ``import corpus_forge.estimate as _estimate_mod`` so we can patch
    the ``estimate_sync`` attribute on that module directly.
    """
    cfg_path = _write_config(tmp_path)
    scan = _scan_dir(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

    import corpus_forge.estimate as estimate_mod

    real = estimate_mod.estimate_sync
    calls: list[dict[str, Any]] = []

    def fake_estimate(path, config, *, embedders=None, compression_ratio=None):
        calls.append(
            {
                "path": str(path),
                "embedders": embedders,
                "compression_ratio": compression_ratio,
            }
        )
        return real(path, config, embedders=embedders, compression_ratio=compression_ratio)

    monkeypatch.setattr(estimate_mod, "estimate_sync", fake_estimate)

    server = _build_server()
    _call_tool_via_handler(
        server,
        "estimate_sync_size",
        {
            "path": str(scan),
            "embedders": ["fake"],
            "compression_ratio": 0.5,
        },
    )
    assert len(calls) == 1
    assert calls[-1]["embedders"] == ["fake"]
    assert calls[-1]["compression_ratio"] == 0.5


def test_estimate_sync_size_default_compression_ratio_picked_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the caller doesn't pass compression_ratio, the value from
    config.estimate.compression_ratio is used (or the 1.0 default)."""
    cfg_path = _write_config(tmp_path)
    scan = _scan_dir(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))
    server = _build_server()
    result = _call_tool_via_handler(server, "estimate_sync_size", {"path": str(scan)})
    assert not result.isError
    est = _extract_payload(result)["estimate"]
    assert est["compression_ratio"] == 1.0


# ─────────────────────────────────────────────────────────────────────────
# Dispatch — error paths
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_sync_size_missing_path_error_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_config(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))
    server = _build_server()
    result = _call_tool_via_handler(
        server, "estimate_sync_size", {"path": str(tmp_path / "no-such-dir")}
    )
    assert result.isError
    blocks = result.content
    assert blocks, "isError result must still carry a content block"
    text = blocks[0].text
    assert "no-such-dir" in text or "does not exist" in text.lower()


def test_estimate_sync_size_unknown_embedder_error_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_config(tmp_path)
    scan = _scan_dir(tmp_path)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))
    server = _build_server()
    result = _call_tool_via_handler(
        server,
        "estimate_sync_size",
        {"path": str(scan), "embedders": ["does-not-exist"]},
    )
    assert result.isError
    text = result.content[0].text
    assert "does-not-exist" in text


def test_estimate_sync_size_schema_rejects_missing_path() -> None:
    """jsonschema validation should kick in at the MCP layer when ``path``
    is absent — the request is rejected before our dispatcher runs.

    We accept either a CallToolResult with isError=True or an exception
    bubbled out of the handler — both are acceptable signals of schema
    rejection.
    """
    import jsonschema

    server = _build_server()
    raised: Exception | None = None
    result = None
    try:
        result = _call_tool_via_handler(server, "estimate_sync_size", {})
    except (jsonschema.ValidationError, Exception) as exc:  # pragma: no cover
        raised = exc
    if raised is not None:
        return
    assert result is not None
    assert getattr(result, "isError", False), "calling estimate_sync_size without 'path' must error"
