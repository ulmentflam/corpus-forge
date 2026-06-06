"""RFC version-update-awareness — the ``check_update`` MCP tool.

Pattern matches ``test_mcp_estimate.py``: build a server with a fake
retriever, call the registered request handlers, parse JSON payloads.
All version-check network access is patched at the module boundary
(``corpus_forge.update.version_check._fetch_latest``); channel
detection is pinned so payloads are deterministic.
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

from corpus_forge.mcp.server import build_server  # noqa: E402
from corpus_forge.update import channels as channels_mod  # noqa: E402
from corpus_forge.update import version_check as vc_mod  # noqa: E402
from corpus_forge.update.channels import recommended_update_command  # noqa: E402

# ─── helpers (mirrors test_mcp_estimate.py) ─────────────────────────────


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


@pytest.fixture
def server():
    return build_server(retriever_builder=_FakeRetriever)


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pin channel detection, point the cache at tmp, clear opt-out."""
    monkeypatch.delenv("CF_NO_VERSION_CHECK", raising=False)
    monkeypatch.setattr(channels_mod, "detect_channel", lambda **kw: "uv-tool")
    cache_path = tmp_path / "version-check.json"
    monkeypatch.setattr(vc_mod, "DEFAULT_CACHE_PATH", cache_path)
    return cache_path


def _prime_cache(cache_path: Path, *, latest: str, when: float = 0.0) -> None:
    import time

    cache_path.write_text(
        json.dumps({"latest": latest, "last_checked_unix": int(when or time.time())}),
        encoding="utf-8",
    )


# ─── registration ───────────────────────────────────────────────────────


class TestRegistration:
    def test_check_update_is_listed(self, server) -> None:
        names = {t.name for t in _list_tools_via_handler(server)}
        assert "check_update" in names

    def test_input_schema_has_optional_force_refresh(self, server) -> None:
        tool = next(t for t in _list_tools_via_handler(server) if t.name == "check_update")
        assert tool.inputSchema["properties"]["force_refresh"]["type"] == "boolean"
        assert "required" not in tool.inputSchema or not tool.inputSchema["required"]


# ─── payload cases ──────────────────────────────────────────────────────


class TestPayload:
    def test_newer_available(
        self, server, monkeypatch: pytest.MonkeyPatch, deterministic_environment: Path
    ) -> None:
        _prime_cache(deterministic_environment, latest="99.0.0", when=2**33)
        monkeypatch.setattr(vc_mod, "__version__", "0.1.0", raising=False)
        result = _call_tool_via_handler(server, "check_update", {})
        payload = _extract_payload(result)
        assert payload["update_available"] is True
        assert payload["latest"] == "99.0.0"
        assert payload["channel"] == "uv-tool"
        assert payload["recommended_command"] == "corpus-forge update"
        assert payload["served_from_cache"] is True

    def test_equal_version_reports_no_update(self, server, deterministic_environment: Path) -> None:
        from corpus_forge import __version__ as installed

        _prime_cache(deterministic_environment, latest=installed, when=2**33)
        payload = _extract_payload(_call_tool_via_handler(server, "check_update", {}))
        assert payload["update_available"] is False
        assert payload["installed"] == installed

    def test_opted_out(
        self, server, monkeypatch: pytest.MonkeyPatch, deterministic_environment: Path
    ) -> None:
        _prime_cache(deterministic_environment, latest="99.0.0", when=2**33)
        monkeypatch.setenv("CF_NO_VERSION_CHECK", "1")
        fetches: list[bool] = []
        monkeypatch.setattr(vc_mod, "_fetch_latest", lambda **kw: fetches.append(True) or None)
        payload = _extract_payload(_call_tool_via_handler(server, "check_update", {}))
        assert payload["update_available"] is False
        assert payload["latest"] is None
        assert "disabled" in payload["note"]
        assert fetches == []  # no network, not even a cache-refresh attempt

    def test_offline_mirrors_silent_failure(
        self, server, monkeypatch: pytest.MonkeyPatch, deterministic_environment: Path
    ) -> None:
        # No cache + fetch fails → latest null, no update, no error.
        monkeypatch.setattr(vc_mod, "_fetch_latest", lambda **kw: None)
        result = _call_tool_via_handler(server, "check_update", {})
        assert not getattr(result, "isError", False)
        payload = _extract_payload(result)
        assert payload["latest"] is None
        assert payload["update_available"] is False

    def test_force_refresh_bypasses_fresh_cache(
        self, server, monkeypatch: pytest.MonkeyPatch, deterministic_environment: Path
    ) -> None:
        import time

        _prime_cache(deterministic_environment, latest="0.0.1", when=time.time())
        fetches: list[bool] = []

        def fake_fetch(**kw: object) -> str:
            fetches.append(True)
            return "99.0.0"

        monkeypatch.setattr(vc_mod, "_fetch_latest", fake_fetch)
        # Fresh cache + no force → served from cache, no fetch.
        payload = _extract_payload(_call_tool_via_handler(server, "check_update", {}))
        assert fetches == []
        assert payload["served_from_cache"] is True
        # force_refresh → fetch happens, fresh answer returned.
        payload = _extract_payload(
            _call_tool_via_handler(server, "check_update", {"force_refresh": True})
        )
        assert fetches == [True]
        assert payload["latest"] == "99.0.0"
        assert payload["served_from_cache"] is False


# ─── channel → recommended_command mapping ─────────────────────────────


class TestRecommendedCommand:
    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            ("uv-tool", "corpus-forge update"),
            ("pipx", "corpus-forge update"),
            ("brew", "corpus-forge update"),
            ("pip", "corpus-forge update"),
            ("source", "corpus-forge update"),
            ("docker", "docker pull ghcr.io/ulmentflam/corpus-forge:latest"),
        ],
    )
    def test_mapping(self, channel: str, expected: str) -> None:
        assert recommended_update_command(channel) == expected
