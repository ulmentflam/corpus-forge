"""Unit tests for the fleet-join flow (RFC fleet-3 item 5).

Covers the pure renderers and the orchestrating :func:`run_join` with a
fake backend (no live Postgres needed — the testcontainers integration
test in ``tests/integration/test_setup_join_two_host.py`` owns the
end-to-end shape):

- :func:`render_join_config` — skeleton-only (nothing published) loads;
  shared scope merges LIVE; shared datasets land as COMMENTED blocks
  (the validation trap) and are named in ``awaiting``.
- :func:`render_join_next_steps` — names the awaiting datasets + the
  ``config pull --apply`` cadence.
- :func:`run_join` — registers the host, records the pulled version,
  writes a loadable config; nothing-published path; existing-config
  refusal (non-interactive) and confirm-overwrite-with-backup
  (interactive).
"""

from __future__ import annotations

import io
import tomllib
from typing import Any

import pytest

from corpus_forge.config import Config
from corpus_forge.setup import (
    JoinError,
    render_join_config,
    render_join_next_steps,
    run_join,
)
from corpus_forge.setup import wizard as wizard_mod

# ── Fake backend ─────────────────────────────────────────────────────────


class _FakeBackend:
    """In-memory stand-in for the PostgresBackend the join flow drives."""

    def __init__(self, shared: tuple[int, dict] | None) -> None:
        self._shared = shared
        self.upserted: list[dict[str, Any]] = []
        self.closed = False

    def upsert_host(self, **kwargs: Any) -> None:
        self.upserted.append(kwargs)

    def get_shared_config(self) -> tuple[int, dict] | None:
        return self._shared

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def isolate_config(tmp_path, monkeypatch):
    """Point CORPUS_FORGE_CONFIG at a tmp config so the state file isolates."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
    return tmp_path


def _wire_backend(monkeypatch, backend: _FakeBackend) -> None:
    """Patch connect/verify + accelerator so run_join touches no real host."""
    monkeypatch.setattr(wizard_mod, "_connect_and_verify_schema", lambda dsn: backend)
    monkeypatch.setattr(
        "corpus_forge.telemetry_registry.accelerator_payload",
        lambda: {"kind": "cpu", "device_name": None, "vram_mb": None},
    )


_SHARED_BODY = {
    "datasets": [{"name": "default", "kind": "text"}, {"name": "code", "kind": "code"}],
    "embedders": [
        {
            "name": "nomic",
            "provider": "sentence_transformers",
            "model_id": "x",
            "dimension": 768,
            "normalize": True,
            "distance": "cosine",
            "active": True,
        }
    ],
    "retrieval": {"alpha": 0.5, "default_k": 10},
    "classifier": {"chain": ["rule"]},
}

_DSN = "postgresql://fleet-host:5432/corpus_forge"


# ── render_join_config ────────────────────────────────────────────────────


class TestRenderJoinConfig:
    def test_nothing_published_skeleton_loads(self) -> None:
        text, awaiting = render_join_config(_DSN, None)
        cfg = Config(**tomllib.loads(text))
        assert cfg.backend.kind == "postgres"
        assert cfg.backend.dsn == _DSN
        assert cfg.datasets == []
        assert awaiting == []

    def test_shared_scope_merges_live_and_loads(self) -> None:
        text, _awaiting = render_join_config(_DSN, _SHARED_BODY)
        cfg = Config(**tomllib.loads(text))
        # Live shared scope landed:
        assert [e.name for e in cfg.embedders] == ["nomic"]
        assert cfg.retrieval.alpha == 0.5
        assert cfg.retrieval.default_k == 10
        # Backend DSN is the join DSN:
        assert cfg.backend.dsn == _DSN

    def test_shared_datasets_are_commented_not_live(self) -> None:
        text, awaiting = render_join_config(_DSN, _SHARED_BODY)
        # The validation trap: sources-less shared datasets must NOT be
        # live (Config requires >=1 source/dataset) — so the loaded
        # config has zero datasets, but the names await sources.
        cfg = Config(**tomllib.loads(text))
        assert cfg.datasets == []
        assert awaiting == ["default", "code"]
        assert "# [[datasets]]" in text
        assert '# name = "default"' in text
        assert '# name = "code"' in text
        assert "fleet dataset — uncomment" in text

    def test_commented_blocks_carry_a_sources_template(self) -> None:
        text, _ = render_join_config(_DSN, _SHARED_BODY)
        assert "# sources = [{plugin" in text


# ── render_join_next_steps ─────────────────────────────────────────────────


class TestRenderJoinNextSteps:
    def test_names_awaiting_datasets(self) -> None:
        out = render_join_next_steps(["default", "code"])
        assert "default, code" in out

    def test_mentions_local_sources_and_pull_cadence(self) -> None:
        out = render_join_next_steps([])
        assert "[[datasets.sources]]" in out
        assert "config pull --apply" in out
        assert "bench embed --all" in out
        assert "ingest --once" in out


# ── run_join ────────────────────────────────────────────────────────────────


class TestRunJoin:
    def test_registers_host_and_records_version(self, isolate_config, monkeypatch) -> None:
        backend = _FakeBackend(shared=(7, _SHARED_BODY))
        _wire_backend(monkeypatch, backend)
        out = io.StringIO()

        config_path, awaiting = run_join(
            _DSN,
            config_dir=isolate_config,
            interactive=False,
            stream_out=out,
        )

        # Host registered with a stable id + the accelerator payload.
        assert len(backend.upserted) == 1
        reg = backend.upserted[0]
        assert reg["host_id"]
        assert reg["accelerator"]["kind"] == "cpu"
        assert backend.closed is True

        # Config written + loadable; shared scope present.
        assert config_path.exists()
        cfg = Config(**tomllib.loads(config_path.read_text(encoding="utf-8")))
        assert cfg.backend.dsn == _DSN
        assert [e.name for e in cfg.embedders] == ["nomic"]
        assert awaiting == ["default", "code"]

        # State file recorded the published version (7) beside the config.
        from corpus_forge.admin.federation import read_last_pulled_version

        assert read_last_pulled_version() == 7

        # host_id file seeded for stable re-derivation.
        assert (isolate_config / "host_id").exists()

    def test_nothing_published_records_version_0(self, isolate_config, monkeypatch) -> None:
        backend = _FakeBackend(shared=None)
        _wire_backend(monkeypatch, backend)
        out = io.StringIO()

        config_path, awaiting = run_join(
            _DSN,
            config_dir=isolate_config,
            interactive=False,
            stream_out=out,
        )

        # Still registers + renders a loadable skeleton with live parts.
        assert len(backend.upserted) == 1
        cfg = Config(**tomllib.loads(config_path.read_text(encoding="utf-8")))
        assert cfg.backend.dsn == _DSN
        assert awaiting == []
        assert "no shared config published yet" in out.getvalue()

        from corpus_forge.admin.federation import read_last_pulled_version

        assert read_last_pulled_version() == 0

    def test_existing_config_noninteractive_refuses(self, isolate_config, monkeypatch) -> None:
        (isolate_config / "config.toml").write_text("# pre-existing\n", encoding="utf-8")
        backend = _FakeBackend(shared=(1, _SHARED_BODY))
        _wire_backend(monkeypatch, backend)

        with pytest.raises(JoinError, match="already exists; refusing to overwrite"):
            run_join(
                _DSN,
                config_dir=isolate_config,
                interactive=False,
                stream_out=io.StringIO(),
            )
        # Never touched the backend — refused before connecting.
        assert backend.upserted == []
        # Original config untouched.
        assert (isolate_config / "config.toml").read_text(encoding="utf-8") == "# pre-existing\n"

    def test_existing_config_interactive_confirm_backs_up(
        self, isolate_config, monkeypatch
    ) -> None:
        original = "# pre-existing local config\n"
        (isolate_config / "config.toml").write_text(original, encoding="utf-8")
        backend = _FakeBackend(shared=(3, _SHARED_BODY))
        _wire_backend(monkeypatch, backend)
        # Confirm.ask → yes
        monkeypatch.setattr(
            "corpus_forge.ui.prompts.Confirm.ask", classmethod(lambda cls, *a, **k: True)
        )
        out = io.StringIO()

        config_path, _awaiting = run_join(
            _DSN,
            config_dir=isolate_config,
            interactive=True,
            stream_out=out,
        )

        # Backup written with the original contents.
        backup = isolate_config / "config.toml.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original
        # New config is the join render (loads + carries the DSN).
        cfg = Config(**tomllib.loads(config_path.read_text(encoding="utf-8")))
        assert cfg.backend.dsn == _DSN

    def test_existing_config_interactive_decline_aborts(self, isolate_config, monkeypatch) -> None:
        original = "# pre-existing\n"
        (isolate_config / "config.toml").write_text(original, encoding="utf-8")
        backend = _FakeBackend(shared=(1, _SHARED_BODY))
        _wire_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "corpus_forge.ui.prompts.Confirm.ask", classmethod(lambda cls, *a, **k: False)
        )

        with pytest.raises(JoinError, match="left untouched"):
            run_join(
                _DSN,
                config_dir=isolate_config,
                interactive=True,
                stream_out=io.StringIO(),
            )
        # Declined before connecting; config preserved.
        assert backend.upserted == []
        assert (isolate_config / "config.toml").read_text(encoding="utf-8") == original

    def test_next_steps_printed(self, isolate_config, monkeypatch) -> None:
        backend = _FakeBackend(shared=(2, _SHARED_BODY))
        _wire_backend(monkeypatch, backend)
        out = io.StringIO()

        run_join(_DSN, config_dir=isolate_config, interactive=False, stream_out=out)

        printed = out.getvalue()
        assert "Next steps:" in printed
        assert "config pull --apply" in printed
        assert "awaiting local sources: default, code" in printed

    def test_upsert_failure_raises_joinerror(self, isolate_config, monkeypatch) -> None:
        backend = _FakeBackend(shared=(1, _SHARED_BODY))

        def _boom(**_: Any) -> None:
            raise RuntimeError("hosts table locked")

        backend.upsert_host = _boom  # type: ignore[method-assign]
        _wire_backend(monkeypatch, backend)

        with pytest.raises(JoinError, match="could not register this host"):
            run_join(
                _DSN,
                config_dir=isolate_config,
                interactive=False,
                stream_out=io.StringIO(),
            )
        # Backend still closed despite the failure (finally).
        assert backend.closed is True


# ── _connect_and_verify_schema (DSN / schema verification) ─────────────────


class _SchemaBackend:
    """Fake PostgresBackend whose ``_execute`` returns a canned schema probe."""

    def __init__(self, dsn: str, schema: str = "corpus", **_: Any) -> None:
        self.dsn = dsn
        self.closed = False

    def _execute(self, query: str, params: tuple = ()) -> list[dict]:
        # ``to_regclass`` present → table exists.
        return [{"reg": "corpus.hosts"}]

    def close(self) -> None:
        self.closed = True


class _NoSchemaBackend(_SchemaBackend):
    def _execute(self, query: str, params: tuple = ()) -> list[dict]:
        return [{"reg": None}]


class TestConnectAndVerifySchema:
    def test_unreachable_dsn_raises_joinerror(self, monkeypatch) -> None:
        def _boom(*a: Any, **k: Any):
            raise OSError("connection refused")

        monkeypatch.setattr("corpus_forge.backends.postgres.PostgresBackend", _boom)
        with pytest.raises(JoinError, match="could not connect"):
            wizard_mod._connect_and_verify_schema(_DSN)

    def test_missing_schema_raises_joinerror(self, monkeypatch) -> None:
        monkeypatch.setattr("corpus_forge.backends.postgres.PostgresBackend", _NoSchemaBackend)
        with pytest.raises(JoinError, match="corpus schema is missing"):
            wizard_mod._connect_and_verify_schema(_DSN)

    def test_present_schema_returns_backend(self, monkeypatch) -> None:
        monkeypatch.setattr("corpus_forge.backends.postgres.PostgresBackend", _SchemaBackend)
        backend = wizard_mod._connect_and_verify_schema(_DSN)
        assert backend.dsn == _DSN

    def test_query_failure_raises_joinerror_and_closes(self, monkeypatch) -> None:
        class _QueryBoom(_SchemaBackend):
            def _execute(self, query: str, params: tuple = ()) -> list[dict]:
                raise RuntimeError("server closed connection")

        monkeypatch.setattr("corpus_forge.backends.postgres.PostgresBackend", _QueryBoom)
        with pytest.raises(JoinError, match="could not query"):
            wizard_mod._connect_and_verify_schema(_DSN)


# ── host-id derivation ─────────────────────────────────────────────────────


class TestResolveJoinHostId:
    def test_seeds_and_persists_hostname(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("socket.gethostname", lambda: "fresh-box")
        hid = wizard_mod._resolve_join_host_id(tmp_path)
        assert hid == "fresh-box"
        assert (tmp_path / "host_id").read_text(encoding="utf-8").strip() == "fresh-box"

    def test_reuses_existing_host_id_file(self, tmp_path) -> None:
        (tmp_path / "host_id").write_text("prior-id\n", encoding="utf-8")
        assert wizard_mod._resolve_join_host_id(tmp_path) == "prior-id"


# ── CLI wiring ─────────────────────────────────────────────────────────────


class TestCliJoinFlag:
    def test_join_flag_invokes_run_join(self, tmp_path, monkeypatch) -> None:
        from typer.testing import CliRunner

        from corpus_forge.cli import app

        seen: dict[str, Any] = {}

        def _fake_run_join(dsn, *, config_dir, interactive, **_):
            seen["dsn"] = dsn
            seen["interactive"] = interactive
            return tmp_path / "config.toml", ["default"]

        monkeypatch.setattr("corpus_forge.setup.run_join", _fake_run_join)

        result = CliRunner().invoke(
            app,
            ["setup", "--join", _DSN, "--non-interactive", "--config-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert seen["dsn"] == _DSN
        assert seen["interactive"] is False

    def test_join_flag_joinerror_exits_1(self, tmp_path, monkeypatch) -> None:
        from typer.testing import CliRunner

        from corpus_forge.cli import app

        def _boom(*a: Any, **k: Any):
            raise JoinError("missing schema")

        monkeypatch.setattr("corpus_forge.setup.run_join", _boom)

        result = CliRunner().invoke(
            app,
            ["setup", "--join", _DSN, "--non-interactive", "--config-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "missing schema" in result.output
