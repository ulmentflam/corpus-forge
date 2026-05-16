"""Cloud provider detection."""

from pathlib import Path
from typing import Literal

# macOS iCloud Drive lives under ``~/Library/Mobile Documents``; substrings
# match either CloudDocs root or any container-scoped namespace.
_ICLOUD_MACOS_SUBSTRINGS = (
    "Library/Mobile Documents/com~apple~CloudDocs",
    "Library/Mobile Documents/iCloud~",
)

# Windows iCloud-for-Windows mounts the same data under one of these names
# (varies by installer version + drive-letter assignment). Match is
# case-insensitive on Windows, which uses case-preserving NTFS. The
# filesystem folder is ``iCloudDrive`` (no space) — "iCloud Drive" is
# only the display name shown in File Explorer; both forms are matched
# so user-rendered paths from the shell still detect.
_ICLOUD_WINDOWS_LOWER_SUBSTRINGS = (
    "iclouddrive",
    "icloud drive",
    "icloudphotos",
    "icloud photos",
)


def detect_cloud_provider(path: Path) -> Literal["icloud", "dropbox", "gdrive", "none"]:
    """Detect which cloud storage provider a path belongs to.

    Returns the first matching provider in precedence order:
    iCloud > Dropbox > Google Drive > none.

    iCloud detection covers both the macOS layout
    (``~/Library/Mobile Documents/com~apple~CloudDocs/``) and the
    iCloud-for-Windows layout
    (``%USERPROFILE%\\iCloudDrive`` / ``%USERPROFILE%\\iCloud Photos``);
    the Windows match is case-insensitive on the lowercased path.
    """
    if not isinstance(path, Path):
        raise TypeError(f"Expected Path, got {type(path).__name__}")

    resolved = str(path.resolve())
    lower = resolved.lower()

    # iCloud (highest precedence) — macOS substrings first (case-sensitive),
    # then Windows substrings (case-insensitive on the lowered form).
    if any(s in resolved for s in _ICLOUD_MACOS_SUBSTRINGS):
        return "icloud"
    if any(s in lower for s in _ICLOUD_WINDOWS_LOWER_SUBSTRINGS):
        return "icloud"

    # Dropbox (case-insensitive — handles lowercase variants on Linux)
    if "dropbox" in lower:
        return "dropbox"

    # Google Drive (case-insensitive)
    if "google drive" in lower or "googledrive" in lower or "my drive" in lower:
        return "gdrive"

    return "none"
