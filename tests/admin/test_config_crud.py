"""Tests for :mod:`corpus_forge.admin.config` (Phase L Wave 7).

We seed a tiny TOML config in a tmp dir, point ``CORPUS_FORGE_CONFIG``
at it, and exercise each verb via the Typer ``CliRunner`` plus the
underlying module helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit
from typer.testing import CliRunner

from corpus_forge.admin import config as admin_config

runner = CliRunner()


_BASE_CONFIG = """\
# corpus-forge config (test fixture)
[backend]
kind = "sqlite"
dsn = "/tmp/test.db"

[daemon]
log_level = "INFO"

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
    """Write the fixture config and redirect ``CORPUS_FORGE_CONFIG``."""

    path = tmp_path / "config.toml"
    path.write_text(_BASE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(path))
    return path


# ── path helpers ────────────────────────────────────────────────────────


def test_resolve_config_path_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "x.toml"
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(target))
    assert admin_config.resolve_config_path() == target


def test_resolve_config_path_uses_cf_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CORPUS_FORGE_CONFIG", raising=False)
    target = tmp_path / "x.toml"
    monkeypatch.setenv("CF_CONFIG", str(target))
    assert admin_config.resolve_config_path() == target


def test_resolve_config_path_prefers_corpus_forge_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cf_target = tmp_path / "cf.toml"
    forge_target = tmp_path / "forge.toml"
    monkeypatch.setenv("CF_CONFIG", str(cf_target))
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(forge_target))
    assert admin_config.resolve_config_path() == forge_target


def test_resolve_config_path_explicit_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(tmp_path / "envvar.toml"))
    explicit = tmp_path / "explicit.toml"
    assert admin_config.resolve_config_path(explicit) == explicit


def test_load_toml_document_round_trips(fake_config: Path) -> None:
    doc = admin_config.load_toml_document(fake_config)
    assert doc["backend"]["kind"] == "sqlite"


def test_load_toml_document_missing_file_returns_empty(tmp_path: Path) -> None:
    doc = admin_config.load_toml_document(tmp_path / "nope.toml")
    assert tomlkit.dumps(doc).strip() == ""


def test_write_toml_atomic_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "out.toml"
    doc = tomlkit.parse("a = 1\n")
    admin_config.write_toml_atomic(target, doc)
    assert target.read_text(encoding="utf-8").strip() == "a = 1"


# ── config get ──────────────────────────────────────────────────────────


def test_cli_get_scalar(fake_config: Path) -> None:
    result = runner.invoke(admin_config.config_app, ["get", "backend.kind"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "sqlite"


def test_cli_get_int(fake_config: Path) -> None:
    result = runner.invoke(admin_config.config_app, ["get", "embedders[0].dimension"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "4096"


def test_cli_get_bool(fake_config: Path) -> None:
    result = runner.invoke(admin_config.config_app, ["get", "embedders[0].normalize"])
    assert result.exit_code == 0
    assert result.stdout.strip() in {"true", "True"}


def test_cli_get_unknown_key_exits_one(fake_config: Path) -> None:
    result = runner.invoke(admin_config.config_app, ["get", "no.such.key"])
    assert result.exit_code == 1


def test_cli_get_array_returns_json(fake_config: Path) -> None:
    result = runner.invoke(admin_config.config_app, ["get", "datasets[0].sources"])
    assert result.exit_code == 0
    # Should parse as JSON.
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert parsed[0]["plugin"] == "filesystem"


# ── config set ──────────────────────────────────────────────────────────


def test_cli_set_int_round_trip(fake_config: Path) -> None:
    """set/get round-trip on an int field."""

    rc = runner.invoke(admin_config.config_app, ["set", "embedders[0].dimension", "1024"])
    assert rc.exit_code == 0, rc.stderr or rc.stdout
    rc2 = runner.invoke(admin_config.config_app, ["get", "embedders[0].dimension"])
    assert rc2.stdout.strip() == "1024"


def test_cli_set_bool_round_trip(fake_config: Path) -> None:
    rc = runner.invoke(admin_config.config_app, ["set", "embedders[0].normalize", "false"])
    assert rc.exit_code == 0
    rc2 = runner.invoke(admin_config.config_app, ["get", "embedders[0].normalize"])
    assert rc2.stdout.strip() in {"false", "False"}


def test_cli_set_invalid_value_rolls_back(fake_config: Path) -> None:
    """Bad value (validation fails) → atomic rollback; file untouched."""

    before = fake_config.read_text(encoding="utf-8")
    # ``dimension`` has ``gt=0`` so 0 fails validation; we pass 0 (not
    # -N) because Typer would interpret -N as a missing-flag value.
    rc = runner.invoke(admin_config.config_app, ["set", "embedders[0].dimension", "0"])
    assert rc.exit_code == 1
    after = fake_config.read_text(encoding="utf-8")
    assert before == after, "file should be untouched on invalid set"


def test_cli_set_invalid_kind_rolls_back(fake_config: Path) -> None:
    before = fake_config.read_text(encoding="utf-8")
    rc = runner.invoke(admin_config.config_app, ["set", "backend.kind", "mongodb"])
    assert rc.exit_code == 1
    assert fake_config.read_text(encoding="utf-8") == before


def test_cli_set_new_field_creates_section(fake_config: Path) -> None:
    """``ollama.base_url`` doesn't exist in the fixture; set should create it."""

    rc = runner.invoke(admin_config.config_app, ["set", "ollama.base_url", "http://example:11434"])
    assert rc.exit_code == 0, rc.stderr or rc.stdout
    rc2 = runner.invoke(admin_config.config_app, ["get", "ollama.base_url"])
    assert rc2.stdout.strip() == "http://example:11434"


def test_set_atomic_writer_preserves_comments(fake_config: Path) -> None:
    """tomlkit round-trip should preserve the leading comment."""

    before = fake_config.read_text(encoding="utf-8")
    assert "# corpus-forge config (test fixture)" in before
    admin_config._set_config_value_atomic("embedders[0].dimension", "1024")
    after = fake_config.read_text(encoding="utf-8")
    assert "# corpus-forge config (test fixture)" in after


# ── config unset ────────────────────────────────────────────────────────


def test_cli_unset_optional_field_removes(fake_config: Path) -> None:
    # ``base_url`` is optional on EmbedderConfig (default None) — unset
    # should drop it.  We first set it, then unset.
    admin_config._set_config_value_atomic("embedders[0].base_url", "http://x:1/v1")
    rc = runner.invoke(admin_config.config_app, ["unset", "embedders[0].base_url"])
    assert rc.exit_code == 0
    # Try to read it back — should be absent.
    rc2 = runner.invoke(admin_config.config_app, ["get", "embedders[0].base_url"])
    assert rc2.exit_code == 1


def test_cli_unset_field_with_default_resets(fake_config: Path) -> None:
    # ``embedders[0].normalize`` has Pydantic default True.  Set to
    # False, then unset → must come back as True.
    admin_config._set_config_value_atomic("embedders[0].normalize", "false")
    rc = runner.invoke(admin_config.config_app, ["unset", "embedders[0].normalize"])
    assert rc.exit_code == 0
    rc2 = runner.invoke(admin_config.config_app, ["get", "embedders[0].normalize"])
    assert rc2.stdout.strip() in {"true", "True"}


# ── config show ─────────────────────────────────────────────────────────


def test_cli_show_default_redacts_secrets(fake_config: Path, monkeypatch) -> None:
    # Add a fake DSN-shaped key so we can verify the redactor fires.
    monkeypatch.setenv("CF_TEST", "yes")
    admin_config._set_config_value_atomic("backend.dsn", "postgresql://user:pass@localhost:5432/db")
    rc = runner.invoke(admin_config.config_app, ["show"])
    assert rc.exit_code == 0
    # The DSN value should be redacted by the default path.
    assert "«redacted»" in rc.stdout
    assert "user:pass" not in rc.stdout


def test_cli_show_with_secrets_flag_shows_raw(fake_config: Path, monkeypatch) -> None:
    admin_config._set_config_value_atomic("backend.dsn", "postgresql://user:pass@localhost:5432/db")
    rc = runner.invoke(admin_config.config_app, ["show", "--secrets"])
    assert rc.exit_code == 0
    assert "user:pass" in rc.stdout


def test_cli_show_diff_only_shows_changes(fake_config: Path) -> None:
    """``--diff`` against a config with mostly defaults shows just the deltas."""

    rc = runner.invoke(admin_config.config_app, ["show", "--diff"])
    assert rc.exit_code == 0
    parsed = json.loads(rc.stdout)
    # ``backend.kind`` differs (we set ``sqlite``; default is ``postgres``).
    assert "backend" in parsed


# ── config path ─────────────────────────────────────────────────────────


def test_cli_path_prints_config_file_path(fake_config: Path) -> None:
    rc = runner.invoke(admin_config.config_app, ["path"])
    assert rc.exit_code == 0
    assert rc.stdout.strip() == str(fake_config)


# ── config validate ─────────────────────────────────────────────────────


def test_cli_validate_ok(fake_config: Path) -> None:
    rc = runner.invoke(admin_config.config_app, ["validate"])
    assert rc.exit_code == 0


def test_cli_validate_explicit_file(fake_config: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.toml"
    other.write_text(_BASE_CONFIG, encoding="utf-8")
    rc = runner.invoke(admin_config.config_app, ["validate", "--file", str(other)])
    assert rc.exit_code == 0


def test_cli_validate_bad_file_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[backend]\nkind = "mongodb"\ndsn = "x"\n', encoding="utf-8")
    rc = runner.invoke(admin_config.config_app, ["validate", "--file", str(bad)])
    assert rc.exit_code == 1


# ── config edit ─────────────────────────────────────────────────────────


def _patch_editor_resolver(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Replace ``_resolve_editor`` with a fixed argv (handles paths-with-spaces)."""

    monkeypatch.setattr(admin_config, "_resolve_editor", lambda: list(argv))


def test_cli_edit_rolls_back_on_invalid_save(
    fake_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If $EDITOR produces an invalid config, the .bak is restored."""

    # Fake editor: writes a known-bad version into the config file.
    editor_script = tmp_path / "editor.py"
    editor_script.write_text(
        "import sys, pathlib\n"
        'pathlib.Path(sys.argv[1]).write_text(\'[backend]\\nkind="mongodb"\\ndsn="x"\\n\')\n'
    )
    _patch_editor_resolver(monkeypatch, [sys.executable, str(editor_script)])

    before = fake_config.read_text(encoding="utf-8")
    rc = runner.invoke(admin_config.config_app, ["edit"])
    assert rc.exit_code == 1, rc.stdout
    after = fake_config.read_text(encoding="utf-8")
    assert after == before, "rollback should restore the .bak"


def test_cli_edit_keeps_save_on_valid(
    fake_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    editor_script = tmp_path / "editor.py"
    editor_script.write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1])\n"
        'p.write_text(p.read_text() + "\\n# edited\\n")\n'
    )
    _patch_editor_resolver(monkeypatch, [sys.executable, str(editor_script)])

    rc = runner.invoke(admin_config.config_app, ["edit"])
    assert rc.exit_code == 0
    assert "# edited" in fake_config.read_text(encoding="utf-8")


import sys  # noqa: E402 — placed at bottom so the script-fixture sees it
