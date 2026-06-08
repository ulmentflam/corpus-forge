"""Unit tests for the setup wizard's Tailscale live-peer picker (RFC fleet-4 item 5).

Covers :func:`maybe_pick_tailscale_endpoint` /
:func:`maybe_pick_tailscale_endpoints`:

- offers remote peer names (self filtered out) and rewrites the answer to
  ``ts://<peer>:<port>[<suffix>]`` when one is picked;
- degrades SILENTLY (no prompt output, answer untouched) when
  ``peers()`` raises :class:`TailscaleUnavailable`, returns no remote
  peers, or in non-interactive mode;
- the picked ``ts://`` value flows through ``render_config_toml``.

The shellout boundary is never touched — tests patch
``corpus_forge.net.tailscale.peers`` (the module attribute the wizard
imports lazily), per the RFC's patch-the-boundary contract.
"""

from __future__ import annotations

import io
import tomllib
from typing import Any

import pytest

import corpus_forge.net.tailscale as ts
from corpus_forge.net.tailscale import Peer, TailscaleUnavailable
from corpus_forge.setup import render_config_toml
from corpus_forge.setup.wizard import (
    maybe_pick_tailscale_endpoint,
    maybe_pick_tailscale_endpoints,
)


def _peers() -> list[Peer]:
    """Self + two remote peers (one offline), self-first like real peers()."""
    return [
        Peer(name="mac", ips=("100.0.0.1",), online=True, is_self=True),
        Peer(name="gb10", ips=("100.0.0.2",), online=True),
        Peer(name="rig", ips=("100.0.0.3",), online=False),
    ]


class TestMaybePickTailscaleEndpoint:
    def test_picks_remote_peer_renders_ts_with_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {"postgres_dsn": "postgresql://localhost:5432/db"}
        out = io.StringIO()
        # "1" → first remote peer (gb10; self is filtered out).
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="the PostgreSQL host",
            interactive=True,
            stream_in=io.StringIO("1\n"),
            stream_out=out,
        )
        assert answers["postgres_dsn"] == "ts://gb10:5432"
        # The picker lists remote peers only, marking offline ones.
        rendered = out.getvalue()
        assert "gb10" in rendered
        assert "rig (offline)" in rendered
        assert "mac" not in rendered  # self filtered out

    def test_suffix_appended_for_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers: dict[str, str] = {"openai_base_url": ""}
        maybe_pick_tailscale_endpoint(
            answers,
            "openai_base_url",
            kind="ollama",
            label="the embedder base_url",
            suffix="/v1",
            interactive=True,
            stream_in=io.StringIO("2\n"),  # rig
            stream_out=io.StringIO(),
        )
        assert answers["openai_base_url"] == "ts://rig:11434/v1"

    def test_blank_keeps_current(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {"postgres_dsn": "postgresql://localhost/db"}
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=True,
            stream_in=io.StringIO("\n"),
            stream_out=io.StringIO(),
        )
        assert answers["postgres_dsn"] == "postgresql://localhost/db"

    def test_zero_keeps_current(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {"postgres_dsn": "keep"}
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=True,
            stream_in=io.StringIO("0\n"),
            stream_out=io.StringIO(),
        )
        assert answers["postgres_dsn"] == "keep"

    def test_out_of_range_keeps_current(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {"postgres_dsn": "keep"}
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=True,
            stream_in=io.StringIO("9\n"),
            stream_out=io.StringIO(),
        )
        assert answers["postgres_dsn"] == "keep"

    def test_non_numeric_keeps_current(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {"postgres_dsn": "keep"}
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=True,
            stream_in=io.StringIO("nope\n"),
            stream_out=io.StringIO(),
        )
        assert answers["postgres_dsn"] == "keep"

    def test_silent_skip_when_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> list[Peer]:
            raise TailscaleUnavailable("down", reason="daemon")

        monkeypatch.setattr(ts, "peers", _boom)
        answers = {"postgres_dsn": "keep"}
        out = io.StringIO()
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=True,
            stream_in=io.StringIO("1\n"),
            stream_out=out,
        )
        assert answers["postgres_dsn"] == "keep"
        assert out.getvalue() == ""  # no prompt emitted

    def test_silent_skip_when_only_self(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ts, "peers", lambda: [Peer(name="mac", ips=(), online=True, is_self=True)]
        )
        answers = {"postgres_dsn": "keep"}
        out = io.StringIO()
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=True,
            stream_in=io.StringIO("1\n"),
            stream_out=out,
        )
        assert answers["postgres_dsn"] == "keep"
        assert out.getvalue() == ""

    def test_silent_skip_non_interactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Non-interactive must not even probe peers().
        def _should_not_call() -> list[Peer]:
            raise AssertionError("peers() must not be probed in non-interactive mode")

        monkeypatch.setattr(ts, "peers", _should_not_call)
        answers = {"postgres_dsn": "ts://gb10:5432"}
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="x",
            interactive=False,
        )
        assert answers["postgres_dsn"] == "ts://gb10:5432"


class TestMaybePickTailscaleEndpoints:
    def test_offers_postgres_and_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {
            "backend": "postgres",
            "postgres_dsn": "postgresql://localhost/db",
            "embedder": "both",
            "openai_base_url": "",
        }
        # Two prompts in sequence: pick gb10 for postgres, rig for base_url.
        maybe_pick_tailscale_endpoints(
            answers,
            interactive=True,
            stream_in=io.StringIO("1\n2\n"),
            stream_out=io.StringIO(),
        )
        assert answers["postgres_dsn"] == "ts://gb10:5432"
        assert answers["openai_base_url"] == "ts://rig:11434/v1"

    def test_no_postgres_no_picker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ts, "peers", _peers)
        answers = {"backend": "sqlite", "embedder": "auto"}
        out = io.StringIO()
        maybe_pick_tailscale_endpoints(
            answers,
            interactive=True,
            stream_in=io.StringIO("1\n"),
            stream_out=out,
        )
        # No host fields → no prompt at all.
        assert out.getvalue() == ""


class TestPickedValueFlowsThroughRender:
    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        return tomllib.loads(text)

    def test_ts_dsn_renders_into_backend(self, tmp_path) -> None:
        answers = {
            "backend": "postgres",
            "postgres_dsn": "ts://gb10:5432/corpus_forge",
            "embedder": "auto",
        }
        # auto embedder shells out to detect_accelerator — force CPU.
        import corpus_forge.setup.wizard as wiz
        from corpus_forge.acceleration import Accelerator, AcceleratorInfo

        orig = wiz.detect_accelerator
        wiz.detect_accelerator = lambda: AcceleratorInfo(kind=Accelerator.CPU)  # type: ignore[assignment]
        try:
            parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        finally:
            wiz.detect_accelerator = orig  # type: ignore[assignment]
        assert parsed["backend"]["dsn"] == "ts://gb10:5432/corpus_forge"

    def test_ts_base_url_renders_into_embedder(self, tmp_path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "openai",
            "openai_base_url": "ts://rig:11434/v1",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        emb = parsed["embedders"][0]
        assert emb["base_url"] == "ts://rig:11434/v1"
