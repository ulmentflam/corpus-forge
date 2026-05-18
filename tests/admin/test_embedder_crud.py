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
