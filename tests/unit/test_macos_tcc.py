"""Unit tests for :mod:`corpus_forge.macos_tcc`.

The module is macOS-specific but every public function degrades to a
safe no-op on Linux / Windows so callers can use it unconditionally.
These tests pin both branches:

- On macOS hosts: real path-classification logic, real probe behaviour.
- On non-macOS hosts: ``is_icloud_path`` returns ``False``,
  ``probe_tcc_access`` returns ``NOT_APPLICABLE``,
  ``open_privacy_settings`` returns ``False``,
  ``request_full_disk_access`` returns ``granted=True``,
  ``download_if_evicted`` returns ``True``.

We monkeypatch :data:`sys.platform` to exercise both branches from the
same test process — easier than skipping a half of the suite on every
CI runner.

What we do NOT test here:

- The real ``brctl download`` round-trip — that requires iCloud Drive
  signed in on the test host and would be flaky on CI. Smoke-tested
  by hand on the maintainer's box.
- The literal ``open`` of System Settings — same reason; we assert
  ``subprocess.run`` is called with the right URL when ``open`` would
  fire, and trust macOS to launch the pane.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from corpus_forge import macos_tcc
from corpus_forge.macos_tcc import (
    PrivacyPane,
    TccProbeOutcome,
    download_if_evicted,
    is_icloud_path,
    is_iclouddrive_managed,
    is_macos,
    open_privacy_settings,
    probe_tcc_access,
    request_full_disk_access,
)

# ─────────────────────────────────────────────────────────────────────
# Platform branch
# ─────────────────────────────────────────────────────────────────────


class TestIsMacos:
    def test_darwin_is_macos(self) -> None:
        with patch("corpus_forge.macos_tcc.sys.platform", "darwin"):
            assert is_macos() is True

    def test_linux_is_not_macos(self) -> None:
        with patch("corpus_forge.macos_tcc.sys.platform", "linux"):
            assert is_macos() is False

    def test_windows_is_not_macos(self) -> None:
        with patch("corpus_forge.macos_tcc.sys.platform", "win32"):
            assert is_macos() is False


# ─────────────────────────────────────────────────────────────────────
# Path classification
# ─────────────────────────────────────────────────────────────────────


class TestIsIcloudPath:
    """``is_icloud_path`` is strict — only the CloudDocs container."""

    def test_cloud_docs_root_is_icloud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        home = Path.home()
        target = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Workspace"
        # Make ICLOUD_DRIVE_ROOT resolvable against this synthetic path
        # (no need for the file to exist — classification is textual).
        assert is_icloud_path(target) is True

    def test_obsidian_provider_is_not_icloud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The strict check returns False for app-sandboxed providers."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        home = Path.home()
        target = home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
        assert is_icloud_path(target) is False

    def test_outside_mobile_documents_is_not_icloud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        assert is_icloud_path("/tmp/not-icloud") is False
        assert is_icloud_path(Path.home() / "Documents") is False

    def test_non_macos_always_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "linux")
        target = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "X"
        assert is_icloud_path(target) is False


class TestIsIcloudDriveManaged:
    """``is_iclouddrive_managed`` is broader — any Mobile Documents path."""

    def test_cloud_docs_is_managed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        target = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "X"
        assert is_iclouddrive_managed(target) is True

    def test_obsidian_is_managed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        target = Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
        assert is_iclouddrive_managed(target) is True

    def test_documents_not_managed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        assert is_iclouddrive_managed(Path.home() / "Documents") is False

    def test_non_macos_always_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "linux")
        target = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "X"
        assert is_iclouddrive_managed(target) is False


# ─────────────────────────────────────────────────────────────────────
# Probe
# ─────────────────────────────────────────────────────────────────────


class TestProbeTccAccess:
    def test_non_macos_returns_not_applicable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "linux")
        result = probe_tcc_access("/any/path")
        assert result.outcome is TccProbeOutcome.NOT_APPLICABLE
        assert result.granted is True
        assert result.denied is False

    def test_macos_grants_when_read_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        f = tmp_path / "ok.txt"
        f.write_text("hello")
        result = probe_tcc_access(f)
        assert result.outcome is TccProbeOutcome.GRANTED
        assert result.granted is True

    def test_macos_missing_when_path_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        result = probe_tcc_access(tmp_path / "nope.txt")
        assert result.outcome is TccProbeOutcome.MISSING

    def test_macos_denied_when_eperm(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A real ``[Errno 1] Operation not permitted`` lands on DENIED."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        f = tmp_path / "tcc.txt"
        f.write_text("hello")
        # Wrap Path.open to raise PermissionError(errno=1) on the probe
        original_open = Path.open

        def fake_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if self == f:
                exc = PermissionError("Operation not permitted")
                exc.errno = 1
                raise exc
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)
        result = probe_tcc_access(f)
        assert result.outcome is TccProbeOutcome.DENIED
        assert result.granted is False
        assert result.denied is True
        assert "TCC" in result.message

    def test_macos_other_errno_is_error_not_denied(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``errno != 1`` is ERROR, not DENIED — keeps the install handshake honest."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        f = tmp_path / "other.txt"
        f.write_text("hi")
        original_open = Path.open

        def fake_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if self == f:
                exc = PermissionError("Permission denied")
                exc.errno = 13  # EACCES, not EPERM
                raise exc
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)
        result = probe_tcc_access(f)
        assert result.outcome is TccProbeOutcome.ERROR
        assert result.granted is False
        assert result.denied is False


# ─────────────────────────────────────────────────────────────────────
# Privacy pane opener
# ─────────────────────────────────────────────────────────────────────


class TestOpenPrivacySettings:
    def test_non_macos_is_noop_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "linux")
        with patch("corpus_forge.macos_tcc.subprocess.run") as mock_run:
            assert open_privacy_settings() is False
            mock_run.assert_not_called()

    def test_macos_full_disk_access_invokes_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        with patch("corpus_forge.macos_tcc.subprocess.run") as mock_run:
            assert open_privacy_settings(PrivacyPane.FULL_DISK_ACCESS) is True
            mock_run.assert_called_once()
            args, _ = mock_run.call_args
            cmd = args[0]
            assert cmd[0] == "open"
            assert "Privacy_AllFiles" in cmd[1]

    def test_macos_files_and_folders_uses_different_pane(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        with patch("corpus_forge.macos_tcc.subprocess.run") as mock_run:
            open_privacy_settings(PrivacyPane.FILES_AND_FOLDERS)
            cmd = mock_run.call_args[0][0]
            assert "Privacy_FilesAndFolders" in cmd[1]


# ─────────────────────────────────────────────────────────────────────
# Install-time handshake
# ─────────────────────────────────────────────────────────────────────


class TestRequestFullDiskAccess:
    def test_non_macos_is_granted_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "linux")
        result = request_full_disk_access([Path.home() / "anything"])
        assert result.granted is True
        assert result.opened_settings is False
        assert "macOS" in result.instruction

    def test_no_paths_returns_granted_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        result = request_full_disk_access([])
        assert result.granted is True
        assert result.opened_settings is False

    def test_denial_opens_settings_and_returns_instruction(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        icloud_dir = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        icloud_dir.mkdir(parents=True)
        f = icloud_dir / "blocked.txt"
        f.write_text("hi")

        # Force the path to count as iCloud-managed by patching the
        # MOBILE_DOCUMENTS_ROOT constant to tmp_path's mirror.
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.MOBILE_DOCUMENTS_ROOT",
            tmp_path / "Library" / "Mobile Documents",
        )
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.ICLOUD_DRIVE_ROOT",
            tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
        )

        original_open = Path.open

        def fake_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if self == f:
                exc = PermissionError("Operation not permitted")
                exc.errno = 1
                raise exc
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        opened_calls: list[tuple] = []

        def fake_open_settings(pane: PrivacyPane = PrivacyPane.FULL_DISK_ACCESS) -> bool:
            opened_calls.append((pane,))
            return True

        monkeypatch.setattr("corpus_forge.macos_tcc.open_privacy_settings", fake_open_settings)

        result = request_full_disk_access([f])
        assert result.granted is False
        assert result.opened_settings is True
        assert opened_calls == [(PrivacyPane.FULL_DISK_ACCESS,)]
        assert "Full Disk Access" in result.instruction
        assert "terminal" in result.instruction.lower()

    def test_open_settings_suppression(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``open_settings_on_denial=False`` reports denial without GUI side-effects."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        icloud_dir = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        icloud_dir.mkdir(parents=True)
        f = icloud_dir / "blocked.txt"
        f.write_text("hi")
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.MOBILE_DOCUMENTS_ROOT",
            tmp_path / "Library" / "Mobile Documents",
        )
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.ICLOUD_DRIVE_ROOT",
            tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
        )

        original_open = Path.open

        def fake_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if self == f:
                exc = PermissionError("Operation not permitted")
                exc.errno = 1
                raise exc
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)
        opener_calls: list[object] = []
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.open_privacy_settings",
            lambda pane=PrivacyPane.FULL_DISK_ACCESS: opener_calls.append(pane) or True,
        )

        result = request_full_disk_access([f], open_settings_on_denial=False)
        assert result.granted is False
        assert result.opened_settings is False
        assert opener_calls == []


# ─────────────────────────────────────────────────────────────────────
# Eviction (brctl download)
# ─────────────────────────────────────────────────────────────────────


class TestDownloadIfEvicted:
    def test_non_macos_is_noop_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "linux")
        with patch("corpus_forge.macos_tcc.subprocess.run") as mock_run:
            assert download_if_evicted("/x") is True
            mock_run.assert_not_called()

    def test_existing_file_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        f = tmp_path / "here.txt"
        f.write_text("x")
        with patch("corpus_forge.macos_tcc.subprocess.run") as mock_run:
            assert download_if_evicted(f) is True
            mock_run.assert_not_called()

    def test_non_icloud_missing_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``brctl`` doesn't help for non-iCloud missing files; surface the miss."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        with patch("corpus_forge.macos_tcc.subprocess.run") as mock_run:
            assert download_if_evicted(tmp_path / "missing.txt") is False
            mock_run.assert_not_called()

    def test_no_brctl_on_path_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When Apple finally removes ``brctl``, fall back gracefully."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        icloud_dir = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        icloud_dir.mkdir(parents=True)
        evicted = icloud_dir / "evicted.md"
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.MOBILE_DOCUMENTS_ROOT",
            tmp_path / "Library" / "Mobile Documents",
        )
        monkeypatch.setattr("corpus_forge.macos_tcc.shutil.which", lambda _: None)
        assert download_if_evicted(evicted) is False

    def test_brctl_download_called_when_evicted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``brctl download`` is invoked when an iCloud path is evicted."""
        monkeypatch.setattr("corpus_forge.macos_tcc.sys.platform", "darwin")
        icloud_dir = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        icloud_dir.mkdir(parents=True)
        evicted = icloud_dir / "evicted.md"
        monkeypatch.setattr(
            "corpus_forge.macos_tcc.MOBILE_DOCUMENTS_ROOT",
            tmp_path / "Library" / "Mobile Documents",
        )
        monkeypatch.setattr("corpus_forge.macos_tcc.shutil.which", lambda _: "/usr/bin/brctl")
        called_cmds: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            called_cmds.append(cmd)
            # Simulate brctl materialising the file
            evicted.write_text("downloaded")
            # Return a fake CompletedProcess-shaped object
            import subprocess

            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr("corpus_forge.macos_tcc.subprocess.run", fake_run)
        result = download_if_evicted(evicted)
        assert result is True
        assert len(called_cmds) == 1
        assert called_cmds[0][:2] == ["/usr/bin/brctl", "download"]


# ─────────────────────────────────────────────────────────────────────
# __all__ surface — guard against accidental private exposure
# ─────────────────────────────────────────────────────────────────────


def test_public_surface_pins() -> None:
    """``__all__`` should not silently shrink."""
    expected = {
        "ICLOUD_DRIVE_ROOT",
        "MOBILE_DOCUMENTS_ROOT",
        "PrivacyPane",
        "TccProbeOutcome",
        "TccProbeResult",
        "download_if_evicted",
        "is_icloud_path",
        "is_iclouddrive_managed",
        "is_macos",
        "open_privacy_settings",
        "probe_tcc_access",
        "request_full_disk_access",
    }
    assert set(macos_tcc.__all__) == expected
