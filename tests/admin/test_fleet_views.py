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


class TestHostsListCli:
    def test_table_default(self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        result = runner.invoke(app, ["hosts", "list"])
        assert result.exit_code == 0
        assert "Fleet hosts" in result.output
        assert "└" in result.output

    def test_json_payload(self, patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        result = runner.invoke(app, ["hosts", "list", "--json"])
        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload["count"] == 1
        assert payload["hosts"][0]["host_id"] == "h1"
        assert payload["hosts"][0]["accelerator"] == "mps"

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


# ---------------------------------------------------------------------------
# format_duration — projected-drain bucketing
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_none_renders_dash(self) -> None:
        assert fv.format_duration(None) == "—"

    def test_seconds_bucket(self) -> None:
        assert fv.format_duration(42) == "~42s"

    def test_minutes_bucket_drops_seconds(self) -> None:
        assert fv.format_duration(5 * 60 + 10) == "~5m"

    def test_hours_bucket_includes_minutes(self) -> None:
        # 2h13m = 7980s.
        assert fv.format_duration(2 * 3600 + 13 * 60) == "~2h13m"

    def test_days_bucket_includes_hours(self) -> None:
        # 1d5h.
        assert fv.format_duration(1 * 86400 + 5 * 3600) == "~1d5h"

    def test_tiny_positive_rounds_up_not_zero(self) -> None:
        assert fv.format_duration(0.2) == "~1s"

    def test_zero_renders_dash(self) -> None:
        assert fv.format_duration(0) == "—"

    def test_negative_renders_dash(self) -> None:
        assert fv.format_duration(-10) == "—"

    def test_nan_renders_dash(self) -> None:
        assert fv.format_duration(float("nan")) == "—"

    def test_inf_renders_dash(self) -> None:
        assert fv.format_duration(float("inf")) == "—"

    def test_unparseable_renders_dash(self) -> None:
        bad: Any = "not-a-number"
        assert fv.format_duration(bad) == "—"


# ---------------------------------------------------------------------------
# build_plan — greedy host→lane recommendation (pure)
# ---------------------------------------------------------------------------


class _Embedder:
    """Minimal stand-in for an active EmbedderConfig (duck-typed)."""

    def __init__(
        self,
        name: str,
        provider: str,
        model_id: str,
        *,
        extensions: list[str] | None = None,
        active: bool = True,
    ) -> None:
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self.extensions = extensions or []
        self.active = active


class TestBuildPlan:
    def test_picks_fastest_host_and_drain_math(self) -> None:
        embedders = [
            _Embedder("qwen", "st", "qwen3"),
            _Embedder("nomic", "st", "nomic"),
        ]
        # qwen: h1=10/s, h2=50/s → h2 wins. nomic: h1=100/s, h2=20/s → h1 wins.
        model_rows = [
            {"model_key": "st:qwen3", "host_id": "h1", "chunks_per_s": 10.0},
            {"model_key": "st:qwen3", "host_id": "h2", "chunks_per_s": 50.0},
            {"model_key": "st:nomic", "host_id": "h1", "chunks_per_s": 100.0},
            {"model_key": "st:nomic", "host_id": "h2", "chunks_per_s": 20.0},
        ]
        backlog = {1: 5000, 2: 1000}
        id_for = {"qwen": 1, "nomic": 2}
        lanes = fv.build_plan(
            embedders=embedders,
            model_rows=model_rows,
            embedder_id_for=lambda ec: id_for[ec.name],
            backlog_for=lambda eid, ext: backlog[eid],
        )
        by_lane = {lane["lane"]: lane for lane in lanes}
        # qwen → fastest host h2 @ 50/s, backlog 5000 → 100s drain.
        assert by_lane["qwen"]["recommended_host"] == "h2"
        assert by_lane["qwen"]["rate"] == 50.0
        assert by_lane["qwen"]["drain_seconds"] == 5000 / 50.0
        # nomic → fastest host h1 @ 100/s, backlog 1000 → 10s drain.
        assert by_lane["nomic"]["recommended_host"] == "h1"
        assert by_lane["nomic"]["drain_seconds"] == 1000 / 100.0

    def test_tie_breaks_on_host_id_ascending(self) -> None:
        embedders = [_Embedder("e", "st", "m")]
        model_rows = [
            {"model_key": "st:m", "host_id": "hb", "chunks_per_s": 50.0},
            {"model_key": "st:m", "host_id": "ha", "chunks_per_s": 50.0},
        ]
        lanes = fv.build_plan(
            embedders=embedders,
            model_rows=model_rows,
            embedder_id_for=lambda ec: 1,
            backlog_for=lambda eid, ext: 100,
        )
        assert lanes[0]["recommended_host"] == "ha"

    def test_no_benchmark_lane_gets_hint(self) -> None:
        embedders = [_Embedder("e", "st", "m")]
        lanes = fv.build_plan(
            embedders=embedders,
            model_rows=[],
            embedder_id_for=lambda ec: 1,
            backlog_for=lambda eid, ext: 42,
        )
        assert lanes[0]["recommended_host"] is None
        assert lanes[0]["rate"] is None
        assert lanes[0]["drain_seconds"] is None
        assert "bench embed" in lanes[0]["note"]

    def test_unregistered_lane_has_zero_backlog(self) -> None:
        embedders = [_Embedder("e", "st", "m")]
        lanes = fv.build_plan(
            embedders=embedders,
            model_rows=[{"model_key": "st:m", "host_id": "h1", "chunks_per_s": 5.0}],
            embedder_id_for=lambda ec: None,  # never registered
            backlog_for=lambda eid, ext: pytest.fail("backlog must not be queried"),
        )
        assert lanes[0]["backlog"] == 0

    def test_extensions_passed_through(self) -> None:
        seen: dict[str, Any] = {}
        embedders = [_Embedder("code", "st", "m", extensions=[".py", ".rs"])]

        def _backlog(eid: int, ext: list[str] | None) -> int:
            seen["ext"] = ext
            return 0

        fv.build_plan(
            embedders=embedders,
            model_rows=[],
            embedder_id_for=lambda ec: 1,
            backlog_for=_backlog,
        )
        assert seen["ext"] == [".py", ".rs"]

    def test_live_claims_recorded_when_provided(self) -> None:
        embedders = [_Embedder("e", "st", "m")]
        lanes = fv.build_plan(
            embedders=embedders,
            model_rows=[{"model_key": "st:m", "host_id": "h1", "chunks_per_s": 5.0}],
            embedder_id_for=lambda ec: 1,
            backlog_for=lambda eid, ext: 50,
            live_claims_for=lambda eid: 7,
        )
        assert lanes[0]["in_flight"] == 7

    def test_zero_and_none_rates_skipped(self) -> None:
        embedders = [_Embedder("e", "st", "m")]
        model_rows = [
            {"model_key": "st:m", "host_id": "h1", "chunks_per_s": 0.0},
            {"model_key": "st:m", "host_id": "h2", "chunks_per_s": None},
            {"model_key": "st:m", "host_id": "h3", "chunks_per_s": 9.0},
        ]
        lanes = fv.build_plan(
            embedders=embedders,
            model_rows=model_rows,
            embedder_id_for=lambda ec: 1,
            backlog_for=lambda eid, ext: 90,
        )
        assert lanes[0]["recommended_host"] == "h3"


class TestPlanRenderAndSerialise:
    def test_plan_table_smoke(self) -> None:
        from rich.console import Console

        from corpus_forge.ui import theme as _theme

        lanes = [
            {
                "lane": "qwen",
                "model_key": "st:qwen3",
                "backlog": 5000,
                "recommended_host": "h2",
                "rate": 50.0,
                "drain_seconds": 100.0,
                "in_flight": None,
                "note": None,
            },
            {
                "lane": "nomic",
                "model_key": "st:nomic",
                "backlog": 10,
                "recommended_host": None,
                "rate": None,
                "drain_seconds": None,
                "in_flight": None,
                "note": fv._NO_BENCH_HINT,
            },
        ]
        table = fv.render_plan_table(lanes)
        console = Console(width=200, record=True, force_terminal=False, theme=_theme.build_theme())
        console.print(table)
        text = console.export_text()
        assert "qwen" in text
        assert "Recommended host" in text
        assert "bench embed" in text  # the no-benchmark hint surfaces

    def test_plan_to_dict_shape(self) -> None:
        lanes = [
            {
                "lane": "qwen",
                "model_key": "st:qwen3",
                "backlog": 5000,
                "recommended_host": "h2",
                "rate": 50.0,
                "drain_seconds": 100.0,
                "in_flight": 3,
                "note": None,
            }
        ]
        out = fv.plan_to_dict(lanes)
        assert out["count"] == 1
        row = out["lanes"][0]
        assert row["lane"] == "qwen"
        assert row["recommended_host"] == "h2"
        assert row["drain"] == "~1m"  # 100s rounds into the minute bucket
        assert row["in_flight"] == 3


# ---------------------------------------------------------------------------
# hosts plan — CLI surface
# ---------------------------------------------------------------------------


class _PlanBackend:
    """Stub backend for ``hosts plan``; write methods fail the test if hit."""

    def __init__(
        self,
        *,
        models: list[dict] | None = None,
        ids: dict[str, int] | None = None,
        backlog: dict[int, int] | None = None,
        claims: dict[int, int] | None = None,
    ) -> None:
        self._models = models or []
        self._ids = ids or {}
        self._backlog = backlog or {}
        self._claims = claims or {}
        self.closed = False

    def list_models_with_latest_benchmark(self) -> list[dict]:
        return self._models

    def find_embedder_row_by_name(self, name: str) -> dict | None:
        eid = self._ids.get(name)
        return None if eid is None else {"id": eid, "name": name}

    def count_chunks_missing_embedding(
        self, embedder_id: int, *, extensions: list[str] | None = None
    ) -> int:
        return self._backlog.get(embedder_id, 0)

    def count_live_claims(self, embedder_id: int, exclude_host_id: str | None = None) -> int:
        return self._claims.get(embedder_id, 0)

    def close(self) -> None:
        self.closed = True

    # ── Write methods: any call is a read-only contract violation. ──────
    def write_embeddings(self, *a: Any, **k: Any) -> None:  # pragma: no cover
        raise AssertionError("hosts plan must be read-only — write_embeddings called")

    def claim_chunks_for_embedding(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
        raise AssertionError("hosts plan must be read-only — claim_chunks_for_embedding called")

    def register_embedder(self, *a: Any, **k: Any) -> int:  # pragma: no cover
        raise AssertionError("hosts plan must be read-only — register_embedder called")


def _cfg_with_embedders(embedders: list[Any], *, kind: str = "postgres") -> Any:
    cfg = type("C", (), {})()
    cfg.backend = type("B", (), {"kind": kind, "dsn": "x", "schema": "corpus"})()
    cfg.embedders = embedders
    return cfg


@pytest.fixture
def plan_patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch config + backend so ``hosts plan`` never touches disk / DB."""
    from corpus_forge.config import Config

    cfg = _cfg_with_embedders(
        [
            _Embedder("qwen", "st", "qwen3"),
            _Embedder("nomic", "st", "nomic"),
            # Inactive lane — exercises the active-filter (must be excluded).
            _Embedder("off", "st", "x", active=False),
        ]
    )
    monkeypatch.setattr(Config, "load", classmethod(lambda c: cfg))
    backend = _PlanBackend(
        models=[
            {"model_key": "st:qwen3", "host_id": "h2", "chunks_per_s": 50.0},
            {"model_key": "st:nomic", "host_id": "h1", "chunks_per_s": 100.0},
        ],
        ids={"qwen": 1, "nomic": 2},
        backlog={1: 5000, 2: 1000},
        claims={1: 2},
    )
    holder: dict[str, Any] = {"backend": backend, "cfg": cfg}
    monkeypatch.setattr(fv, "_build_backend", lambda config: holder["backend"])
    return holder


class TestHostsPlanCli:
    def test_table_default(
        self, plan_patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_human(monkeypatch)
        result = runner.invoke(app, ["hosts", "plan"])
        assert result.exit_code == 0
        assert "Recommended host" in result.output
        assert "└" in result.output
        assert plan_patched["backend"].closed is True

    def test_json_payload(
        self, plan_patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_human(monkeypatch)
        result = runner.invoke(app, ["hosts", "plan", "--json"])
        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload["count"] == 2
        by_lane = {lane["lane"]: lane for lane in payload["lanes"]}
        assert by_lane["qwen"]["recommended_host"] == "h2"
        assert by_lane["qwen"]["drain_seconds"] == 100.0
        assert by_lane["qwen"]["in_flight"] == 2

    def test_no_benchmarks_exits_0_with_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        cfg = _cfg_with_embedders([_Embedder("e", "st", "m")])
        monkeypatch.setattr(Config, "load", classmethod(lambda c: cfg))
        # Registry row exists (never benchmarked → host_id None), backlog>0.
        backend = _PlanBackend(
            models=[{"model_key": "st:m", "host_id": None, "chunks_per_s": None}],
            ids={"e": 1},
            backlog={1: 99},
        )
        monkeypatch.setattr(fv, "_build_backend", lambda config: backend)
        result = runner.invoke(app, ["hosts", "plan"])
        assert result.exit_code == 0
        assert "no benchmarks yet" in result.output

    def test_all_drained_exits_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        cfg = _cfg_with_embedders([_Embedder("e", "st", "m")])
        monkeypatch.setattr(Config, "load", classmethod(lambda c: cfg))
        backend = _PlanBackend(
            models=[{"model_key": "st:m", "host_id": "h1", "chunks_per_s": 5.0}],
            ids={"e": 1},
            backlog={1: 0},
        )
        monkeypatch.setattr(fv, "_build_backend", lambda config: backend)
        result = runner.invoke(app, ["hosts", "plan"])
        assert result.exit_code == 0
        assert "all lanes drained" in result.output

    def test_sqlite_backend_federation_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        cfg = _cfg_with_embedders([], kind="sqlite")
        monkeypatch.setattr(Config, "load", classmethod(lambda c: cfg))

        # _build_backend must never be reached on the sqlite gate.
        def _boom(config: Any) -> Any:
            raise AssertionError("sqlite must short-circuit before building a backend")

        monkeypatch.setattr(fv, "_build_backend", _boom)
        result = runner.invoke(app, ["hosts", "plan"])
        assert result.exit_code == 0
        assert "federation requires the postgres backend" in result.output

    def test_sqlite_json_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        cfg = _cfg_with_embedders([], kind="sqlite")
        monkeypatch.setattr(Config, "load", classmethod(lambda c: cfg))
        result = runner.invoke(app, ["hosts", "plan", "--json"])
        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload["status"] == "unsupported"

    def test_backend_unreachable_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_human(monkeypatch)
        from corpus_forge.config import Config

        cfg = _cfg_with_embedders([_Embedder("e", "st", "m")])
        monkeypatch.setattr(Config, "load", classmethod(lambda c: cfg))

        def _boom(config: Any) -> Any:
            raise RuntimeError("no db")

        monkeypatch.setattr(fv, "_build_backend", _boom)
        result = runner.invoke(app, ["hosts", "plan"])
        assert result.exit_code == 1


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
