"""Unit tests for detect_cloud_provider — cloud storage detection from filesystem paths."""

from pathlib import Path

import pytest

# The function does not exist yet — these tests must fail red.
from corpus_forge.sync.cloud import detect_cloud_provider

# ── iCloud tests ──────────────────────────────────────────────────────────


class TestDetectCloudProvideriCloud:
    """iCloud Drive and iCloud app-container paths detect as 'icloud'."""

    def test_icloud_cloud_docs_path(self, tmp_path):
        """Full iCloud Drive path: Library/Mobile Documents/com~apple~CloudDocs."""
        iCloud_path = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Notes"
        iCloud_path.mkdir(parents=True)
        assert detect_cloud_provider(iCloud_path) == "icloud"

    def test_icloud_iCloud_tilde_path(self, tmp_path):
        """iCloud app container: Library/Mobile Documents/iCloud~com.apple.Notes."""
        iCloud_path = (
            tmp_path / "Library" / "Mobile Documents" / "iCloud~com~apple~CloudKit" / "data"
        )
        iCloud_path.mkdir(parents=True)
        assert detect_cloud_provider(iCloud_path) == "icloud"

    def test_icloud_deeply_nested_file(self, tmp_path):
        """Deep file inside iCloud Drive still detects."""
        iCloud_path = (
            tmp_path
            / "Library"
            / "Mobile Documents"
            / "com~apple~CloudDocs"
            / "Vault"
            / "docs"
            / "2024"
            / "report.md"
        )
        iCloud_path.parent.mkdir(parents=True)
        assert detect_cloud_provider(iCloud_path) == "icloud"

    def test_icloud_case_sensitive_upper(self, tmp_path):
        """Path with 'Library/Mobile Documents' capitalization is matched."""
        iCloud_path = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "x"
        iCloud_path.parent.mkdir(parents=True)
        assert detect_cloud_provider(iCloud_path) == "icloud"


# ── Dropbox tests ─────────────────────────────────────────────────────────


class TestDetectCloudProviderDropbox:
    """Dropbox paths detect as 'dropbox'."""

    def test_dropbox_home_path(self, tmp_path):
        """Standard Dropbox symlink path contains 'Dropbox'."""
        dropbox_path = tmp_path / "Dropbox" / "CorpusForge" / "vault"
        dropbox_path.mkdir(parents=True)
        assert detect_cloud_provider(dropbox_path) == "dropbox"

    def test_dropbox_nested_inside(self, tmp_path):
        """Deep file inside Dropbox still detects."""
        dropbox_path = tmp_path / "Users" / "alice" / "Dropbox" / "Work" / "project" / "notes.md"
        dropbox_path.parent.mkdir(parents=True)
        assert detect_cloud_provider(dropbox_path) == "dropbox"

    def test_dropbox_lowercase_variant_not_matched(self, tmp_path):
        """'dropbox' (lowercase) substring should still match (case-insensitive path check)."""
        dropbox_path = tmp_path / "home" / "alice" / "dropbox" / "vault"
        dropbox_path.mkdir(parents=True)
        # On macOS the real path is Dropbox with capital D; on Linux it may be lowercase.
        # The spec says substring match on str(path.resolve()), so we test what the spec says.
        assert detect_cloud_provider(dropbox_path) == "dropbox"


# ── Google Drive tests ────────────────────────────────────────────────────


class TestDetectCloudProviderGoogleDrive:
    """Google Drive paths detect as 'gdrive'."""

    def test_google_drive_with_space(self, tmp_path):
        """'Google Drive' (with space) substring matches."""
        gdrive_path = tmp_path / "Users" / "alice" / "Google Drive" / "CorpusForge"
        gdrive_path.mkdir(parents=True)
        assert detect_cloud_provider(gdrive_path) == "gdrive"

    def test_google_drive_no_space(self, tmp_path):
        """'GoogleDrive' (no space) substring matches."""
        gdrive_path = tmp_path / "Users" / "alice" / "GoogleDrive" / "vault"
        gdrive_path.mkdir(parents=True)
        assert detect_cloud_provider(gdrive_path) == "gdrive"

    def test_my_drive_variant(self, tmp_path):
        """'My Drive' substring matches."""
        gdrive_path = tmp_path / "Users" / "alice" / "My Drive" / "Notes"
        gdrive_path.mkdir(parents=True)
        assert detect_cloud_provider(gdrive_path) == "gdrive"

    def test_deep_google_drive_file(self, tmp_path):
        """Deep file inside Google Drive still detects."""
        gdrive_path = (
            tmp_path / "Users" / "bob" / "Google Drive" / "Vault" / "2024" / "Q1" / "report.md"
        )
        gdrive_path.parent.mkdir(parents=True)
        assert detect_cloud_provider(gdrive_path) == "gdrive"


# ── None / unmatched tests ────────────────────────────────────────────────


class TestDetectCloudProviderNone:
    """Plain paths with no cloud keyword return 'none'."""

    def test_plain_local_path(self, tmp_path):
        """A regular local directory returns 'none'."""
        local_path = tmp_path / "Users" / "alice" / "Documents" / "vault"
        local_path.mkdir(parents=True)
        assert detect_cloud_provider(local_path) == "none"

    def test_empty_tmp_path(self):
        """Root-like path returns 'none'."""
        assert detect_cloud_provider(Path("/")) == "none"

    def test_relative_path_no_match(self, tmp_path, monkeypatch):
        """Relative path with no cloud keyword returns 'none'."""
        # chdir to tmp_path so relative paths don't resolve to iCloud
        monkeypatch.chdir(tmp_path)
        rel = Path("my_vault")
        assert detect_cloud_provider(rel) == "none"

    def test_path_with_vault_in_name_no_false_positive(self, tmp_path):
        """'vault' in path must NOT trigger a cloud match."""
        vault_path = tmp_path / "projects" / "corpus-vault" / "data"
        vault_path.mkdir(parents=True)
        assert detect_cloud_provider(vault_path) == "none"

    def test_path_with_drive_in_name_no_false_positive(self, tmp_path):
        """'drive' as part of another word (e.g. 'driven') must NOT falsely match 'Google Drive'."""
        driven_path = tmp_path / "projects" / "driven" / "app"
        driven_path.mkdir(parents=True)
        assert detect_cloud_provider(driven_path) == "none"


# ── Precedence tests ──────────────────────────────────────────────────────


class TestDetectCloudProviderPrecedence:
    """First match in precedence wins: iCloud > Dropbox > Google Drive > none."""

    def test_icloud_precedence_over_dropbox(self, tmp_path):
        """If path contains both iCloud and Dropbox markers, iCloud wins."""
        # Construct a path that contains both substrings
        cloud_path = (
            tmp_path
            / "Library"
            / "Mobile Documents"
            / "com~apple~CloudDocs"
            / "Dropbox"  # Dropbox substring inside iCloud path
            / "vault"
        )
        cloud_path.mkdir(parents=True)
        assert detect_cloud_provider(cloud_path) == "icloud"

    def test_icloud_precedence_over_google_drive(self, tmp_path):
        """If path contains both iCloud and Google Drive markers, iCloud wins."""
        cloud_path = (
            tmp_path
            / "Library"
            / "Mobile Documents"
            / "iCloud~com~apple~CloudKit"
            / "Google Drive"
            / "vault"
        )
        cloud_path.mkdir(parents=True)
        assert detect_cloud_provider(cloud_path) == "icloud"

    def test_dropbox_precedence_over_google_drive(self, tmp_path):
        """If path contains both Dropbox and Google Drive markers, Dropbox wins."""
        cloud_path = tmp_path / "Dropbox" / "Google Drive" / "vault"
        cloud_path.mkdir(parents=True)
        assert detect_cloud_provider(cloud_path) == "dropbox"


# ── Type / format tests ───────────────────────────────────────────────────


class TestDetectCloudProviderTypeHandling:
    """Wrong types and edge-format inputs."""

    def test_string_input_raises(self):
        """Passing a plain string (not Path) should raise TypeError."""
        with pytest.raises(TypeError):
            detect_cloud_provider("/Users/alice/Dropbox")  # type: ignore[arg-type]

    def test_pathlib_posix_path(self, tmp_path):
        """Path with forward slashes on macOS should still resolve correctly."""
        p = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "test"
        p.mkdir(parents=True)
        assert detect_cloud_provider(p) == "icloud"

    def test_symlink_resolved(self, tmp_path):
        """Symlinked path: detection should work on the resolved target."""
        real_dir = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "real"
        real_dir.mkdir(parents=True)
        link_dir = tmp_path / "link_to_icloud"
        link_dir.symlink_to(real_dir)
        assert detect_cloud_provider(link_dir) == "icloud"

    def test_path_with_spaces(self, tmp_path):
        """Path containing spaces should resolve without error."""
        spaced = tmp_path / "My Documents" / "Google Drive" / "vault"
        spaced.mkdir(parents=True)
        assert detect_cloud_provider(spaced) == "gdrive"


# ── Return type tests ─────────────────────────────────────────────────────


class TestDetectCloudProviderReturnType:
    """Return type is Literal["icloud", "dropbox", "gdrive", "none"]."""

    @pytest.mark.parametrize(
        ("describe", "provider"),
        [
            ("iCloud iCloud~ path", "icloud"),
            ("iCloud com~apple~CloudDocs path", "icloud"),
            ("Dropbox path", "dropbox"),
            ("Google Drive path", "gdrive"),
            ("GoogleDrive path", "gdrive"),
            ("My Drive path", "gdrive"),
            ("unmatched path", "none"),
        ],
    )
    def test_returns_valid_literal(self, tmp_path, describe, provider):
        """All paths return one of the four valid literal values."""
        if provider == "icloud":
            test_path = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "x"
        elif provider == "dropbox":
            test_path = tmp_path / "Dropbox" / "x"
        elif provider == "gdrive":
            test_path = tmp_path / "Google Drive" / "x"
        else:
            test_path = tmp_path / "local" / "vault" / "x"
        test_path.mkdir(parents=True)
        result = detect_cloud_provider(test_path)
        assert result in ("icloud", "dropbox", "gdrive", "none")
        assert result == provider
