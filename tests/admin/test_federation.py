"""Tests for :mod:`corpus_forge.admin.federation` (RFC fleet-3 item 3).

The three verbs (``config publish`` / ``config pull`` / ``config diff``)
ride the existing ``config`` sub-app. These tests pin:

- ``publish`` happy path + state-file write + version-conflict refusal
  message + deny-list refusal (poisoned ``shared_scope_dict``).
- ``pull`` dry-run writes NOTHING (file unchanged, no ``.bak``) and
  ``--apply`` writes the merged text, creates a ``.bak``, records the
  pulled version.
- ``diff`` shows the version numbers.
- "nothing published" shapes (pull + diff, exit 0).
- SQLite backend → clean ``federation requires the postgres backend``
  error, no traceback.

Backends are stubbed (no DB); config + state-file live in a tmp dir via
``CORPUS_FORGE_CONFIG`` (the #104 isolation pattern).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_forge.admin import federation as fed
from corpus_forge.backends.base import (
    FederationUnsupported,
    SharedConfigVersionConflict,
)
from corpus_forge.cli import app

runner = CliRunner()


# A minimal postgres-backed config so ``Config.load`` succeeds and
# ``host_id()`` resolves without touching the filesystem default. The
# stub backend replaces the real one, so the DSN never gets dialled.
_BASE_CONFIG = """\
# corpus-forge config (federation test fixture)
[backend]
kind = "postgres"
dsn = "postgresql://localhost/corpus"
schema = "corpus"

[daemon]
host_id = "test-host"

[[datasets]]
name = "notes"
kind = "text"
sources = [{plugin = "filesystem", root = "/tmp/notes", chunker = "markdown"}]

[[embedders]]
name = "qwen3_8b"
provider = "sentence_transformers"
model_id = "stale-model-id"
dimension = 4096
normalize = true
distance = "cosine"
active = true

[retrieval]
default_k = 10
"""


class _StubBackend:
    """Records publish calls; serves a canned ``get_shared_config``."""

    def __init__(
        self,
        *,
        shared: tuple[int, dict] | None = None,
        publish_version: int | None = None,
        publish_exc: Exception | None = None,
    ) -> None:
        self._shared = shared
        self._publish_version = publish_version
        self._publish_exc = publish_exc
        self.put_calls: list[dict[str, Any]] = []
        self.closed = False

    def migrate(self) -> None:  # called by _build_backend → patched out
        pass

    def get_shared_config(self) -> tuple[int, dict] | None:
        return self._shared

    def put_shared_config(self, body: dict, expected_version: int, published_by: str) -> int:
        self.put_calls.append(
            {"body": body, "expected_version": expected_version, "published_by": published_by}
        )
        if self._publish_exc is not None:
            raise self._publish_exc
        assert self._publish_version is not None
        return self._publish_version

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed the fixture config and redirect ``CORPUS_FORGE_CONFIG``."""

    path = tmp_path / "config.toml"
    path.write_text(_BASE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(path))
    return path


def _patch_backend(monkeypatch: pytest.MonkeyPatch, backend: _StubBackend) -> None:
    monkeypatch.setattr(fed, "_build_backend", lambda config: backend)


# ── state-file helpers ────────────────────────────────────────────────────


def test_state_path_honours_config_env(cfg: Path) -> None:
    assert fed.state_path() == cfg.parent / "federation-state.json"


def test_read_last_pulled_version_absent_is_zero(cfg: Path) -> None:
    assert fed.read_last_pulled_version() == 0


def test_state_round_trip(cfg: Path) -> None:
    fed.write_last_pulled_version(7)
    assert fed.read_last_pulled_version() == 7
    raw = json.loads(fed.state_path().read_text(encoding="utf-8"))
    assert raw == {"last_pulled_version": 7}


def test_read_last_pulled_version_corrupt_is_zero(cfg: Path) -> None:
    fed.state_path().write_text("not json{", encoding="utf-8")
    assert fed.read_last_pulled_version() == 0


# ── deny-list scan ────────────────────────────────────────────────────────


def test_scan_for_denied_key_clean() -> None:
    assert fed._scan_for_denied_key({"datasets": [{"name": "n", "kind": "text"}]}) is None


def test_scan_for_denied_key_flags_secret() -> None:
    found = fed._scan_for_denied_key({"embedders": [{"name": "e", "api_key_env": "X"}]})
    assert found == "$.embedders[0].api_key_env"


# ── publish ────────────────────────────────────────────────────────────────


def test_publish_happy_path_writes_state(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(publish_version=1)
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "publish", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "published"
    assert payload["version"] == 1
    assert payload["published_by"] == "test-host"

    # First publish uses expected_version=0 (never pulled).
    assert backend.put_calls[0]["expected_version"] == 0
    assert backend.put_calls[0]["published_by"] == "test-host"
    # Body carries the shared scope (dataset names, embedder defs).
    body = backend.put_calls[0]["body"]
    assert body["datasets"] == [{"name": "notes", "kind": "text"}]
    # State recorded the new version.
    assert fed.read_last_pulled_version() == 1


def test_publish_uses_last_pulled_as_expected_version(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fed.write_last_pulled_version(3)
    backend = _StubBackend(publish_version=4)
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "publish", "--json"])
    assert result.exit_code == 0, result.output
    assert backend.put_calls[0]["expected_version"] == 3
    assert fed.read_last_pulled_version() == 4


def test_publish_conflict_says_pull_first(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fed.write_last_pulled_version(2)
    backend = _StubBackend(
        publish_exc=SharedConfigVersionConflict(),
        shared=(5, {"datasets": []}),
    )
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "publish", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "conflict"
    assert payload["published_version"] == 5
    assert payload["last_pulled_version"] == 2
    assert "config pull" in payload["error"]
    # State must NOT advance on a refused publish.
    assert fed.read_last_pulled_version() == 2


def test_publish_conflict_human_message(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(
        publish_exc=SharedConfigVersionConflict(),
        shared=(5, {"datasets": []}),
    )
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "publish"])
    assert result.exit_code == 1
    assert "pull" in result.output


def test_publish_deny_list_refusal(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a poisoned body so the runtime re-scan trips even though the
    # structural invariant makes this impossible in practice.
    monkeypatch.setattr(
        fed,
        "shared_scope_dict",
        lambda config: {"embedders": [{"name": "e", "dsn": "postgres://leak"}]},
    )
    backend = _StubBackend(publish_version=1)
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "publish", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "denied"
    assert payload["offending_key"] == "$.embedders[0].dsn"
    # Never reached the backend.
    assert backend.put_calls == []


def test_publish_sqlite_clean_error(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(publish_exc=FederationUnsupported())
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "publish"])
    assert result.exit_code == 1
    assert "federation requires the postgres backend" in result.output
    # Clean error — not a traceback.
    assert "Traceback" not in result.output


# ── pull ────────────────────────────────────────────────────────────────────


def _shared_body() -> dict:
    # Overwrites the stale model_id; adds a retrieval key.
    return {
        "datasets": [{"name": "notes", "kind": "text"}],
        "embedders": [{"name": "qwen3_8b", "model_id": "Qwen/Qwen3-Embedding-8B"}],
        "retrieval": {"fusion": "rrf"},
    }


def test_pull_dry_run_writes_nothing(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = cfg.read_text(encoding="utf-8")
    mtime_before = cfg.stat().st_mtime_ns
    backend = _StubBackend(shared=(2, _shared_body()))
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "pull", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "diff"
    assert payload["published_version"] == 2
    assert payload["changed"] is True

    # Nothing written: content + mtime unchanged, no .bak, no state file.
    assert cfg.read_text(encoding="utf-8") == original
    assert cfg.stat().st_mtime_ns == mtime_before
    assert not cfg.with_name(cfg.name + ".bak").exists()
    assert not fed.state_path().exists()


def test_pull_apply_writes_merged_and_backup(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = cfg.read_text(encoding="utf-8")
    backend = _StubBackend(shared=(2, _shared_body()))
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "pull", "--apply", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "applied"
    assert payload["version"] == 2

    # Merged text landed: shared key converged, local comment survived.
    merged = cfg.read_text(encoding="utf-8")
    assert "Qwen/Qwen3-Embedding-8B" in merged
    assert "stale-model-id" not in merged
    assert "federation test fixture" in merged  # comment preserved
    # Backup carries the pre-merge text.
    bak = cfg.with_name(cfg.name + ".bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original
    # State recorded.
    assert fed.read_last_pulled_version() == 2


def test_pull_nothing_published(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(shared=None)
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "pull", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "nothing_published"


def test_pull_sqlite_clean_error(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom(_StubBackend):
        def get_shared_config(self) -> tuple[int, dict] | None:
            raise FederationUnsupported()

    _patch_backend(monkeypatch, _Boom())
    result = runner.invoke(app, ["config", "pull"])
    assert result.exit_code == 1
    assert "federation requires the postgres backend" in result.output
    assert "Traceback" not in result.output


# ── diff ────────────────────────────────────────────────────────────────────


def test_diff_shows_versions(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fed.write_last_pulled_version(1)
    backend = _StubBackend(shared=(2, _shared_body()))
    _patch_backend(monkeypatch, backend)

    result = runner.invoke(app, ["config", "diff", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "diff"
    assert payload["published_version"] == 2
    assert payload["local_version"] == 1
    assert payload["changed"] is True
    assert any("model_id" in line for line in payload["diff"])
    # diff is inspection-only — writes nothing.
    assert not cfg.with_name(cfg.name + ".bak").exists()


def test_diff_human_panel_shows_versions(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fed.write_last_pulled_version(1)
    backend = _StubBackend(shared=(2, _shared_body()))
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "diff"])
    assert result.exit_code == 0, result.output
    assert "v2" in result.output
    assert "v1" in result.output


def test_diff_nothing_published(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(shared=None)
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "diff", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "nothing_published"


def test_diff_no_changes(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A body that merges to the same text → changed is False.
    from corpus_forge.config import Config
    from corpus_forge.config_scope import merge_shared_scope, shared_scope_dict

    config = Config.load()
    body = shared_scope_dict(config)
    # Pre-merge the local config so a pull would be a no-op.
    cfg.write_text(merge_shared_scope(cfg.read_text(encoding="utf-8"), body), encoding="utf-8")

    backend = _StubBackend(shared=(1, body))
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "diff", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["changed"] is False


# ── human (non-agent) render paths ────────────────────────────────────────


def test_publish_human_ok(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(publish_version=1)
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "publish"])
    assert result.exit_code == 0, result.output
    assert "v1" in result.output
    assert "test-host" in result.output


def test_publish_conflict_get_failure_fallback(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # When the post-conflict get_shared_config itself fails, the message
    # falls back to expected_version + 1 rather than raising.
    fed.write_last_pulled_version(2)

    class _Boom(_StubBackend):
        def get_shared_config(self) -> tuple[int, dict] | None:
            raise RuntimeError("transient")

    backend = _Boom(publish_exc=SharedConfigVersionConflict())
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "publish", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "conflict"
    assert payload["published_version"] == 3  # expected (2) + 1 fallback


def test_pull_apply_human_output(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(shared=(2, _shared_body()))
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "pull", "--apply"])
    assert result.exit_code == 0, result.output
    assert "v2" in result.output


def test_pull_human_dry_run_shows_apply_hint(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(shared=(2, _shared_body()))
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "pull"])
    assert result.exit_code == 0, result.output
    assert "--apply" in result.output


def test_diff_human_no_changes(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.config import Config
    from corpus_forge.config_scope import merge_shared_scope, shared_scope_dict

    config = Config.load()
    body = shared_scope_dict(config)
    cfg.write_text(merge_shared_scope(cfg.read_text(encoding="utf-8"), body), encoding="utf-8")
    backend = _StubBackend(shared=(1, body))
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "diff"])
    assert result.exit_code == 0, result.output
    assert "nothing to pull" in result.output


def test_pull_human_nothing_published(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _StubBackend(shared=None)
    _patch_backend(monkeypatch, backend)
    result = runner.invoke(app, ["config", "pull"])
    assert result.exit_code == 0
    assert "nothing published" in result.output


def test_read_last_pulled_version_oserror_is_zero(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A state path that exists but can't be read → 0 (treated as "never").
    fed.write_last_pulled_version(9)

    def _boom(self: Path, *a: Any, **k: Any) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert fed.read_last_pulled_version() == 0


def test_read_last_pulled_version_non_int_is_zero(cfg: Path) -> None:
    fed.state_path().write_text(json.dumps({"last_pulled_version": "oops"}), encoding="utf-8")
    assert fed.read_last_pulled_version() == 0


def test_publish_deny_list_human_render(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fed, "shared_scope_dict", lambda config: {"x": [{"vault_root": "/leak"}]})
    _patch_backend(monkeypatch, _StubBackend(publish_version=1))
    result = runner.invoke(app, ["config", "publish"])
    assert result.exit_code == 2
    assert "refusing to publish" in result.output


def test_missing_config_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(tmp_path / "absent.toml"))
    result = runner.invoke(app, ["config", "publish"])
    assert result.exit_code == 2
    assert "No configuration found" in result.output


# ── helper coverage (real bodies, no CLI) ────────────────────────────────


def test_build_backend_postgres_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_forge.backends.postgres as pg_mod

    created: dict[str, Any] = {}

    class _FakePg:
        def __init__(self, *, dsn: str, schema: str) -> None:
            created["dsn"] = dsn
            created["schema"] = schema
            self.migrated = False
            self.closed = False

        def migrate(self) -> None:
            self.migrated = True

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(pg_mod, "PostgresBackend", _FakePg)

    class _Cfg:
        class backend:
            kind = "postgres"
            dsn = "postgresql://x/y"
            schema = "corpus"

    backend = fed._build_backend(_Cfg())
    assert created == {"dsn": "postgresql://x/y", "schema": "corpus"}
    assert backend.migrated is True
    fed._close_backend(backend)
    assert backend.closed is True


def test_build_backend_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_forge.backends.sqlite as sq_mod

    class _FakeSqlite:
        def __init__(self, *, path: str, schema: str) -> None:
            self.path = path

        def migrate(self) -> None:
            pass

    monkeypatch.setattr(sq_mod, "SQLiteBackend", _FakeSqlite)

    class _Cfg:
        class backend:
            kind = "sqlite"
            dsn = "/tmp/x.db"
            schema = "corpus"

    backend = fed._build_backend(_Cfg())
    assert isinstance(backend, _FakeSqlite)


def test_close_backend_without_close_is_noop() -> None:
    fed._close_backend(object())  # no close attr → silently returns
