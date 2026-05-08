"""Cloud provider detection."""

from pathlib import Path
from typing import Literal


def detect_cloud_provider(path: Path) -> Literal["icloud", "dropbox", "gdrive", "none"]:
    """Detect which cloud storage provider a path belongs to.

    Returns the first matching provider in precedence order: iCloud > Dropbox > Google Drive > none.
    """
    if not isinstance(path, Path):
        raise TypeError(f"Expected Path, got {type(path).__name__}")

    resolved = str(path.resolve())
    lower = resolved.lower()

    # iCloud (highest precedence)
    if (
        "Library/Mobile Documents/com~apple~CloudDocs" in resolved
        or "Library/Mobile Documents/iCloud~" in resolved
    ):
        return "icloud"

    # Dropbox (case-insensitive — handles lowercase variants on Linux)
    if "dropbox" in lower:
        return "dropbox"

    # Google Drive (case-insensitive)
    if "google drive" in lower or "googledrive" in lower or "my drive" in lower:
        return "gdrive"

    return "none"
