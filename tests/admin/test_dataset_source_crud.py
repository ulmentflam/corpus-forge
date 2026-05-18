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


# ─── coverage-push branches (low-level units, do not gate CI) ───────────
#
# These exercise the alternate branches in dataset.py / source.py that the
# happy-path crud tests don't hit:
#   - dataset.py:    list with no backend, _doc_count backend failure,
#                    get with backend failure, markdown_vault/claude_code
#                    branches in add, abort-confirm in remove, drop_vectors
#                    success + no-rows + failure.
#   - source.py:     markdown_vault/claude_code/opencode branches in add,
#                    config-invalid after add/remove, source-unknown in
#                    add/remove, ingest-now confirm path with mocked
#                    run_attached.


def test_dataset_list_with_no_backend_renders_q_marks(fake_config: Path, monkeypatch) -> None:
    """When ``_get_backend`` raises, ``list`` still renders rows with `?`."""

    def _boom(_cfg):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(dataset_admin, "_get_backend", _boom)

    result = runner.invoke(dataset_admin.dataset_app, ["list"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "default" in combined


def test_dataset_list_doc_count_failure_shows_q(fake_config: Path, monkeypatch) -> None:
    """`_doc_count` returns ``"?"`` on backend exception (line 57-58)."""

    backend = MagicMock()
    backend._execute.side_effect = RuntimeError("query failed")
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(dataset_admin.dataset_app, ["list"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "default" in combined
    assert "?" in combined


def test_dataset_get_with_backend_failure_shows_q(fake_config: Path, monkeypatch) -> None:
    """`cmd_get` swallows backend failure and emits ``"document_count": "?"``."""

    def _boom(_cfg):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(dataset_admin, "_get_backend", _boom)

    result = runner.invoke(dataset_admin.dataset_app, ["get", "default"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["document_count"] == "?"


def test_dataset_add_markdown_vault_branch(fake_config: Path) -> None:
    """`add` with ``markdown_vault`` plugin uses ``vault_root`` field."""

    inputs = "\n".join(
        [
            "text",  # kind
            "markdown_vault",  # plugin
            "/tmp/vault",  # vault_root
        ]
    )
    result = runner.invoke(dataset_admin.dataset_app, ["add", "vault_ds"], input=inputs)
    assert result.exit_code == 0, result.stdout
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    new_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "vault_ds")
    sources = list(new_ds.get("sources") or [])
    assert sources[0].get("vault_root") == "/tmp/vault"
    assert sources[0].get("plugin") == "markdown_vault"


def test_dataset_add_claude_code_branch(fake_config: Path) -> None:
    """`add` with ``claude_code`` plugin uses ``projects_root`` field."""

    inputs = "\n".join(
        [
            "chat",  # kind
            "claude_code",  # plugin
            "/tmp/projects",  # projects_root
        ]
    )
    result = runner.invoke(dataset_admin.dataset_app, ["add", "claude_ds"], input=inputs)
    assert result.exit_code == 0, result.stdout
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    new_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "claude_ds")
    sources = list(new_ds.get("sources") or [])
    assert sources[0].get("projects_root") == "/tmp/projects"
    assert sources[0].get("chunker") == "conversation"


def test_dataset_remove_aborts_on_confirm_no(fake_config: Path) -> None:
    """When the user says ``n`` to the confirm prompt, exit 0 and nothing removed."""

    result = runner.invoke(dataset_admin.dataset_app, ["remove", "default"], input="n\n")
    # ``raise typer.Exit(code=0)`` from the abort path.
    assert result.exit_code == 0
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    names = [d.get("name") for d in doc.get("datasets") or []]
    assert "default" in names


@pytest.fixture
def two_dataset_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Config seeded with two datasets so removing one keeps the schema valid."""

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[backend]\nkind = "sqlite"\ndsn = "/tmp/test.db"\n'
        "[daemon]\n"
        '[[datasets]]\nname = "primary"\nkind = "text"\n'
        'sources = [{plugin = "filesystem", root = "/tmp/p", chunker = "markdown"}]\n'
        '[[datasets]]\nname = "secondary"\nkind = "text"\n'
        'sources = [{plugin = "filesystem", root = "/tmp/s", chunker = "markdown"}]\n'
        '[[embedders]]\nname = "qwen3_8b"\nprovider = "sentence_transformers"\n'
        'model_id = "Qwen/Qwen3-Embedding-8B"\ndimension = 4096\n'
        'normalize = true\ndistance = "cosine"\nactive = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
    return cfg


def test_dataset_remove_drop_vectors_no_rows(two_dataset_config: Path, monkeypatch) -> None:
    """`--drop-vectors` against an unregistered dataset prints the "no rows" note."""

    backend = MagicMock()
    # ``rows`` empty → take the no-rows branch.
    backend._execute.return_value = []
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(
        dataset_admin.dataset_app,
        ["remove", "secondary", "--yes", "--drop-vectors"],
    )
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "No backend rows" in combined or "never registered" in combined


def test_dataset_remove_drop_vectors_success(two_dataset_config: Path, monkeypatch) -> None:
    """`--drop-vectors` happy path issues DELETE statements via the backend."""

    backend = MagicMock()
    # SELECT returns one row with id=42; subsequent DELETEs return [].
    backend._execute.side_effect = [
        [{"id": 42}],  # SELECT id FROM ...
        [],  # DELETE FROM documents ...
        [],  # DELETE FROM datasets ...
    ]
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(
        dataset_admin.dataset_app,
        ["remove", "secondary", "--yes", "--drop-vectors"],
    )
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Dropped backend rows" in combined
    # Three SQL calls total — SELECT + 2 DELETEs.
    assert backend._execute.call_count == 3


def test_dataset_remove_drop_vectors_postgres_branch(tmp_path: Path, monkeypatch) -> None:
    """The postgres branch issues ``corpus.`` schema-prefixed DELETEs."""

    # Seed a postgres-flavoured config with two datasets so removing one
    # keeps the schema valid.
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[backend]\nkind = "postgres"\ndsn = "postgresql://u:p@h/db"\n'
        "[daemon]\n"
        '[[datasets]]\nname = "pg_ds"\nkind = "text"\n'
        'sources = [{plugin = "filesystem", root = "/tmp/x", chunker = "markdown"}]\n'
        '[[datasets]]\nname = "keepme"\nkind = "text"\n'
        'sources = [{plugin = "filesystem", root = "/tmp/y", chunker = "markdown"}]\n'
        '[[embedders]]\nname = "e1"\nprovider = "openai"\n'
        'model_id = "text-embedding-3-small"\ndimension = 1536\n'
        'normalize = true\ndistance = "cosine"\nactive = true\n'
        'base_url = "http://localhost:11434/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))

    backend = MagicMock()
    backend._execute.side_effect = [
        [{"id": 7}],  # SELECT id FROM corpus.datasets ...
        [],  # DELETE FROM corpus.documents ...
        [],  # DELETE FROM corpus.datasets ...
    ]
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(
        dataset_admin.dataset_app,
        ["remove", "pg_ds", "--yes", "--drop-vectors"],
    )
    assert result.exit_code == 0
    # The postgres branch uses ``corpus.`` schema-qualified table names.
    sql_text = " ".join(call.args[0] for call in backend._execute.call_args_list)
    assert "corpus.datasets" in sql_text
    assert "corpus.documents" in sql_text


def test_dataset_remove_drop_vectors_swallows_failure(fake_config: Path, monkeypatch) -> None:
    """A backend exception during ``--drop-vectors`` is downgraded to a warn."""

    backend = MagicMock()
    backend._execute.side_effect = RuntimeError("backend exploded")
    monkeypatch.setattr(dataset_admin, "_get_backend", lambda _cfg: backend)

    result = runner.invoke(
        dataset_admin.dataset_app,
        ["remove", "default", "--yes", "--drop-vectors"],
    )
    # The remove step still succeeds even when the vector drop fails.
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Vector / row drop failed" in combined


# ── source.py coverage push ─────────────────────────────────────────────


def test_source_add_unknown_dataset_exits_nonzero(fake_config: Path) -> None:
    """``source add -d <unknown>`` exits 1."""

    result = runner.invoke(
        source_admin.source_app,
        ["add", "-d", "no-such-dataset", "--no-ingest"],
        input="filesystem\n/tmp/x\nmarkdown\n",
    )
    assert result.exit_code == 1


def test_source_add_markdown_vault_branch(fake_config: Path) -> None:
    """``source add`` with ``markdown_vault`` collects ``vault_root``."""

    inputs = "\n".join(
        [
            "markdown_vault",  # plugin
            "/tmp/vault",  # vault_root
        ]
    )
    result = runner.invoke(
        source_admin.source_app,
        ["add", "-d", "default", "--no-ingest"],
        input=inputs,
    )
    assert result.exit_code == 0, result.stdout
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    default_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "default")
    sources = list(default_ds.get("sources") or [])
    assert any(s.get("vault_root") == "/tmp/vault" for s in sources)


def test_source_add_claude_code_branch(fake_config: Path) -> None:
    """``source add`` with ``claude_code`` collects ``projects_root``."""

    inputs = "\n".join(
        [
            "claude_code",  # plugin
            "/tmp/projects",  # projects_root
        ]
    )
    result = runner.invoke(
        source_admin.source_app,
        ["add", "-d", "default", "--no-ingest"],
        input=inputs,
    )
    assert result.exit_code == 0, result.stdout
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    default_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "default")
    sources = list(default_ds.get("sources") or [])
    assert any(s.get("projects_root") == "/tmp/projects" for s in sources)


def test_source_add_opencode_branch(fake_config: Path) -> None:
    """``source add`` with ``opencode`` collects ``storage_root``."""

    inputs = "\n".join(
        [
            "opencode",  # plugin
            "/tmp/opencode-storage",  # storage_root
        ]
    )
    result = runner.invoke(
        source_admin.source_app,
        ["add", "-d", "default", "--no-ingest"],
        input=inputs,
    )
    assert result.exit_code == 0, result.stdout
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    default_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "default")
    sources = list(default_ds.get("sources") or [])
    assert any(s.get("storage_root") == "/tmp/opencode-storage" for s in sources)


def test_source_add_ingest_now_path_invokes_run_attached(fake_config: Path, monkeypatch) -> None:
    """When the user says ``y`` to ``Ingest now?``, ``run_attached`` is called."""

    captured: dict = {}

    def _fake_run_attached(argv, *, component, background=False, **_kw):
        captured["argv"] = argv
        captured["component"] = component
        captured["background"] = background
        return 0

    # Patch via the module the source.py code imports lazily.
    from corpus_forge.admin import foreground as _fg_mod

    monkeypatch.setattr(_fg_mod, "run_attached", _fake_run_attached)

    inputs = "\n".join(
        [
            "filesystem",
            "/tmp/with-ingest",
            "markdown",
            "y",  # ingest now? -> yes
        ]
    )
    result = runner.invoke(
        source_admin.source_app,
        ["add", "-d", "default"],
        input=inputs,
    )
    assert result.exit_code == 0, result.stdout
    assert captured["component"] == "ingest"
    assert "ingest" in captured["argv"]
    assert "--once" in captured["argv"]


def test_source_add_ingest_now_run_attached_nonzero_warns(fake_config: Path, monkeypatch) -> None:
    """A non-zero rc from the ingest run surfaces a warning, exit 0."""

    def _fake_run_attached(argv, *, component, background=False, **_kw):
        return 3  # arbitrary non-zero

    from corpus_forge.admin import foreground as _fg_mod

    monkeypatch.setattr(_fg_mod, "run_attached", _fake_run_attached)

    inputs = "filesystem\n/tmp/ingest-fail\nmarkdown\ny\n"
    result = runner.invoke(
        source_admin.source_app,
        ["add", "-d", "default"],
        input=inputs,
    )
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "rc=3" in combined or "exited with" in combined


def test_source_remove_aborts_on_confirm_no(fake_config: Path) -> None:
    """``remove`` without ``--yes`` and prompt=no exits 0 and no removal."""

    result = runner.invoke(
        source_admin.source_app,
        ["remove", "-d", "default", "0"],
        input="n\n",
    )
    assert result.exit_code == 0
    doc = tomlkit.parse(fake_config.read_text(encoding="utf-8"))
    default_ds = next(d for d in doc.get("datasets") or [] if d.get("name") == "default")
    assert len(list(default_ds.get("sources") or [])) == 1


def test_source_remove_unknown_dataset_exits_nonzero(fake_config: Path) -> None:
    """``remove -d <unknown>`` exits 1."""

    result = runner.invoke(
        source_admin.source_app,
        ["remove", "-d", "no-such", "0", "--yes"],
    )
    assert result.exit_code == 1


def test_source_list_no_sources_warns(tmp_path: Path, monkeypatch) -> None:
    """Empty ``sources`` list inside a known dataset → warn but exit 0."""

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[backend]\nkind = "sqlite"\ndsn = "/tmp/x"\n'
        "[daemon]\n"
        '[[datasets]]\nname = "empty_ds"\nkind = "text"\nsources = []\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg))
    result = runner.invoke(source_admin.source_app, ["list", "-d", "empty_ds"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "No sources" in combined
