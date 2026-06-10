"""Tests for the informational ``embed_claims`` doctor check (rfc-fleet-2).

Like ``model_telemetry``, the check NEVER blocks doctor — every outcome is
``OK`` or ``WARN`` (or ``SKIP`` when config didn't load), never ``FAIL``.
What we pin:

1. Stale-claim count → ``OK`` "swept automatically" under the threshold;
   ``WARN`` above it.
2. Lease-TTL vs observed rate → ``WARN`` when the worst-case batch
   wall-clock nears the lease; skipped silently when no benchmark row.
3. Multi-host SQLite → ``WARN``; single-host SQLite → ``OK``.
4. Postgres with zero claims / no telemetry → ``OK``.
5. Backend unreachable / pre-migrate → ``OK`` "claims unavailable".
6. The check is registered in ``run_doctor`` (loaded + config-missing).

The backend / config are mocked so no real DB is needed; ``CheckStatus``
is never ``FAIL`` on any branch — that's the load-bearing invariant.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.backends.base import FederationUnsupported
from corpus_forge.doctor.checks import (
    _EMBED_CLAIMS_CONN_WARN_FRACTION,
    _LEASE_TTL_BATCH_FRACTION,
    _STALE_CLAIMS_WARN_THRESHOLD,
    CheckStatus,
    _check_embed_claims,
    _embed_claims_server_load_warning,
    run_doctor,
)


def _embedder(
    *,
    name: str = "qwen3-4096",
    provider: str = "openai",
    model_id: str = "qwen3",
    batch_size: int = 32,
    active: bool = True,
) -> MagicMock:
    ec = MagicMock()
    ec.name = name
    ec.provider = provider
    ec.model_id = model_id
    ec.batch_size = batch_size
    ec.active = active
    return ec


def _cfg(
    kind: str = "postgres",
    *,
    embedders: list | None = None,
    host_id: str = "host-A",
    claim_lease_ttl: int = 600,
) -> MagicMock:
    cfg = MagicMock()
    cfg.backend.kind = kind
    cfg.backend.dsn = "postgresql://x:y@localhost/z" if kind == "postgres" else "/tmp/c.db"
    cfg.backend.schema = "corpus"
    cfg.embedders = embedders if embedders is not None else []
    cfg.host_id.return_value = host_id
    cfg.embed.claim_lease_ttl = claim_lease_ttl
    return cfg


def _pg_backend(
    *,
    stale: int = 0,
    benchmark_rows: list | None = None,
    server_load: dict | None = None,
) -> MagicMock:
    backend = MagicMock()
    backend.count_stale_claims.return_value = stale
    backend.list_models_with_latest_benchmark.return_value = benchmark_rows or []
    # Default to a comfortably-idle server so server-load (issue #125) never
    # WARNs unless a test opts into a hot snapshot.
    backend.server_load.return_value = server_load or {
        "backends": 5,
        "max_connections": 100,
        "db_size_bytes": 12 * 1024 * 1024,
    }
    return backend


class TestStaleClaims:
    def test_zero_stale_ok(self) -> None:
        backend = _pg_backend(stale=0)
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "no stale claims" in result.detail

    def test_some_stale_under_threshold_ok(self) -> None:
        backend = _pg_backend(stale=42)
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "42 stale claim(s)" in result.detail
        assert "swept automatically" in result.detail

    def test_stale_over_threshold_warns(self) -> None:
        backend = _pg_backend(stale=_STALE_CLAIMS_WARN_THRESHOLD + 1)
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.WARN
        assert "crash-looping" in result.detail


class TestLeaseTtlSanity:
    def test_slow_rate_warns_naming_embedder_and_fix(self) -> None:
        # batch 64 chunks at 0.1 chunks/s = 640s worst case; > 50% of 600s.
        ec = _embedder(name="qwen3-4096", batch_size=64)
        backend = _pg_backend(
            stale=0,
            benchmark_rows=[
                {"host_id": "host-A", "model_key": "openai:qwen3", "chunks_per_s": 0.1},
            ],
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A", claim_lease_ttl=600)
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.WARN
        assert "qwen3-4096" in result.detail
        assert "claim_lease_ttl" in result.detail
        assert "600s" in result.detail

    def test_fast_rate_ok(self) -> None:
        # 32 chunks at 100 chunks/s = 0.32s — comfortably under budget.
        ec = _embedder(batch_size=32)
        backend = _pg_backend(
            stale=0,
            benchmark_rows=[
                {"host_id": "host-A", "model_key": "openai:qwen3", "chunks_per_s": 100.0},
            ],
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.OK

    def test_no_benchmark_for_lane_skipped_silently(self) -> None:
        # Embedder configured but no benchmark row → no WARN.
        ec = _embedder(name="nomic-code", provider="openai", model_id="nomic", batch_size=999)
        backend = _pg_backend(stale=0, benchmark_rows=[])
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.OK

    def test_benchmark_for_other_host_skipped(self) -> None:
        # A slow benchmark, but for a DIFFERENT host → not our problem.
        ec = _embedder(batch_size=64)
        backend = _pg_backend(
            stale=0,
            benchmark_rows=[
                {"host_id": "host-B", "model_key": "openai:qwen3", "chunks_per_s": 0.1},
            ],
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.OK

    def test_inactive_embedder_skipped(self) -> None:
        ec = _embedder(batch_size=64, active=False)
        backend = _pg_backend(
            stale=0,
            benchmark_rows=[
                {"host_id": "host-A", "model_key": "openai:qwen3", "chunks_per_s": 0.1},
            ],
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.OK

    def test_getattr_fallback_when_no_embed_config(self) -> None:
        # EmbedConfig not on main (PR #107): cfg has no `embed` attr →
        # getattr falls back to the 600s default in the message.
        ec = _embedder(name="qwen3-4096", batch_size=64)
        backend = _pg_backend(
            stale=0,
            benchmark_rows=[
                {"host_id": "host-A", "model_key": "openai:qwen3", "chunks_per_s": 0.1},
            ],
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A")
        # Simulate the pre-#107 world: no `embed` config block at all.
        del cfg.embed
        cfg.embed = None
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.WARN
        assert "600s" in result.detail


class TestServerLoad:
    """Issue #125 — the Postgres saturation hint."""

    def test_hot_server_warns_with_fix(self) -> None:
        backend = _pg_backend(
            stale=0,
            server_load={"backends": 90, "max_connections": 100, "db_size_bytes": 5 << 30},
        )
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.WARN
        assert "90/100" in result.detail
        assert "max_size" in result.detail  # names the fix knob

    def test_idle_server_ok(self) -> None:
        backend = _pg_backend(
            stale=0,
            server_load={"backends": 5, "max_connections": 100, "db_size_bytes": 1 << 20},
        )
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "no stale claims" in result.detail

    def test_server_load_warn_precedes_ttl_warn(self) -> None:
        # Both a hot server AND a slow-rate lease risk: the saturation WARN
        # (the more urgent "DB melting") is what surfaces.
        ec = _embedder(name="qwen3-4096", batch_size=64)
        backend = _pg_backend(
            stale=0,
            benchmark_rows=[
                {"host_id": "host-A", "model_key": "openai:qwen3", "chunks_per_s": 0.1}
            ],
            server_load={"backends": 95, "max_connections": 100, "db_size_bytes": 1 << 20},
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A", claim_lease_ttl=600)
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status == CheckStatus.WARN
        assert "95/100" in result.detail
        assert "claim_lease_ttl" not in result.detail  # server-load took precedence

    # ── helper-level edge cases (best-effort, never raise) ───────────────

    def test_helper_warns_at_threshold(self) -> None:
        backend = MagicMock()
        at = int(_EMBED_CLAIMS_CONN_WARN_FRACTION * 100)
        backend.server_load.return_value = {
            "backends": at,
            "max_connections": 100,
            "db_size_bytes": 0,
        }
        assert _embed_claims_server_load_warning(backend) is not None

    def test_helper_none_below_threshold(self) -> None:
        backend = MagicMock()
        backend.server_load.return_value = {
            "backends": 1,
            "max_connections": 100,
            "db_size_bytes": 0,
        }
        assert _embed_claims_server_load_warning(backend) is None

    def test_helper_none_when_no_method(self) -> None:
        backend = MagicMock(spec=[])  # no server_load attribute
        assert _embed_claims_server_load_warning(backend) is None

    def test_helper_none_when_read_raises(self) -> None:
        backend = MagicMock()
        backend.server_load.side_effect = Exception("permission denied for pg_stat_activity")
        assert _embed_claims_server_load_warning(backend) is None

    def test_helper_none_when_max_connections_zero(self) -> None:
        backend = MagicMock()
        backend.server_load.return_value = {
            "backends": 5,
            "max_connections": 0,
            "db_size_bytes": 0,
        }
        assert _embed_claims_server_load_warning(backend) is None


class TestSqliteMultiHost:
    def test_multi_host_warns(self) -> None:
        backend = MagicMock()
        backend.list_hosts_with_latest_rate.return_value = [
            {"host_id": "host-A"},
            {"host_id": "host-B"},
        ]
        with patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend):
            result = _check_embed_claims(_cfg("sqlite"))
        assert result.status == CheckStatus.WARN
        assert "federation requires postgres" in result.detail
        assert "2 hosts" in result.detail

    def test_single_host_ok(self) -> None:
        backend = MagicMock()
        backend.list_hosts_with_latest_rate.return_value = [{"host_id": "host-A"}]
        with patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend):
            result = _check_embed_claims(_cfg("sqlite"))
        assert result.status == CheckStatus.OK
        assert "single-host sqlite" in result.detail

    def test_hosts_table_missing_tolerated_ok(self) -> None:
        # Old SQLite DBs predate corpus.hosts — the read raises; tolerate.
        backend = MagicMock()
        backend.list_hosts_with_latest_rate.side_effect = Exception("no such table: hosts")
        with patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend):
            result = _check_embed_claims(_cfg("sqlite"))
        assert result.status == CheckStatus.OK
        assert "single-host sqlite" in result.detail


class TestUnavailable:
    def test_backend_construction_fails_ok(self) -> None:
        with patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            side_effect=RuntimeError("backend down"),
        ):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "claims unavailable" in result.detail

    def test_count_stale_raises_pre_migrate_ok(self) -> None:
        backend = MagicMock()
        backend.count_stale_claims.side_effect = Exception("no such table: embed_claims")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "claims unavailable" in result.detail

    def test_federation_unsupported_is_ok(self) -> None:
        # A non-postgres backend slipping past the kind branch must stay OK.
        backend = MagicMock()
        backend.count_stale_claims.side_effect = FederationUnsupported("nope")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(_cfg("postgres"))
        assert result.status == CheckStatus.OK
        assert "claims unavailable" in result.detail


class TestNeverFails:
    """The single most important property: the check never blocks doctor."""

    def test_no_branch_fails(self) -> None:
        ec = _embedder(batch_size=64)
        backend = _pg_backend(
            stale=_STALE_CLAIMS_WARN_THRESHOLD + 5,
            benchmark_rows=[
                {"host_id": "host-A", "model_key": "openai:qwen3", "chunks_per_s": 0.1},
            ],
        )
        cfg = _cfg("postgres", embedders=[ec], host_id="host-A")
        with patch("corpus_forge.backends.postgres.PostgresBackend", return_value=backend):
            result = _check_embed_claims(cfg)
        assert result.status != CheckStatus.FAIL


class TestThresholdConstants:
    def test_fraction_is_half(self) -> None:
        assert _LEASE_TTL_BATCH_FRACTION == 0.5

    def test_stale_threshold_generous(self) -> None:
        assert _STALE_CLAIMS_WARN_THRESHOLD >= 1000


class TestRegisteredInRunDoctor:
    """``run_doctor`` must include the new check unconditionally."""

    def test_embed_claims_appears_in_report_names(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        names = {r.name for r in report.results}
        assert "embed_claims" in names

    def test_embed_claims_skipped_when_config_missing(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        rows = [r for r in report.results if r.name == "embed_claims"]
        assert len(rows) == 1
        assert rows[0].status is CheckStatus.SKIP
        assert "config not loaded" in rows[0].detail
