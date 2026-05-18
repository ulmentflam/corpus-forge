"""Tests for ``corpus-forge service install/uninstall`` (Phase L Wave 8).

Covers the platform-specific unit generators + the apply behaviour
(``--apply`` writes to user-scope locations; ``--system`` is refused).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_forge.admin import service_install as si

# ── generate_systemd_unit ───────────────────────────────────────────────


def test_systemd_unit_contains_unit_section() -> None:
    text = si.generate_systemd_unit()
    assert "[Unit]" in text
    assert "Description=corpus-forge daemon" in text


def test_systemd_unit_contains_exec_start_service_start() -> None:
    text = si.generate_systemd_unit()
    # ExecStart must contain `service start` — the new entry-point.
    assert "ExecStart=" in text
    assert "service start" in text


def test_systemd_unit_includes_config_path_env(tmp_path: Path) -> None:
    custom = tmp_path / "config.toml"
    text = si.generate_systemd_unit(config_path=custom)
    assert f"Environment=CF_CONFIG={custom}" in text


def test_systemd_unit_has_restart_on_failure() -> None:
    text = si.generate_systemd_unit()
    assert "Restart=on-failure" in text


def test_systemd_unit_has_install_target() -> None:
    text = si.generate_systemd_unit()
    assert "[Install]" in text
    assert "WantedBy=default.target" in text


# ── generate_launchd_plist ──────────────────────────────────────────────


def test_launchd_plist_is_valid_xml() -> None:
    plist = si.generate_launchd_plist()
    # XML parser must accept it (otherwise launchd would reject it too).
    ET.fromstring(plist)  # raises ParseError on garbage


def test_launchd_plist_has_label() -> None:
    plist = si.generate_launchd_plist()
    root = ET.fromstring(plist)
    dict_node = root.find("dict")
    assert dict_node is not None
    # Walk key/value pairs and locate Label.
    children = list(dict_node)
    label_idx = next(i for i, e in enumerate(children) if e.tag == "key" and e.text == "Label")
    label_value = children[label_idx + 1]
    assert label_value.tag == "string"
    assert label_value.text == "com.corpus-forge"


def test_launchd_plist_program_arguments_includes_service_start() -> None:
    plist = si.generate_launchd_plist()
    assert "<string>service</string>" in plist
    assert "<string>start</string>" in plist


def test_launchd_plist_has_run_at_load_and_keep_alive() -> None:
    plist = si.generate_launchd_plist()
    assert "<key>RunAtLoad</key>" in plist
    assert "<key>KeepAlive</key>" in plist


def test_launchd_plist_environment_variables_include_cf_config(tmp_path: Path) -> None:
    custom = tmp_path / "config.toml"
    plist = si.generate_launchd_plist(config_path=custom)
    assert "<key>CF_CONFIG</key>" in plist
    assert f"<string>{custom}</string>" in plist


# ── generate_schtasks_command ───────────────────────────────────────────


def test_schtasks_argv_contains_create() -> None:
    argv = si.generate_schtasks_command()
    assert "/create" in argv


def test_schtasks_argv_includes_task_name() -> None:
    argv = si.generate_schtasks_command()
    assert si.SCHTASKS_TASK_NAME in argv


def test_schtasks_argv_first_token_is_schtasks() -> None:
    argv = si.generate_schtasks_command()
    assert argv[0] == "schtasks"


def test_schtasks_argv_runs_service_start() -> None:
    argv = si.generate_schtasks_command()
    tr_idx = argv.index("/TR")
    tr_value = argv[tr_idx + 1]
    assert "service start" in tr_value


def test_schtasks_argv_carries_config_path(tmp_path: Path) -> None:
    custom = tmp_path / "config.toml"
    argv = si.generate_schtasks_command(config_path=custom)
    tr_idx = argv.index("/TR")
    tr_value = argv[tr_idx + 1]
    assert f"CF_CONFIG={custom}" in tr_value


# ── install verb: stdout-only mode ──────────────────────────────────────


def test_install_systemd_without_apply_prints_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin HOME so the unit's CF_CONFIG path is predictable.
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--systemd"])
    assert result.exit_code == 0, result.output
    assert "[Unit]" in result.output
    assert "service start" in result.output


def test_install_launchd_without_apply_prints_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--launchd"])
    assert result.exit_code == 0, result.output
    assert "<?xml" in result.output
    assert "com.corpus-forge" in result.output


def test_install_schtasks_without_apply_prints_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--schtasks"])
    assert result.exit_code == 0, result.output
    assert "schtasks" in result.output
    assert "/create" in result.output


# ── install verb: --apply (user-scope only) ─────────────────────────────


def test_install_systemd_apply_writes_unit_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # Recompute the install location relative to the new HOME.
    expected = tmp_path / ".config" / "systemd" / "user" / "corpus-forge.service"
    monkeypatch.setattr(si, "SYSTEMD_USER_UNIT_PATH", expected)

    # Stub out systemctl so the test never touches the real init.
    import subprocess

    def _fake_run(argv, *args, **kwargs):
        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--systemd", "--apply"])
    assert result.exit_code == 0, result.output
    assert expected.exists()
    content = expected.read_text(encoding="utf-8")
    assert "ExecStart=" in content


def test_install_launchd_apply_writes_plist_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / "Library" / "LaunchAgents" / "com.corpus-forge.plist"
    monkeypatch.setattr(si, "LAUNCHD_PLIST_PATH", expected)

    import subprocess

    def _fake_run(argv, *args, **kwargs):
        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--launchd", "--apply"])
    assert result.exit_code == 0, result.output
    assert expected.exists()
    assert "com.corpus-forge" in expected.read_text(encoding="utf-8")


def test_install_refuses_system_wide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--system"])
    # Non-zero exit + helpful error message pointing at sudo.
    assert result.exit_code != 0
    # Combine stdout and stderr (Typer's error output) for the assertion.
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "sudo" in combined.lower() or "system" in combined.lower()


def test_install_rejects_multiple_kind_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "install", "--systemd", "--launchd"])
    assert result.exit_code != 0


# ── uninstall ───────────────────────────────────────────────────────────


def test_uninstall_systemd_removes_unit_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    unit = tmp_path / ".config" / "systemd" / "user" / "corpus-forge.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(si, "SYSTEMD_USER_UNIT_PATH", unit)

    import subprocess

    def _fake_run(argv, *args, **kwargs):
        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall", "--systemd"])
    assert result.exit_code == 0, result.output
    assert not unit.exists()


def test_uninstall_launchd_removes_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    plist = tmp_path / "Library" / "LaunchAgents" / "com.corpus-forge.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(si, "LAUNCHD_PLIST_PATH", plist)

    import subprocess

    def _fake_run(argv, *args, **kwargs):
        class _R:
            returncode = 0
            stderr = ""
            stdout = ""

        return _R()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall", "--launchd"])
    assert result.exit_code == 0, result.output
    assert not plist.exists()


def test_uninstall_no_unit_file_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    nowhere = tmp_path / "missing.service"
    monkeypatch.setattr(si, "SYSTEMD_USER_UNIT_PATH", nowhere)

    import subprocess

    def _fake_run(*a, **kw):
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    runner = CliRunner()
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "uninstall", "--systemd"])
    assert result.exit_code == 0
