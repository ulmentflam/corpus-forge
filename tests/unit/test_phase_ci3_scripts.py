"""Phase CI-3 script split + Makefile uname dispatch pins.

- `scripts/{install,stop,uninstall}.sh` are gone (moved with `git mv`).
- `scripts/macos/{install,stop,uninstall}.sh` exist + executable + bash -n clean.
- `scripts/linux/{install,stop,uninstall}.sh` exist + executable + bash -n clean.
- `packaging/corpus-forge.service.template` exists with [Unit][Service][Install] anchors.
- Makefile `stop` / `logs` targets dispatch on `uname -s` and reference launchctl
  (on darwin) / systemctl (on linux).
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
PACKAGING = REPO_ROOT / "packaging"
MAKEFILE = REPO_ROOT / "Makefile"

MACOS_SCRIPTS = ["install.sh", "stop.sh", "uninstall.sh"]
LINUX_SCRIPTS = ["install.sh", "stop.sh", "uninstall.sh"]


# ── old top-level scripts gone ──────────────────────────────────────────────


class TestOldScriptsRemoved:
    @pytest.mark.parametrize("name", MACOS_SCRIPTS)
    def test_no_top_level_script(self, name: str) -> None:
        path = SCRIPTS / name
        assert not path.exists(), (
            f"Old top-level script {path} should have been git-mv'd into scripts/macos/"
        )


# ── macOS scripts ───────────────────────────────────────────────────────────


class TestMacOSScripts:
    @pytest.mark.parametrize("name", MACOS_SCRIPTS)
    def test_exists(self, name: str) -> None:
        path = SCRIPTS / "macos" / name
        assert path.exists(), f"Missing {path}"

    @pytest.mark.requires_unix
    @pytest.mark.parametrize("name", MACOS_SCRIPTS)
    def test_executable(self, name: str) -> None:
        """``S_IXUSR`` is a POSIX bit Windows filesystems don't preserve."""
        path = SCRIPTS / "macos" / name
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} is not executable (mode={oct(mode)})"

    @pytest.mark.parametrize("name", MACOS_SCRIPTS)
    def test_bash_syntax(self, name: str) -> None:
        path = SCRIPTS / "macos" / name
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash not on PATH")
        result = subprocess.run(
            [bash, "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, (
            f"bash -n {path} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_install_references_plist(self) -> None:
        """macOS installer must still render the launchd plist."""
        path = SCRIPTS / "macos" / "install.sh"
        text = path.read_text(encoding="utf-8")
        assert "plist" in text.lower()
        # Path to the template — must adapt to the new ../packaging relative root if any.
        assert "corpus-forge.plist.template" in text


# ── Linux scripts ───────────────────────────────────────────────────────────


class TestLinuxScripts:
    @pytest.mark.parametrize("name", LINUX_SCRIPTS)
    def test_exists(self, name: str) -> None:
        path = SCRIPTS / "linux" / name
        assert path.exists(), f"Missing {path}"

    @pytest.mark.requires_unix
    @pytest.mark.parametrize("name", LINUX_SCRIPTS)
    def test_executable(self, name: str) -> None:
        """``S_IXUSR`` is a POSIX bit Windows filesystems don't preserve."""
        path = SCRIPTS / "linux" / name
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} is not executable (mode={oct(mode)})"

    @pytest.mark.parametrize("name", LINUX_SCRIPTS)
    def test_bash_syntax(self, name: str) -> None:
        path = SCRIPTS / "linux" / name
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash not on PATH")
        result = subprocess.run(
            [bash, "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, (
            f"bash -n {path} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    def test_install_uses_systemctl_user(self) -> None:
        path = SCRIPTS / "linux" / "install.sh"
        text = path.read_text(encoding="utf-8")
        assert "systemctl --user" in text, "Linux installer should use `systemctl --user`"
        assert "corpus-forge.service" in text

    def test_install_references_service_template(self) -> None:
        path = SCRIPTS / "linux" / "install.sh"
        text = path.read_text(encoding="utf-8")
        assert "corpus-forge.service.template" in text, (
            "Linux installer should render from packaging/corpus-forge.service.template"
        )

    def test_install_targets_xdg_user_config(self) -> None:
        path = SCRIPTS / "linux" / "install.sh"
        text = path.read_text(encoding="utf-8")
        assert ".config/systemd/user" in text, (
            "Linux installer should write to ~/.config/systemd/user/"
        )

    def test_stop_uses_systemctl(self) -> None:
        text = (SCRIPTS / "linux" / "stop.sh").read_text(encoding="utf-8")
        assert "systemctl --user stop" in text

    def test_uninstall_uses_systemctl(self) -> None:
        text = (SCRIPTS / "linux" / "uninstall.sh").read_text(encoding="utf-8")
        assert "systemctl --user disable" in text


# ── systemd service template ────────────────────────────────────────────────


class TestServiceTemplate:
    template = PACKAGING / "corpus-forge.service.template"

    def test_exists(self) -> None:
        assert self.template.exists(), f"Missing {self.template}"

    def test_unit_section(self) -> None:
        text = self.template.read_text(encoding="utf-8")
        assert "[Unit]" in text

    def test_service_section(self) -> None:
        text = self.template.read_text(encoding="utf-8")
        assert "[Service]" in text

    def test_install_section(self) -> None:
        text = self.template.read_text(encoding="utf-8")
        assert "[Install]" in text
        assert "WantedBy=default.target" in text

    def test_exec_start_uses_corpus_forge_daemon(self) -> None:
        text = self.template.read_text(encoding="utf-8")
        # %h is systemd's user-home substitution; ExecStart must call corpus-forge daemon.
        assert "ExecStart=" in text
        assert "corpus-forge" in text
        assert "daemon" in text

    def test_restart_on_failure(self) -> None:
        text = self.template.read_text(encoding="utf-8")
        assert "Restart=on-failure" in text

    def test_environment_home(self) -> None:
        text = self.template.read_text(encoding="utf-8")
        assert "Environment=HOME=%h" in text or "HOME=%h" in text


# ── Makefile dispatch ───────────────────────────────────────────────────────


class TestMakefileDispatch:
    def test_makefile_has_os_var(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        # Either explicit `OS := $(shell uname -s)` or inline `$(shell uname -s)`.
        assert "uname -s" in text, "Makefile should dispatch on `uname -s` for stop/logs in CI-3"

    def test_makefile_has_systemctl_branch(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        assert "systemctl --user" in text, (
            "Makefile must have a Linux branch using `systemctl --user`"
        )

    def test_makefile_has_journalctl_branch(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        assert "journalctl --user" in text, (
            "Makefile must have a Linux logs branch using `journalctl --user`"
        )

    def test_makefile_keeps_launchctl(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        assert "launchctl" in text or "macos/stop.sh" in text, (
            "Makefile must keep macOS launchctl behaviour (directly or via macos/stop.sh)"
        )

    @pytest.mark.skipif(sys.platform != "darwin", reason="darwin-only smoke check")
    def test_make_dryrun_stop_on_darwin(self) -> None:
        """`make -n stop` on darwin should expand to something with launchctl or macos/stop.sh."""
        make = shutil.which("make")
        if not make:
            pytest.skip("make not on PATH")
        result = subprocess.run(
            [make, "-n", "stop"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, (
            f"make -n stop failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert (
            "launchctl" in combined
            or "macos/stop.sh" in combined
            or "scripts/macos/stop.sh" in combined
        ), f"darwin `make -n stop` should mention launchctl or macos/stop.sh; got:\n{combined}"
