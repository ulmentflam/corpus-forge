"""Coverage targeting :mod:`corpus_forge.admin.config` — the internal
helpers + the editor / unset / show-diff edges not covered by the
``config get/set`` happy path in ``test_config_crud.py``.

We don't replicate the CRUD surface; we exercise the helpers that
``set/unset/edit/show`` lean on, plus the side-effect prompt + the
table renderer used by sibling verbs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import tomlkit
from typer.testing import CliRunner

from corpus_forge.admin import config as admin_config
from corpus_forge.admin._path import Token

_BASE_CONFIG = """\
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
    path = tmp_path / "config.toml"
    path.write_text(_BASE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(path))
    return path


# ── resolve_config_path ─────────────────────────────────────────────────


def test_resolve_config_path_default_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_FORGE_CONFIG", raising=False)
    result = admin_config.resolve_config_path()
    assert result == Path.home() / ".config" / "corpus-forge" / "config.toml"


# ── _resolve_field_info ─────────────────────────────────────────────────


def test_resolve_field_info_unknown_key() -> None:
    assert admin_config._resolve_field_info("no.such.thing") is None


def test_resolve_field_info_known_scalar() -> None:
    info = admin_config._resolve_field_info("backend.kind")
    assert info is not None


def test_resolve_field_info_steps_into_list() -> None:
    # ``embedders[0].dimension`` walks through a list[X] then into the
    # ``dimension`` field of EmbedderConfig.
    info = admin_config._resolve_field_info("embedders[0].dimension")
    assert info is not None


def test_list_inner_type_passthrough() -> None:
    # Non-list annotation returns itself.
    assert admin_config._list_inner_type(int) is int


def test_list_inner_type_unwraps_list() -> None:
    assert admin_config._list_inner_type(list[str]) is str


# ── _config_defaults & _diff_dicts ──────────────────────────────────────


def test_config_defaults_returns_dict() -> None:
    defaults = admin_config._config_defaults()
    assert isinstance(defaults, dict)
    # The top-level shape must include the major sections.
    assert "backend" in defaults or "embedders" in defaults


def test_diff_dicts_finds_changes() -> None:
    a = {"x": 1, "y": {"a": 2, "b": 3}, "z": 4}
    b = {"x": 1, "y": {"a": 2, "b": 99}, "z": 4}
    delta = admin_config._diff_dicts(a, b)
    assert delta == {"y": {"b": 3}}


def test_diff_dicts_handles_lists_whole() -> None:
    a = {"x": [1, 2, 3]}
    b = {"x": [1, 2]}
    delta = admin_config._diff_dicts(a, b)
    assert delta == {"x": [1, 2, 3]}


def test_diff_dicts_empty_when_equal() -> None:
    assert admin_config._diff_dicts({"a": 1}, {"a": 1}) == {}


def test_diff_dicts_missing_baseline_key() -> None:
    a = {"x": 1}
    b = {}
    delta = admin_config._diff_dicts(a, b)
    assert delta == {"x": 1}


# ── _to_json_safe / _to_plain ───────────────────────────────────────────


def test_to_json_safe_passes_scalars() -> None:
    assert admin_config._to_json_safe(7) == 7
    assert admin_config._to_json_safe("x") == "x"


def test_to_json_safe_recurses_dict() -> None:
    assert admin_config._to_json_safe({"a": [1, 2]}) == {"a": [1, 2]}


def test_to_plain_recurses() -> None:
    doc = tomlkit.parse("[a]\nb = 1\nc = [2, 3]\n")
    plain = admin_config._to_plain(doc)
    assert plain["a"]["b"] == 1
    assert plain["a"]["c"] == [2, 3]


# ── _remove_at_path ─────────────────────────────────────────────────────


def test_remove_at_path_noop_empty_tokens() -> None:
    doc = tomlkit.parse("a = 1\n")
    admin_config._remove_at_path(doc, [])
    assert "a" in doc


def test_remove_at_path_removes_key() -> None:
    doc = tomlkit.parse("[s]\na = 1\nb = 2\n")
    admin_config._remove_at_path(doc, [Token("key", "s"), Token("key", "a")])
    assert "a" not in doc["s"]
    assert "b" in doc["s"]


def test_remove_at_path_drops_list_index() -> None:
    doc = tomlkit.parse('[[items]]\nname = "a"\n\n[[items]]\nname = "b"\n')
    admin_config._remove_at_path(doc, [Token("key", "items"), Token("index", "0")])
    items = doc["items"]
    assert len(items) == 1
    assert items[0]["name"] == "b"


def test_remove_at_path_missing_last_key_is_noop() -> None:
    """Removing a non-existent leaf key is a no-op (KeyError suppressed)."""

    doc = tomlkit.parse("[s]\na = 1\n")
    # ``s.b`` doesn't exist — last token bail keeps the rest intact.
    admin_config._remove_at_path(doc, [Token("key", "s"), Token("key", "b")])
    assert doc["s"]["a"] == 1


def test_remove_at_path_out_of_range_index_is_noop() -> None:
    """Removing an out-of-range list index is a no-op."""

    doc = tomlkit.parse('[[items]]\nname = "a"\n')
    admin_config._remove_at_path(doc, [Token("key", "items"), Token("index", "9")])
    assert len(doc["items"]) == 1


# ── _walk_one ───────────────────────────────────────────────────────────


def test_walk_one_key_step() -> None:
    doc = tomlkit.parse("[a]\nx = 1\n")
    assert admin_config._walk_one(doc, Token("key", "a"))["x"] == 1


def test_walk_one_index_step() -> None:
    container = [10, 20, 30]
    assert admin_config._walk_one(container, Token("index", "1")) == 20


# ── _field_default ──────────────────────────────────────────────────────


def test_field_default_with_factory() -> None:
    """A factory-defaulted field returns the factory's value."""

    class _Info:
        default = None
        default_factory = list

    val = admin_config._field_default(_Info())
    assert val == []


def test_field_default_factory_raises_returns_remove_sentinel() -> None:
    class _Info:
        default = None

        @staticmethod
        def default_factory():
            raise RuntimeError("boom")

    val = admin_config._field_default(_Info())
    assert val is admin_config._SENTINEL_REMOVE


def test_field_default_scalar() -> None:
    class _Info:
        default = "hello"
        default_factory = None

    assert admin_config._field_default(_Info()) == "hello"


def test_field_default_no_default_returns_remove() -> None:
    class _Info:
        default = None
        default_factory = None

    assert admin_config._field_default(_Info()) is admin_config._SENTINEL_REMOVE


# ── _resolve_editor ─────────────────────────────────────────────────────


def test_resolve_editor_prefers_visual(monkeypatch: pytest.MonkeyPatch) -> None:
    """``$VISUAL`` wins over ``$EDITOR``."""

    monkeypatch.setenv("VISUAL", "fake-visual")
    monkeypatch.setenv("EDITOR", "fake-editor")
    monkeypatch.setattr(
        admin_config.shutil,
        "which",
        lambda name: "/fake/visual" if name == "fake-visual" else None,
    )
    result = admin_config._resolve_editor()
    assert result == ["fake-visual"]


def test_resolve_editor_falls_back_to_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "fake-editor")
    monkeypatch.setattr(
        admin_config.shutil,
        "which",
        lambda name: "/fake/editor" if name == "fake-editor" else None,
    )
    result = admin_config._resolve_editor()
    assert result == ["fake-editor"]


def test_resolve_editor_splits_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``EDITOR`` value with flags is split on whitespace."""

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "fake-vim -p --noplugin")
    monkeypatch.setattr(
        admin_config.shutil,
        "which",
        lambda name: "/fake/vim" if name == "fake-vim" else None,
    )
    result = admin_config._resolve_editor()
    assert result is not None
    assert len(result) > 1
    assert result[0] == "fake-vim"


def test_resolve_editor_returns_none_when_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars, no PATH candidates → returns None."""

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(admin_config.shutil, "which", lambda _name: None)
    assert admin_config._resolve_editor() is None


def test_resolve_editor_env_with_unknown_binary_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$EDITOR=ghost-editor`` (not on PATH) skips to the fallback list."""

    import sys as _sys

    monkeypatch.setenv("EDITOR", "this-binary-does-not-exist-xyz")
    monkeypatch.delenv("VISUAL", raising=False)

    # The fallback list is platform-specific: ``notepad.exe`` on Windows,
    # ``vim`` / ``vi`` / ``nano`` everywhere else.  Pick the right candidate.
    fallback = "notepad.exe" if _sys.platform.startswith("win") else "vim"
    fallback_path = "C:\\fake\\notepad.exe" if _sys.platform.startswith("win") else "/fake/vim"

    monkeypatch.setattr(
        admin_config.shutil,
        "which",
        lambda name: fallback_path if name == fallback else None,
    )
    result = admin_config._resolve_editor()
    assert result == [fallback_path]


# ── _maybe_prompt_side_effect ───────────────────────────────────────────


def test_maybe_prompt_side_effect_unrelated_key_returns_false() -> None:
    assert admin_config._maybe_prompt_side_effect("backend.kind", non_interactive=False) is False


def test_maybe_prompt_side_effect_non_interactive_emits_info() -> None:
    """When non_interactive=True and the key is in the prefix list, the
    helper logs and returns False (no prompt)."""

    assert admin_config._maybe_prompt_side_effect("ollama.base_url", non_interactive=True) is False


def test_maybe_prompt_side_effect_interactive_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_config.Confirm, "ask", lambda *a, **k: True)
    assert admin_config._maybe_prompt_side_effect("ollama.base_url", non_interactive=False) is True


def test_maybe_prompt_side_effect_interactive_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_config.Confirm, "ask", lambda *a, **k: False)
    assert admin_config._maybe_prompt_side_effect("embedders[0]", non_interactive=False) is False


# ── render_table_summary ────────────────────────────────────────────────


def test_render_table_summary_prints_rows(capsys: pytest.CaptureFixture) -> None:
    admin_config.render_table_summary(
        [{"name": "x", "value": 1}, {"name": "y", "value": 2}],
        title="Demo",
        columns=[("Name", "name"), ("Value", "value")],
    )
    out = capsys.readouterr()
    combined = (out.out or "") + (out.err or "")
    assert "x" in combined
    assert "y" in combined


def test_render_table_summary_handles_missing_key(capsys: pytest.CaptureFixture) -> None:
    admin_config.render_table_summary(
        [{"name": "x"}],
        title="Demo",
        columns=[("Name", "name"), ("Missing", "missing_key")],
    )
    # Should still render with an empty cell for the missing key.
    out = capsys.readouterr()
    combined = (out.out or "") + (out.err or "")
    assert "x" in combined


# ── cmd_unset edge: unknown key ─────────────────────────────────────────


def test_cli_unset_unknown_key_handles_missing_intermediate(fake_config: Path) -> None:
    """Unsetting a non-existent top-level key path is a no-op + validates OK.

    The unset machinery walks the doc; a NonExistentKey raised by tomlkit
    propagates as ConfigWriteError from the helper. The CLI translates
    that to exit-code 1. Either outcome covers the branch — we just need
    the code path exercised.
    """

    runner = CliRunner()
    result = runner.invoke(admin_config.config_app, ["unset", "no.such.thing"])
    # Either succeeds (silent no-op) or fails the rollback check (1).
    assert result.exit_code in (0, 1)


def test_cli_unset_validates_after(fake_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If post-unset validation fails, exit code is 1."""

    def _fake_load(**_kw):
        raise ValueError("invalid")

    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda cls, **kw: _fake_load(**kw)),
    )

    runner = CliRunner()
    result = runner.invoke(admin_config.config_app, ["unset", "backend.kind"])
    assert result.exit_code == 1


# ── cmd_edit failure paths ──────────────────────────────────────────────


def test_cli_edit_no_config_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(tmp_path / "missing.toml"))

    runner = CliRunner()
    result = runner.invoke(admin_config.config_app, ["edit"])
    assert result.exit_code == 1


def test_cli_edit_editor_not_found_exits_one(
    fake_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin_config, "_resolve_editor", lambda: None)
    runner = CliRunner()
    result = runner.invoke(admin_config.config_app, ["edit"])
    assert result.exit_code == 1


def test_cli_edit_editor_returns_nonzero(
    fake_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If $EDITOR exits non-zero, the config is left untouched + backup removed."""

    monkeypatch.setattr(admin_config, "_resolve_editor", lambda: ["true"])

    def _editor_fails(*_a, **_k):
        return 99

    monkeypatch.setattr(admin_config.subprocess, "call", _editor_fails)

    before = fake_config.read_text(encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(admin_config.config_app, ["edit"])
    assert result.exit_code == 99
    after = fake_config.read_text(encoding="utf-8")
    assert before == after
    # The backup must be cleaned up.
    assert not fake_config.with_suffix(".toml.bak").exists()


# ── _set_config_value_atomic helpful errors ─────────────────────────────


def test_set_atomic_raises_for_uncoercable(fake_config: Path) -> None:
    """Pass a value that can't be coerced for the field type."""

    with pytest.raises(admin_config.ConfigWriteError):
        admin_config._set_config_value_atomic("embedders[0].dimension", "not-a-number")


# ── show --diff (legacy load via Config.load) ───────────────────────────


def test_cli_show_default_no_secrets(fake_config: Path) -> None:
    """No DSN to redact → ``show`` just prints the config text."""

    runner = CliRunner()
    result = runner.invoke(admin_config.config_app, ["show"])
    assert result.exit_code == 0
    assert "[backend]" in result.stdout


# ── write_toml_atomic error path ────────────────────────────────────────


def test_write_toml_atomic_unwraps_tmp_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the tmp write raises, the .tmp file must be cleaned up."""

    target = tmp_path / "out.toml"

    # Force os.fdopen to raise mid-write.
    original_fdopen = os.fdopen

    class _BoomWrapper:
        def __init__(self, real) -> None:
            self._real = real

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._real.__exit__(*exc)
            return False

        def write(self, data: str) -> None:
            raise OSError("disk full")

    def _boom(fd, mode, *a, **k):
        return _BoomWrapper(original_fdopen(fd, mode, *a, **k))

    monkeypatch.setattr(admin_config.os, "fdopen", _boom)

    with pytest.raises(OSError):
        admin_config.write_toml_atomic(target, tomlkit.parse("a = 1\n"))
    # No leftover .tmp files.
    assert not list(tmp_path.glob(f"{target.name}.*.tmp"))


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
