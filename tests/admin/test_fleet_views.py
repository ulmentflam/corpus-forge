"""Tests for the fleet read verbs ``models list`` / ``hosts list`` (rfc-fleet-1).

Three layers, all DB-free except the SQLite read-helper layer (which
drives a real in-``tmp_path`` ``SQLiteBackend`` through alembic so the
0018 telemetry tables exist):

* **Pure render / serialise helpers** — staleness bucketing
  (:func:`format_age`), accelerator-blob collapsing
  (:func:`accelerator_summary`), Rich-table smoke, and the ``--json``
  object shape including the rendered ``age`` field.
* **Backend read helpers** — "latest per (host, model)" correctness with
  multiple rows (the *older* row must NOT win), never-benchmarked models
  still appear, empty-table paths, host aggregate rate + model count.
* **CLI surface** — exit codes, the ``--json`` payload, the Rich-table
  default, and the missing-config / unreachable-backend error branches,
  with config + backend patched so the verbs never hit disk / DB.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_forge.admin import fleet_views as fv
from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# format_age — staleness bucketing
# ---------------------------------------------------------------------------


class TestFormatAge:
    def test_none_renders_never(self) -> None:
        assert fv.format_age(None) == "never"

    def test_seconds_bucket(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        past = now - timedelta(seconds=42)
        assert fv.format_age(past, now=now) == "42s ago"

    def test_minutes_bucket(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        assert fv.format_age(now - timedelta(minutes=5, seconds=10), now=now) == "5m ago"

    def test_hours_bucket(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        assert fv.format_age(now - timedelta(hours=2, minutes=30), now=now) == "2h ago"

    def test_days_bucket(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        assert fv.format_age(now - timedelta(days=3, hours=5), now=now) == "3d ago"

    def test_iso_string_input_parsed(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        iso = (now - timedelta(hours=1)).isoformat()
        assert fv.format_age(iso, now=now) == "1h ago"

    def test_naive_datetime_assumed_utc(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        naive = datetime(2026, 6, 5, 11, 0, 0)
        assert fv.format_age(naive, now=now) == "1h ago"

    def test_future_timestamp_clamps_to_zero(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        future = now + timedelta(minutes=10)
        assert fv.format_age(future, now=now) == "0s ago"

    def test_unparseable_string_renders_unknown(self) -> None:
        assert fv.format_age("not-a-timestamp") == "?"

    def test_unparseable_type_renders_unknown(self) -> None:
        assert fv.format_age(object()) == "?"


# ---------------------------------------------------------------------------
# accelerator_summary — blob collapsing
# ---------------------------------------------------------------------------


class TestAcceleratorSummary:
    def test_none_renders_dash(self) -> None:
        assert fv.accelerator_summary(None) == "—"

    def test_dict_device_name_with_vram(self) -> None:
        blob = {"kind": "cuda", "device_name": "GB10", "vram_mb": 20480}
        assert fv.accelerator_summary(blob) == "GB10 (20 GB)"

    def test_dict_falls_back_to_kind_when_no_device_name(self) -> None:
        assert fv.accelerator_summary({"kind": "mps", "device_name": None}) == "mps"

    def test_json_string_input_parsed(self) -> None:
        assert fv.accelerator_summary('{"kind": "cpu"}') == "cpu"

    def test_unparseable_json_string_renders_dash(self) -> None:
        assert fv.accelerator_summary("{not json") == "—"

    def test_non_dict_json_renders_dash(self) -> None:
        assert fv.accelerator_summary("[1, 2, 3]") == "—"

    def test_empty_fields_render_dash(self) -> None:
        assert fv.accelerator_summary({"kind": None, "device_name": None}) == "—"

    def test_zero_vram_omitted(self) -> None:
        assert fv.accelerator_summary({"kind": "cpu", "vram_mb": 0}) == "cpu"


# ---------------------------------------------------------------------------
# Render + serialise shapes
# ---------------------------------------------------------------------------


def _model_row(**over: Any) -> dict:
    base = {
        "model_key": "st:m1",
        "kind": "embedder",
        "provider": "st",
        "model_id": "m1",
        "dimension": 384,
        "host_id": "h1",
        "chunks_per_s": 99.0,
        "transport": "local",
        "device": "mps",
        "source": "bench",
        "measured_at": datetime(2026, 6, 5, 11, 0, 0, tzinfo=UTC),
    }
    base.update(over)
    return base


def _host_row(**over: Any) -> dict:
    base = {
        "host_id": "h1",
        "hostname": "mac",
        "os": "macOS",
        "accelerator": {"kind": "mps"},
        "models": 2,
        "latest_chunks_per_s": 99.0,
        "last_seen": datetime(2026, 6, 5, 11, 30, 0, tzinfo=UTC),
    }
    base.update(over)
    return base


class TestRenderAndSerialise:
    def test_models_table_smoke(self) -> None:
        from rich.console import Console

        from corpus_forge.ui import theme as _theme

        table = fv.render_models_table([_model_row(), _model_row(host_id=None, chunks_per_s=None)])
        console = Console(width=200, record=True, force_terminal=False, theme=_theme.build_theme())
        console.print(table)
        text = console.export_text()
        assert "st:m1" in text
        assert "Model registry" in text
        # Never-benchmarked row dashes the optional cells.
        assert "—" in text

    def test_models_to_dict_shape_and_age(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        out = fv.models_to_dict([_model_row()], now=now)
        assert out["count"] == 1
        row = out["models"][0]
        assert row["model_key"] == "st:m1"
        assert row["chunks_per_s"] == 99.0
        assert row["age"] == "1h ago"
        # model_id is carried through even though the table omits it.
        assert row["model_id"] == "m1"

    def test_cold_start_renders_seconds_in_table(self) -> None:
        """A row with ``cold_start_s`` shows ``"X.XXs"``; a None row dashes it."""
        from rich.console import Console

        from corpus_forge.ui import theme as _theme

        table = fv.render_models_table(
            [
                _model_row(cold_start_s=1.25),
                _model_row(host_id="h2", cold_start_s=None),
            ]
        )
        console = Console(width=200, record=True, force_terminal=False, theme=_theme.build_theme())
        console.print(table)
        text = console.export_text()
        assert "Cold start" in text  # new column header
        assert "1.25s" in text  # measured cold start
        assert "—" in text  # None cold start dashes

    def test_cold_start_carried_in_models_to_dict(self) -> None:
        out = fv.models_to_dict([_model_row(cold_start_s=2.5)])
        assert out["models"][0]["cold_start_s"] == 2.5
        # A row that never set cold_start_s serialises None (additive key).
        out_none = fv.models_to_dict([_model_row()])
        assert out_none["models"][0]["cold_start_s"] is None

    def test_hosts_table_smoke(self) -> None:
        from rich.console import Console

        from corpus_forge.ui import theme as _theme

        table = fv.render_hosts_table([_host_row()])
        console = Console(width=200, record=True, force_terminal=False, theme=_theme.build_theme())
        console.print(table)
        text = console.export_text()
        assert "h1" in text
        assert "Fleet hosts" in text

    def test_hosts_to_dict_shape_and_age(self) -> None:
        now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
        out = fv.hosts_to_dict([_host_row()], now=now)
        assert out["count"] == 1
        row = out["hosts"][0]
        assert row["host_id"] == "h1"
        assert row["accelerator"] == "mps"  # collapsed, not the raw blob
        assert row["last_seen_age"] == "30m ago"
        # No peer_status → online is null (Tailscale unavailable).
        assert row["online"] is None


class TestHostsTailscaleMarkers:
    """RFC fleet-4 item 5 — ●/○ markers + the --json ``online`` field."""

    @staticmethod
    def _render_text(table: Any) -> str:
        from rich.console import Console

        from corpus_forge.ui import theme as _theme

        console = Console(width=200, record=True, force_terminal=False, theme=_theme.build_theme())
        console.print(table)
        return console.export_text()

    def test_no_peer_status_omits_column(self) -> None:
        # peer_status=None → table identical to pre-fleet-4 (no column).
        table = self._render_text(fv.render_hosts_table([_host_row(tailscale_name="gb10")]))
        assert "Tailscale" not in table
        assert "●" not in table and "○" not in table

    def test_online_marker_when_name_matches_online_peer(self) -> None:
        table = self._render_text(
            fv.render_hosts_table([_host_row(tailscale_name="gb10")], peer_status={"gb10": True})
        )
        assert "Tailscale" in table
        assert "●" in table

    def test_offline_marker_when_peer_offline(self) -> None:
        table = self._render_text(
            fv.render_hosts_table([_host_row(tailscale_name="gb10")], peer_status={"gb10": False})
        )
        assert "○" in table

    def test_offline_marker_when_not_a_peer(self) -> None:
        # Host with a name that's NOT in the peer map → offline glyph.
        table = self._render_text(
            fv.render_hosts_table([_host_row(tailscale_name="ghost")], peer_status={"gb10": True})
        )
        assert "○" in table

    def test_offline_marker_when_no_tailscale_name(self) -> None:
        # Host that never reported a tailscale_name → offline glyph.
        table = self._render_text(fv.render_hosts_table([_host_row()], peer_status={"gb10": True}))
        assert "○" in table

    def test_json_online_true_false_and_none(self) -> None:
        # online True when matched + online.
        out = fv.hosts_to_dict([_host_row(tailscale_name="gb10")], peer_status={"gb10": True})
        assert out["hosts"][0]["online"] is True
        # online False when matched + offline / not a peer.
        out = fv.hosts_to_dict([_host_row(tailscale_name="gb10")], peer_status={"gb10": False})
        assert out["hosts"][0]["online"] is False


class TestProbePeerStatus:
    def test_returns_name_online_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.net.tailscale as ts
        from corpus_forge.net.tailscale import Peer

        monkeypatch.setattr(
            ts,
            "peers",
            lambda: [
                Peer(name="gb10", ips=(), online=True, is_self=True),
                Peer(name="rig", ips=(), online=False),
            ],
        )
        assert fv._probe_peer_status() == {"gb10": True, "rig": False}

    def test_none_when_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.net.tailscale as ts
        from corpus_forge.net.tailscale import TailscaleUnavailable

        def _boom() -> Any:
            raise TailscaleUnavailable("down", reason="daemon")

        monkeypatch.setattr(ts, "peers", _boom)
        assert fv._probe_peer_status() is None

    def test_none_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.net.tailscale as ts

        monkeypatch.setattr(ts, "peers", list)
        assert fv._probe_peer_status() is None


# ---------------------------------------------------------------------------
# Backend read helpers — real SQLite (alembic-migrated)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SQLiteBackend:
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()
    return backend


class TestSqliteReadHelpers:
    def test_empty_tables_return_empty(self, sqlite_backend: SQLiteBackend) -> None:
        assert sqlite_backend.list_models_with_latest_benchmark() == []
        assert sqlite_backend.list_hosts_with_latest_rate() == []
        assert sqlite_backend.model_benchmark_stats() == {"count": 0, "freshest": None}

    def test_latest_per_host_model_older_row_does_not_win(
        self, sqlite_backend: SQLiteBackend
    ) -> None:
        import time

        b = sqlite_backend
        b.upsert_host(host_id="h1", hostname="mac", os="macOS", accelerator={"kind": "mps"})
        b.upsert_models(
            [
                {
                    "model_key": "st:m1",
                    "kind": "embedder",
                    "provider": "st",
                    "model_id": "m1",
                    "dimension": 384,
                }
            ]
        )
        # Older row first, then a fresher row — the fresher must win.
        b.insert_model_benchmark(
            host_id="h1",
            model_key="st:m1",
            source="bench",
            transport="local",
            device="mps",
            batch_size=32,
            sample_chunks=64,
            chunks_per_s=10.0,
        )
        time.sleep(0.01)
        b.insert_model_benchmark(
            host_id="h1",
            model_key="st:m1",
            source="embed-run",
            transport="local",
            device="mps",
            batch_size=32,
            sample_chunks=64,
            chunks_per_s=99.0,
        )
        rows = b.list_models_with_latest_benchmark()
        latest = [r for r in rows if r["host_id"] == "h1"]
        assert len(latest) == 1
        assert latest[0]["chunks_per_s"] == 99.0
        assert latest[0]["source"] == "embed-run"

    def test_never_benchmarked_model_still_appears(self, sqlite_backend: SQLiteBackend) -> None:
        b = sqlite_backend
        b.upsert_models(
            [
                {
                    "model_key": "st:m2",
                    "kind": "embedder",
                    "provider": "st",
                    "model_id": "m2",
                    "dimension": 768,
                }
            ]
        )
        rows = b.list_models_with_latest_benchmark()
        assert len(rows) == 1
        assert rows[0]["model_key"] == "st:m2"
        assert rows[0]["host_id"] is None
        assert rows[0]["chunks_per_s"] is None

    def test_per_host_rows_and_host_aggregate(self, sqlite_backend: SQLiteBackend) -> None:
        b = sqlite_backend
        b.upsert_host(host_id="h1", hostname="mac", os="macOS", accelerator={"kind": "mps"})
        b.upsert_host(
            host_id="h2",
            hostname="gb10",
            os="Linux",
            accelerator={"kind": "cuda", "device_name": "GB10"},
        )
        b.upsert_models(
            [
                {
                    "model_key": "st:m1",
                    "kind": "embedder",
                    "provider": "st",
                    "model_id": "m1",
                    "dimension": 384,
                }
            ]
        )
        b.insert_model_benchmark(
            host_id="h1",
            model_key="st:m1",
            source="bench",
            transport="local",
            device="mps",
            batch_size=32,
            sample_chunks=64,
            chunks_per_s=99.0,
        )
        b.insert_model_benchmark(
            host_id="h2",
            model_key="st:m1",
            source="bench",
            transport="local",
            device="cuda",
            batch_size=64,
            sample_chunks=64,
            chunks_per_s=500.0,
        )
        # One model row per host that benchmarked it.
        model_rows = [r for r in b.list_models_with_latest_benchmark() if r["host_id"] is not None]
        assert {r["host_id"] for r in model_rows} == {"h1", "h2"}
        # Host aggregate: each host has 1 distinct model + its own rate.
        hosts = {r["host_id"]: r for r in b.list_hosts_with_latest_rate()}
        assert hosts["h1"]["models"] == 1
        assert hosts["h1"]["latest_chunks_per_s"] == 99.0
        assert hosts["h2"]["latest_chunks_per_s"] == 500.0

    def test_host_with_no_benchmarks_appears_with_nulls(
        self, sqlite_backend: SQLiteBackend
    ) -> None:
        b = sqlite_backend
        b.upsert_host(host_id="lonely", hostname="x", os="y", accelerator=None)
        rows = b.list_hosts_with_latest_rate()
        assert len(rows) == 1
        assert rows[0]["host_id"] == "lonely"
        assert rows[0]["models"] == 0
        assert rows[0]["latest_chunks_per_s"] is None

    def test_stats_count_and_freshest(self, sqlite_backend: SQLiteBackend) -> None:
        b = sqlite_backend
        b.upsert_host(host_id="h1", hostname="mac", os="macOS", accelerator=None)
        b.upsert_models(
            [
                {
                    "model_key": "st:m1",
                    "kind": "embedder",
                    "provider": "st",
                    "model_id": "m1",
                    "dimension": 384,
                }
            ]
        )
        b.insert_model_benchmark(
            host_id="h1",
            model_key="st:m1",
            source="bench",
            transport="local",
            device="cpu",
            batch_size=1,
            sample_chunks=1,
            chunks_per_s=1.0,
        )
        b.insert_model_benchmark(
            host_id="h1",
            model_key="st:m1",
            source="bench",
            transport="local",
            device="cpu",
            batch_size=1,
            sample_chunks=1,
            chunks_per_s=2.0,
        )
        stats = b.model_benchmark_stats()
        assert stats["count"] == 2
        assert stats["freshest"] is not None


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class _StubBackend:
    def __init__(
        self, *, models: list[dict] | None = None, hosts: list[dict] | None = None
    ) -> None:
        self._models = models or []
        self._hosts = hosts or []
        self.closed = False

    def list_models_with_latest_benchmark(self) -> list[dict]:
        return self._models

    def list_hosts_with_latest_rate(self) -> list[dict]:
        return self._hosts

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch config load + backend build so the CLI never hits disk / DB."""
    from corpus_forge.config import Config

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))
    holder: dict[str, Any] = {"backend": _StubBackend(models=[_model_row()], hosts=[_host_row()])}
    monkeypatch.setattr(fv, "_build_backend", lambda config: holder["backend"])
    return holder


def _force_human(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.ui import agent as agent_mod

    monkeypatch.setattr(agent_mod, "is_agent_mode", lambda detection=None: False)


class TestModelsListCli:
    def test_table_default(self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        result = runner.invoke(app, ["models", "list"])
        assert result.exit_code == 0
        # Narrow CliRunner width truncates cell + header text; assert on
        # the stable table title token and a box border instead.
        assert "Model registry" in result.output
        assert "└" in result.output
        # The JSON object must NOT be on the human path.
        assert '"count"' not in result.output
        assert patched["backend"].closed is True

    def test_json_payload(self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        result = runner.invoke(app, ["models", "list", "--json"])
        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload["count"] == 1
        assert payload["models"][0]["model_key"] == "st:m1"
        assert "age" in payload["models"][0]

    def test_missing_config_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        def _raise(cls: Any) -> Any:
            raise FileNotFoundError

        monkeypatch.setattr(Config, "load", classmethod(_raise))
        result = runner.invoke(app, ["models", "list"])
        assert result.exit_code == 2

    def test_backend_unreachable_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))

        def _boom(config: Any) -> Any:
            raise RuntimeError("no db")

        monkeypatch.setattr(fv, "_build_backend", _boom)
        result = runner.invoke(app, ["models", "list"])
        assert result.exit_code == 1


def _no_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the CLI's peer probe to "Tailscale absent" (no real shellout)."""
    monkeypatch.setattr(fv, "_probe_peer_status", lambda: None)


class TestHostsListCli:
    def test_table_default(self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        _no_tailscale(monkeypatch)
        result = runner.invoke(app, ["hosts", "list"])
        assert result.exit_code == 0
        assert "Fleet hosts" in result.output
        assert "└" in result.output
        # Tailscale absent → no marker column.
        assert "Tailscale" not in result.output

    def test_json_payload(self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        _no_tailscale(monkeypatch)
        result = runner.invoke(app, ["hosts", "list", "--json"])
        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload["count"] == 1
        assert payload["hosts"][0]["host_id"] == "h1"
        assert payload["hosts"][0]["accelerator"] == "mps"
        # Tailscale absent → online is null.
        assert payload["hosts"][0]["online"] is None

    def test_table_with_tailscale_markers(
        self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_human(monkeypatch)
        patched["backend"]._hosts = [_host_row(tailscale_name="gb10")]
        monkeypatch.setattr(fv, "_probe_peer_status", lambda: {"gb10": True})
        result = runner.invoke(app, ["hosts", "list"])
        assert result.exit_code == 0
        # Narrow CliRunner width truncates the header text; assert on the
        # online glyph the marker column renders instead.
        assert "●" in result.output

    def test_json_online_field_when_tailscale_present(
        self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_human(monkeypatch)
        patched["backend"]._hosts = [_host_row(tailscale_name="gb10")]
        monkeypatch.setattr(fv, "_probe_peer_status", lambda: {"gb10": False})
        result = runner.invoke(app, ["hosts", "list", "--json"])
        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload["hosts"][0]["online"] is False

    def test_backend_unreachable_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: object()))

        def _boom(config: Any) -> Any:
            raise RuntimeError("no db")

        monkeypatch.setattr(fv, "_build_backend", _boom)
        result = runner.invoke(app, ["hosts", "list"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# _build_backend dispatch
# ---------------------------------------------------------------------------


class TestBuildBackend:
    def test_unsupported_kind_raises(self) -> None:
        cfg = type("C", (), {})()
        cfg.backend = type("B", (), {"kind": "mongodb", "dsn": "x", "schema": "corpus"})()
        with pytest.raises(ValueError, match="Unsupported backend kind"):
            fv._build_backend(cfg)

    def test_sqlite_branch_builds_real_backend(self, tmp_path: Any) -> None:
        cfg = type("C", (), {})()
        cfg.backend = type(
            "B", (), {"kind": "sqlite", "dsn": str(tmp_path / "c.db"), "schema": "corpus"}
        )()
        backend = fv._build_backend(cfg)
        # The migrated backend can answer the telemetry read immediately.
        assert backend.list_models_with_latest_benchmark() == []


class TestSmallHelpers:
    def test_fmt_rate_handles_bad_value(self) -> None:
        assert fv._fmt_rate("not-a-number") == "—"
        assert fv._fmt_rate(None) == "—"
        assert fv._fmt_rate(12.345) == "12.3"

    def test_coerce_datetime_returns_none_for_unknown_type(self) -> None:
        assert fv._coerce_datetime(object()) is None

    def test_coerce_datetime_returns_none_for_none(self) -> None:
        assert fv._coerce_datetime(None) is None

    def test_close_backend_noop_without_close(self) -> None:
        # A backend with no close() must not raise.
        fv._close_backend(object())

    def test_load_config_missing_raises_exit_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import typer

        from corpus_forge.config import Config

        def _raise(cls: Any) -> Any:
            raise FileNotFoundError

        monkeypatch.setattr(Config, "load", classmethod(_raise))
        with pytest.raises(typer.Exit) as exc_info:
            fv._load_config_or_exit()
        assert exc_info.value.exit_code == 2

    def test_load_config_success_returns_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        from corpus_forge.config import Config

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: sentinel))
        assert fv._load_config_or_exit() is sentinel
