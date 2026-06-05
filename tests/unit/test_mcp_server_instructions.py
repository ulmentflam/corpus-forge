"""RFC version-update-awareness — passive ``instructions=`` advisory.

``build_server()`` passes an instructions string to ``Server`` so
every MCP client learns about an available update at initialize,
without calling any tool. Cache-only by contract: building the server
must never hit the network.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip("mcp")

from corpus_forge.mcp import server as server_mod  # noqa: E402
from corpus_forge.mcp.server import build_server  # noqa: E402
from corpus_forge.update import version_check as vc_mod  # noqa: E402


class _FakeRetriever:
    def __init__(self) -> None:
        self.backend = MagicMock()


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("CF_NO_VERSION_CHECK", raising=False)
    cache_path = tmp_path / "version-check.json"
    monkeypatch.setattr(vc_mod, "DEFAULT_CACHE_PATH", cache_path)
    return cache_path


def _prime_cache(cache_path: Path, *, latest: str) -> None:
    cache_path.write_text(
        json.dumps({"latest": latest, "last_checked_unix": int(time.time())}),
        encoding="utf-8",
    )


class TestInstructions:
    def test_advisory_present_when_newer_available(
        self, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _prime_cache(isolated_cache, latest="99.0.0")
        monkeypatch.setattr(vc_mod, "__version__", "0.1.0", raising=False)
        server = build_server(retriever_builder=_FakeRetriever)
        assert server.instructions is not None
        assert "v99.0.0" in server.instructions
        assert "corpus-forge update" in server.instructions
        assert "check_update" in server.instructions

    def test_advisory_absent_when_current(self, isolated_cache: Path) -> None:
        from corpus_forge import __version__ as installed

        _prime_cache(isolated_cache, latest=installed)
        server = build_server(retriever_builder=_FakeRetriever)
        assert server.instructions is not None  # base instructions remain
        assert "newer corpus-forge" not in server.instructions

    def test_advisory_absent_without_cache(self, isolated_cache: Path) -> None:
        server = build_server(retriever_builder=_FakeRetriever)
        assert server.instructions is not None
        assert "newer corpus-forge" not in server.instructions

    def test_suppressed_under_opt_out(
        self, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _prime_cache(isolated_cache, latest="99.0.0")
        monkeypatch.setenv("CF_NO_VERSION_CHECK", "1")
        server = build_server(retriever_builder=_FakeRetriever)
        assert server.instructions is not None
        assert "newer corpus-forge" not in server.instructions

    def test_build_never_networks(
        self, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with a stale (expired-TTL) cache, build is cache-only."""
        isolated_cache.write_text(
            json.dumps({"latest": "99.0.0", "last_checked_unix": 0}),
            encoding="utf-8",
        )

        def explode(**kw: object) -> str:
            raise AssertionError("build_server must not call the network")

        monkeypatch.setattr(vc_mod, "_fetch_latest", explode)
        server = build_server(retriever_builder=_FakeRetriever)
        # Stale-but-present cache still yields the advisory (a stale
        # "newer available" is still true or harmlessly conservative).
        assert server.instructions is not None
        assert "v99.0.0" in server.instructions

    def test_advisory_helper_handles_corrupt_cache(self, isolated_cache: Path) -> None:
        isolated_cache.write_text("not json{", encoding="utf-8")
        assert server_mod._update_advisory() is None
