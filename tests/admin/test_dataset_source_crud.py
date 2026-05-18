"""Tests for :mod:`corpus_forge.admin.dataset` + :mod:`source` (Phase L Wave 7).

We seed a config with one dataset + one source, then exercise CRUD via
the Typer ``CliRunner``.  Backend reads (``_doc_count``, ``--drop-vectors``)
are mocked since the test env has no live Postgres.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomlkit
from typer.testing import CliRunner

from corpus_forge.admin import dataset as dataset_admin
from corpus_forge.admin import source as source_admin

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
"""


@pytest.fixture
def fake_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(_CONFIG, encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(p))
    return p


# ── dataset list / get ──────────────────────────────────────────────────


def test_dataset_list_renders_rows(fake_config: Path, monkeypatch) -> None:
    backend = MagicMock()
    backend._execute.return_value = [{"n": 12}]
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(dataset_admin.dataset_app, ["list"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "default" in combined


def test_dataset_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``[[datasets]]`` blocks — list should warn but exit 0."""

    cfg = tmp_path / "config.toml"
    cfg.write_text('[backend]\nkind = "sqlite"\ndsn = "/tmp/x"\n[daemon]\n', encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
    result = runner.invoke(dataset_admin.dataset_app, ["list"])
    assert result.exit_code == 0


def test_dataset_get_prints_json(fake_config: Path, monkeypatch) -> None:
    backend = MagicMock()
    backend._execute.return_value = [{"n": 5}]
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(dataset_admin.dataset_app, ["get", "default"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "default"
    assert payload["kind"] == "text"


def test_dataset_get_unknown_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(dataset_admin.dataset_app, ["get", "no-such"])
    assert result.exit_code == 1


# ── dataset add / remove ────────────────────────────────────────────────


def test_dataset_add_appends_entry(fake_config: Path) -> None:
    inputs = "\n".join(
        [
            "text",  # kind
            "filesystem",  # plugin
            "/tmp/journal",  # root
            "markdown",  # chunker
        ]
    )
    result = runner.invoke(dataset_admin.dataset_app, ["add", "journal"], input=inputs)
    assert result.exit_code == 0, result.stdout

    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    names = [d.get("name") for d in doc.get("datasets") or []]
    assert "default" in names
    assert "journal" in names


def test_dataset_add_duplicate_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(dataset_admin.dataset_app, ["add", "default"])
    assert result.exit_code == 1


def test_dataset_remove_drops_entry(fake_config: Path) -> None:
    # Add a second dataset first so we don't end up with an empty list.
    inputs = "text\nfilesystem\n/tmp/extra\nmarkdown\n"
    runner.invoke(dataset_admin.dataset_app, ["add", "extra"], input=inputs)

    result = runner.invoke(dataset_admin.dataset_app, ["remove", "extra", "--yes"])
    assert result.exit_code == 0

    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    names = [d.get("name") for d in doc.get("datasets") or []]
    assert "extra" not in names
    assert "default" in names


def test_dataset_remove_unknown_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(dataset_admin.dataset_app, ["remove", "no-such", "--yes"])
    assert result.exit_code == 1


# ── source list / add / remove ──────────────────────────────────────────


def test_source_list_renders_rows(fake_config: Path) -> None:
    result = runner.invoke(source_admin.source_app, ["list", "-d", "default"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "filesystem" in combined
    assert "/tmp/notes" in combined


def test_source_list_unknown_dataset_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(source_admin.source_app, ["list", "-d", "no-such"])
    assert result.exit_code == 1


def test_source_add_round_trip(fake_config: Path) -> None:
    # Decline ingest at the end so the test stays hermetic.
    inputs = "\n".join(
        [
            "filesystem",  # plugin
            "/tmp/second",  # root
            "markdown",  # chunker
            "n",  # ingest now? -> no
        ]
    )
    result = runner.invoke(source_admin.source_app, ["add", "-d", "default"], input=inputs)
    assert result.exit_code == 0, result.stdout

    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    default_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "default")
    sources = list(default_ds.get("sources") or [])
    assert len(sources) == 2
    assert any(s.get("root") == "/tmp/second" for s in sources)


def test_source_remove_drops_entry(fake_config: Path) -> None:
    # Use `--no-ingest` to skip the ingest prompt entirely.
    inputs = "filesystem\n/tmp/extra\nmarkdown\n"
    runner.invoke(
        source_admin.source_app,
        ["add", "-d", "default", "--no-ingest"],
        input=inputs,
    )
    # Now remove index 0 (the original source).
    result = runner.invoke(source_admin.source_app, ["remove", "-d", "default", "0", "--yes"])
    assert result.exit_code == 0
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    default_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "default")
    sources = list(default_ds.get("sources") or [])
    assert len(sources) == 1
    assert sources[0].get("root") == "/tmp/extra"


def test_source_remove_out_of_range_exits_nonzero(fake_config: Path) -> None:
    result = runner.invoke(source_admin.source_app, ["remove", "-d", "default", "99", "--yes"])
    assert result.exit_code == 1
