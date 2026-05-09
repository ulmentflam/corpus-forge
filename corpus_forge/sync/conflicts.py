"""Conflict file naming utilities for the sync subsystem."""

import re
from datetime import datetime
from pathlib import Path


def is_cloud_duplicate(path: Path) -> tuple[bool, str | None, Path | None]:
    """Detect and classify cloud-sync conflict copies by filename pattern.

    Returns (is_duplicate, provider, canonical_path).
    provider is one of "icloud", "dropbox", "gdrive", "finder".
    """
    path = Path(path)
    stem = path.stem
    suffix = path.suffix

    # Google Drive: "<stem> (1)<suffix>" or "<stem>-conflict-<date>-<n><suffix>"
    gdrive_match = re.match(r"^(.+?)(?: \(1\)|-conflict-[\w-]+-\d+)$", stem)
    if gdrive_match:
        canonical = path.with_name(f"{gdrive_match.group(1)}{suffix}")
        return (True, "gdrive", canonical)

    # Dropbox: "<stem> (<host>'s conflicted copy <date>)<suffix>"
    dropbox_match = re.match(r"^(.+?) \(.*'s conflicted copy .*\)$", stem)
    if dropbox_match:
        canonical = path.with_name(f"{dropbox_match.group(1)}{suffix}")
        return (True, "dropbox", canonical)

    # Finder: "<stem> copy<suffix>" or "<stem> copy <n><suffix>"
    # Checked before icloud so "Foo copy 2" → finder, not icloud
    finder_match = re.match(r"^(.+?) copy(?: (\d+))?$", stem)
    if finder_match:
        canonical = path.with_name(f"{finder_match.group(1)}{suffix}")
        return (True, "finder", canonical)

    # iCloud: "<stem> <n><suffix>" or "<stem> (<n>)<suffix>"
    icloud_match = re.match(r"^(.+?)(?: (\d+)| \((\d+)\))$", stem)
    if icloud_match:
        canonical = path.with_name(f"{icloud_match.group(1)}{suffix}")
        return (True, "icloud", canonical)

    return (False, None, None)


def conflict_filename(
    original: Path,
    host: str,
    ts: datetime,
    provider: str | None = None,
) -> Path:
    """Return a canonical conflict filename for *original*.

    Without provider: <stem>.conflict-<host>-<ts><suffix>
    With provider: <stem>.conflict-<provider>-<host>-<ts><suffix>
    """
    if not isinstance(original, Path):
        raise TypeError(f"expected Path, got {type(original).__name__}")
    if not isinstance(host, str):
        raise TypeError(f"expected str for host, got {type(host).__name__}")
    if not isinstance(ts, datetime):
        raise TypeError(f"expected datetime for ts, got {type(ts).__name__}")
    if provider is not None and not isinstance(provider, str):
        raise TypeError(f"expected str or None for provider, got {type(provider).__name__}")

    stem = original.stem
    suffix = original.suffix
    ts_str = ts.strftime("%Y%m%dT%H%M%SZ")

    if provider:
        new_name = f"{stem}.conflict-{provider}-{host}-{ts_str}{suffix}"
    else:
        new_name = f"{stem}.conflict-{host}-{ts_str}{suffix}"

    return original.with_name(new_name)
