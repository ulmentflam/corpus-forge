"""Tests for :mod:`corpus_forge.admin.embedder` (Phase L Wave 7).

We exercise the CRUD verbs over a tmp ``config.toml`` with a fixture
embedder, mocking the embedder registry's encode path so the tests
don't need to download a real model.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import tomlkit
from typer.testing import CliRunner

from corpus_forge.admin import embedder as embedder_admin

runner = CliRunner()


_CONFIG = """\
[backend]
kind = "sqlite"
dsn = "/tmp/test.db"

[daemon]

[[datasets]]
name = "default"
kind = "text"
sources = [{plugin = "filesystem", root = "/tmp/notes", chunker = "markdown"}]

[[embedders]]
name = "qwen3_8b"
provider = "sentence_transformers"
model_id = "Qwen/Qwen3-Embedding-8B"
dimension = 4096
normalize = true
distance = "cosine"
active = true

[[embedders]]
name = "bge_m3"
provider = "sentence_transformers"
model_id = "BAAI/bge-m3"
dimension = 1024
normalize = true
distance = "cosine"
active = false
"""


@pytest.fixture
def fake_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(_CONFIG, encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(p))
    return p


# ── _count_coverage ─────────────────────────────────────────────────────


def test_count_coverage_handles_missing_row() -> None:
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = None
    assert embedder_admin._count_coverage(backend, "x") == 0


def test_count_coverage_returns_int() -> None:
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = {"id": 7, "name": "x"}
    backend.count_existing_embeddings.return_value = 1234
    assert embedder_admin._count_coverage(backend, "x") == 1234


def test_count_coverage_swallows_backend_exception() -> None:
    backend = MagicMock()
    backend.find_embedder_row_by_name.side_effect = RuntimeError("conn refused")
    assert embedder_admin._count_coverage(backend, "x") == "?"


# ── cmd_list ────────────────────────────────────────────────────────────


def test_cli_list_renders_each_embedder(fake_config: Path, monkeypatch) -> None:
    # Patch backend factory to a stub so we don't need a real DB.
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = None
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(embedder_admin.embedder_app, ["list"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "qwen3_8b" in combined
    assert "bge_m3" in combined


def test_cli_list_handles_unreachable_backend(fake_config: Path, monkeypatch) -> None:
    def _explode(_cfg):
        raise RuntimeError("backend down")

    monkeypatch.setattr(embedder_admin, "_get_backend", _explode)
    result = runner.invoke(embedder_admin.embedder_app, ["list"])
    # Should still render rows with "?" coverage.
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "qwen3_8b" in combined


# ── cmd_get ─────────────────────────────────────────────────────────────


def test_cli_get_prints_json_record(fake_config: Path, monkeypatch) -> None:
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = None
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: backend)
    result = runner.invoke(embedder_admin.embedder_app, ["get", "qwen3_8b"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "qwen3_8b"
    assert payload["dimension"] == 4096
    assert payload["active"] is True


def test_cli_get_unknown_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(embedder_admin.embedder_app, ["get", "no-such"])
    assert result.exit_code == 1


# ── cmd_add ─────────────────────────────────────────────────────────────


def test_cli_add_appends_embedder(fake_config: Path, monkeypatch) -> None:
    """Driving the wizard with piped stdin should append a new entry."""

    inputs = "\n".join(
        [
            "sentence_transformers",  # provider
            "BAAI/bge-small-en",  # model_id
            "384",  # dimension
            "y",  # normalize
            "cosine",  # distance
            "",  # (no api_key prompt — sentence_transformers branch)
        ]
    )
    result = runner.invoke(embedder_admin.embedder_app, ["add", "bge_small"], input=inputs)
    assert result.exit_code == 0, result.stdout

    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    names = [e.get("name") for e in doc.get("embedders", [])]
    assert "bge_small" in names


def test_cli_add_duplicate_name_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(embedder_admin.embedder_app, ["add", "qwen3_8b"])
    assert result.exit_code == 1


# ── cmd_remove ──────────────────────────────────────────────────────────


def test_cli_remove_drops_from_config(fake_config: Path) -> None:
    result = runner.invoke(embedder_admin.embedder_app, ["remove", "bge_m3", "--yes"])
    assert result.exit_code == 0
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    names = [e.get("name") for e in doc.get("embedders", [])]
    assert "bge_m3" not in names
    assert "qwen3_8b" in names


def test_cli_remove_aborts_without_yes(fake_config: Path) -> None:
    # Pipe "n" to the confirm prompt.
    result = runner.invoke(embedder_admin.embedder_app, ["remove", "bge_m3"], input="n\n")
    assert result.exit_code == 0
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    names = [e.get("name") for e in doc.get("embedders", [])]
    assert "bge_m3" in names, "embedder should still be present after 'n'"


def test_cli_remove_unknown_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(embedder_admin.embedder_app, ["remove", "no-such", "--yes"])
    assert result.exit_code == 1


def test_cli_remove_drop_vectors_calls_backend(fake_config: Path, monkeypatch) -> None:
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = {
        "id": 1,
        "name": "bge_m3",
        "table_name": "embeddings_bge_m3",
    }
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: backend)
    result = runner.invoke(
        embedder_admin.embedder_app, ["remove", "bge_m3", "--yes", "--drop-vectors"]
    )
    assert result.exit_code == 0
    backend._execute.assert_called()


# ── cmd_set_active ──────────────────────────────────────────────────────


def test_cli_set_active_flips_flags(fake_config: Path, monkeypatch) -> None:
    # No drift means compare_active returns [].
    monkeypatch.setattr("corpus_forge.embedders.fingerprint.compare_active", lambda *a, **k: [])
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: MagicMock())

    result = runner.invoke(embedder_admin.embedder_app, ["set-active", "bge_m3"])
    assert result.exit_code == 0, result.stdout
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    actives = {e["name"]: bool(e["active"]) for e in doc.get("embedders", [])}
    assert actives["qwen3_8b"] is False
    assert actives["bge_m3"] is True


def test_cli_set_active_unknown_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(embedder_admin.embedder_app, ["set-active", "no-such"])
    assert result.exit_code == 1


def test_cli_set_active_triggers_drift_check(fake_config: Path, monkeypatch) -> None:
    """When ``compare_active`` returns drifts, the prompt is offered."""

    from corpus_forge.embedders.fingerprint import EmbedderDrift

    drift = EmbedderDrift(
        name="bge_m3",
        was_model_id="qwen3:8b",
        was_dimension=4096,
        now_model_id="BAAI/bge-m3",
        now_dimension=1024,
        chunks_to_rerun=10,
        est_seconds=1.0,
        fingerprint_was="a" * 8,
        fingerprint_now="b" * 8,
    )

    monkeypatch.setattr(
        "corpus_forge.embedders.fingerprint.compare_active",
        lambda *a, **k: [drift],
    )
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: MagicMock())
    # Bypass the prompt — pretend the user picked "skip".
    monkeypatch.setattr(
        "corpus_forge.embedders.drift_prompt.prompt_for_drift",
        lambda drifts, **kw: "skip",
    )

    result = runner.invoke(embedder_admin.embedder_app, ["set-active", "bge_m3"])
    assert result.exit_code == 0


# ── cmd_test (run_embedder_smoke) ───────────────────────────────────────


def test_run_embedder_smoke_returns_outcome(fake_config: Path, monkeypatch) -> None:
    """Encode is mocked; we just need to see the dim + timing flow."""

    fake_embedder = MagicMock()
    fake_embedder.encode.return_value = np.zeros((1, 4096), dtype=np.float32)
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.registry.register",
        lambda **kw: fake_embedder,
    )
    outcome = embedder_admin.run_embedder_smoke("qwen3_8b")
    assert outcome.name == "qwen3_8b"
    assert outcome.dim == 4096


def test_cli_test_unknown_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(embedder_admin.embedder_app, ["test", "no-such"])
    assert result.exit_code == 1


def test_cli_test_reports_dim_and_timing(fake_config: Path, monkeypatch) -> None:
    fake_embedder = MagicMock()
    fake_embedder.encode.return_value = np.zeros((1, 4096), dtype=np.float32)
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.registry.register",
        lambda **kw: fake_embedder,
    )
    result = runner.invoke(embedder_admin.embedder_app, ["test", "qwen3_8b"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "dim=4096" in combined


# ── repair-indexes ─────────────────────────────────────────────────────


class _AuditBackend:
    """Minimal stand-in for PostgresBackend used by ``audit_embedder_indexes``.

    ``__class__.__name__`` must be ``PostgresBackend`` so the audit
    doesn't short-circuit (SQLite path returns an empty list).
    """

    def __init__(self, execute_responses):
        self._execute_responses = list(execute_responses)
        self.executed: list[tuple[str, tuple]] = []

    def _execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self._execute_responses.pop(0)


# Make the class name match PostgresBackend so the audit path runs.
_AuditBackend.__name__ = "PostgresBackend"


def test_audit_returns_ok_when_index_matches_small_dim() -> None:
    backend = _AuditBackend(
        [
            # embedders listing
            [{"name": "small", "dimension": 1024, "table_name": "embeddings_small"}],
            # information_schema.tables exists
            [{"exists": 1}],
            # pg_get_indexdef returns the matching definition
            [
                {
                    "def": (
                        "CREATE INDEX embeddings_small_hnsw ON corpus.embeddings_small "
                        "USING hnsw (embedding vector_cosine_ops)"
                    )
                }
            ],
        ]
    )
    rows = embedder_admin.audit_embedder_indexes(backend)
    assert len(rows) == 1
    assert rows[0].status == "OK"
    assert rows[0].dimension == 1024


def test_audit_flags_drift_for_oversized_dim_with_legacy_index() -> None:
    """A dim=4096 embedder whose index is still ``vector_cosine_ops``
    is the exact failure mode the migration + repair tooling
    exists to fix. Audit must surface it as DRIFT.
    """
    backend = _AuditBackend(
        [
            [{"name": "qwen3_4096", "dimension": 4096, "table_name": "embeddings_qwen3_4096"}],
            [{"exists": 1}],
            [
                {
                    "def": (
                        "CREATE INDEX embeddings_qwen3_4096_hnsw ON corpus.embeddings_qwen3_4096 "
                        "USING hnsw (embedding vector_cosine_ops)"
                    )
                }
            ],
        ]
    )
    rows = embedder_admin.audit_embedder_indexes(backend)
    assert len(rows) == 1
    assert rows[0].status == "DRIFT"
    # Target should be the halfvec(4000) projection, not vector_cosine_ops.
    assert "halfvec(4000)" in rows[0].target_indexdef
    assert "halfvec_cosine_ops" in rows[0].target_indexdef


def test_audit_flags_table_missing_when_no_chunks_table() -> None:
    backend = _AuditBackend(
        [
            [{"name": "stub", "dimension": 768, "table_name": "embeddings_stub"}],
            [],  # information_schema returns no row
        ]
    )
    rows = embedder_admin.audit_embedder_indexes(backend)
    assert len(rows) == 1
    assert rows[0].status == "TABLE_MISSING"


def test_audit_flags_missing_index_when_pg_get_indexdef_empty() -> None:
    backend = _AuditBackend(
        [
            [{"name": "halfvec_ok", "dimension": 4096, "table_name": "embeddings_halfvec_ok"}],
            [{"exists": 1}],
            [],  # no index row
        ]
    )
    rows = embedder_admin.audit_embedder_indexes(backend)
    assert len(rows) == 1
    assert rows[0].status == "MISSING"


def test_audit_ok_for_4096_when_halfvec_projection_present() -> None:
    """An already-correct index must report OK so the migration +
    repair tooling don't thrash on idempotent re-runs. The matching
    indexdef uses the ``subvector(...)::halfvec(N)`` truncate-then-cast
    shape required by pgvector when storage is ``vector(dim)`` and
    ``dim > N``.
    """
    backend = _AuditBackend(
        [
            [{"name": "qwen3_4096", "dimension": 4096, "table_name": "embeddings_qwen3_4096"}],
            [{"exists": 1}],
            [
                {
                    "def": (
                        "CREATE INDEX embeddings_qwen3_4096_hnsw "
                        "ON corpus.embeddings_qwen3_4096 "
                        "USING hnsw ("
                        "(subvector(embedding, 1, 4000)::halfvec(4000)) "
                        "halfvec_cosine_ops)"
                    )
                }
            ],
        ]
    )
    rows = embedder_admin.audit_embedder_indexes(backend)
    assert rows[0].status == "OK"


def test_repair_emits_drop_then_create() -> None:
    """The repair helper must drop the stale index BEFORE creating the
    new one — running them in the other order would crash on the
    name collision — AND it must run both inside a single
    connection/transaction so a CREATE failure rolls back the DROP.
    """
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    # Capture SQL through a single mock cursor so the order assertion
    # also verifies the two statements share a connection.
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=None)

    @contextmanager
    def _get_connection_stub():
        yield conn

    backend = MagicMock()
    backend.__class__.__name__ = "PostgresBackend"
    backend._get_connection = _get_connection_stub

    row = embedder_admin.IndexAuditRow(
        name="qwen3_4096",
        dimension=4096,
        table_name="embeddings_qwen3_4096",
        current_indexdef="CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)",
        target_indexdef=(
            "CREATE INDEX ... USING hnsw "
            "((subvector(embedding, 1, 4000)::halfvec(4000)) halfvec_cosine_ops)"
        ),
        status="DRIFT",
    )
    embedder_admin.repair_embedder_index(backend, row)

    # Both statements executed on the same cursor in order.
    assert cur.execute.call_count == 2
    drop_call, create_call = cur.execute.call_args_list
    drop_sql = drop_call.args[0].as_string()
    create_sql = create_call.args[0].as_string()
    assert "DROP INDEX" in drop_sql
    assert "embeddings_qwen3_4096_hnsw" in drop_sql
    assert "CREATE INDEX" in create_sql
    assert "halfvec(4000)" in create_sql
    assert "halfvec_cosine_ops" in create_sql
    assert "subvector(embedding, 1, 4000)" in create_sql

    # And ``commit`` must fire ONCE at the end so the DROP+CREATE
    # is atomic — calling ``conn.commit()`` between them would
    # break the rollback-on-CREATE-failure contract.
    conn.commit.assert_called_once()


def test_cli_repair_dry_run_does_not_apply(monkeypatch) -> None:
    """No ``--apply`` flag → CLI prints the audit + exits non-zero
    when there's drift, but never calls the repair helper."""

    drifted = embedder_admin.IndexAuditRow(
        name="qwen3_4096",
        dimension=4096,
        table_name="embeddings_qwen3_4096",
        current_indexdef="CREATE INDEX ... vector_cosine_ops",
        target_indexdef="CREATE INDEX ... halfvec_cosine_ops",
        status="DRIFT",
    )
    monkeypatch.setattr(embedder_admin, "_load_config", MagicMock)
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: MagicMock())
    monkeypatch.setattr(embedder_admin, "audit_embedder_indexes", lambda _b: [drifted])
    called = []
    monkeypatch.setattr(
        embedder_admin,
        "repair_embedder_index",
        lambda _b, _r: called.append(_r),
    )

    result = runner.invoke(embedder_admin.embedder_app, ["repair-indexes"])
    assert result.exit_code == 1  # drift detected without --apply
    assert called == []


def test_cli_repair_apply_invokes_helper_for_each_drifted_row(monkeypatch) -> None:
    drifted_rows = [
        embedder_admin.IndexAuditRow(
            name=f"e{i}",
            dimension=4096,
            table_name=f"embeddings_e{i}",
            current_indexdef="vector_cosine_ops",
            target_indexdef="halfvec_cosine_ops",
            status="DRIFT",
        )
        for i in range(3)
    ]
    monkeypatch.setattr(embedder_admin, "_load_config", MagicMock)
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: MagicMock())
    monkeypatch.setattr(embedder_admin, "audit_embedder_indexes", lambda _b: drifted_rows)
    called = []
    monkeypatch.setattr(
        embedder_admin,
        "repair_embedder_index",
        lambda _b, _r: called.append(_r.name),
    )

    result = runner.invoke(embedder_admin.embedder_app, ["repair-indexes", "--apply"])
    assert result.exit_code == 0
    assert called == ["e0", "e1", "e2"]


def test_cli_repair_clean_audit_returns_success(monkeypatch) -> None:
    clean_rows = [
        embedder_admin.IndexAuditRow(
            name="small",
            dimension=1024,
            table_name="embeddings_small",
            current_indexdef="CREATE INDEX ... vector_cosine_ops",
            target_indexdef="CREATE INDEX ... vector_cosine_ops",
            status="OK",
        )
    ]
    monkeypatch.setattr(embedder_admin, "_load_config", MagicMock)
    monkeypatch.setattr(embedder_admin, "_get_backend", lambda _cfg: MagicMock())
    monkeypatch.setattr(embedder_admin, "audit_embedder_indexes", lambda _b: clean_rows)

    result = runner.invoke(embedder_admin.embedder_app, ["repair-indexes"])
    assert result.exit_code == 0


def test_audit_short_circuits_on_sqlite_backend() -> None:
    """SQLite has no HNSW index to audit; the function must return
    an empty list immediately rather than try to query pg_class."""

    class _SqliteStub:
        pass

    _SqliteStub.__name__ = "SQLiteBackend"
    assert embedder_admin.audit_embedder_indexes(_SqliteStub()) == []
