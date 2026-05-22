"""macOS iCloud Drive + TCC integration for corpus-forge.

corpus-forge is frequently configured to ingest from iCloud Drive
paths (``~/Library/Mobile Documents/com~apple~CloudDocs/...``). macOS
gates that location behind the **TCC** (Transparency, Consent, and
Control) permission system: a process whose terminal app hasn't been
granted "Full Disk Access" or "Files and Folders → iCloud Drive" sees
``[Errno 1] Operation not permitted`` on ``open()`` even though POSIX
permissions allow the read.

This module gives ``corpus-forge setup`` and ``corpus-forge doctor``
the pieces they need to detect that situation, surface a clear
recovery, and (when the user agrees) pop the right Privacy pane in
System Settings.

Public surface:

- :func:`is_icloud_path` — recognise paths under macOS iCloud's mirror
  (``~/Library/Mobile Documents/com~apple~CloudDocs/...``).
- :func:`is_iclouddrive_managed` — broader check that also matches
  ``com~apple~Obsidian`` and other ``Mobile Documents`` providers.
- :func:`probe_tcc_access` — non-destructive 1-byte read to test
  whether the caller can actually read a path. Returns a structured
  result that distinguishes TCC denial from other I/O failures.
- :func:`open_privacy_settings` — open System Settings to the right
  Privacy pane (Full Disk Access or Files and Folders) so the user
  can grant access manually.
- :func:`request_full_disk_access` — convenience that probes the
  given path, and on TCC denial opens System Settings and returns
  a structured outcome plus instruction text naming the binary the
  user should add.
- :func:`download_if_evicted` — best-effort ``brctl download`` wrapper
  for cloud-only placeholders. The Wave 4 ``FilesystemSource`` already
  tolerates ``FileNotFoundError`` from eviction (PR #19); this helper
  is the proactive companion — call it before reading on macOS so
  iCloud has a chance to materialise the file first.

On non-macOS hosts every public function degrades to a safe no-op:
``is_icloud_path`` always returns ``False``, ``probe_tcc_access``
returns ``GRANTED``, ``open_privacy_settings`` does nothing,
``request_full_disk_access`` returns ``GRANTED``, and
``download_if_evicted`` returns ``True``. Callers can therefore use
this module unconditionally and the cross-platform install paths
stay simple.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
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
]


def is_macos() -> bool:
    """Return ``True`` when the current platform is macOS."""

    return sys.platform == "darwin"


# Canonical iCloud Drive mirror location on macOS. ``com~apple~CloudDocs``
# is the bundle identifier of the system Finder integration; user data
# (Documents, Desktop sync, etc.) all live under it.
MOBILE_DOCUMENTS_ROOT = Path.home() / "Library" / "Mobile Documents"
ICLOUD_DRIVE_ROOT = MOBILE_DOCUMENTS_ROOT / "com~apple~CloudDocs"


def _normalise(path: str | os.PathLike[str]) -> Path:
    """Resolve ``path`` relative to the user's home, expanding ``~``.

    We deliberately do NOT call ``Path.resolve()`` — that follows
    symlinks, and the user's setup may symlink iCloud paths to a
    shorter alias (e.g. ``~/Workspace -> Library/Mobile Documents/...``).
    Following the symlink would expose the iCloud-rooted path
    correctly, BUT it would also fail on broken symlinks during
    detection. ``.expanduser()`` + ``.absolute()`` keeps the textual
    path the user wrote while still letting us answer the question
    deterministically.
    """

    return Path(path).expanduser().absolute()


def is_icloud_path(path: str | os.PathLike[str]) -> bool:
    """Return ``True`` when ``path`` lives under macOS iCloud Drive's mirror.

    Walks the path's parents up to ``~/Library/Mobile Documents/com~apple~CloudDocs``.
    On non-macOS hosts this always returns ``False``. The check is
    intentionally textual (no ``stat`` / no symlink resolution) so it
    can run cheaply on every configured root without hitting TCC.

    Symlinks pointed *into* iCloud Drive (e.g. ``~/Workspace -> Library/Mobile Documents/...``)
    are NOT followed by this function — pass the resolved target if
    you want symlink-following semantics. The companion
    :func:`is_iclouddrive_managed` is broader; this one is strict
    about the CloudDocs container specifically.
    """

    if not is_macos():
        return False
    target = _normalise(path)
    try:
        target.relative_to(ICLOUD_DRIVE_ROOT.expanduser().absolute())
    except ValueError:
        return False
    return True


def is_iclouddrive_managed(path: str | os.PathLike[str]) -> bool:
    """Return ``True`` for any ``Mobile Documents``-rooted path.

    Broader than :func:`is_icloud_path` — also matches third-party
    iCloud Drive providers like Obsidian
    (``iCloud~md~obsidian/Documents``). These paths are subject to
    the same TCC gate as the system CloudDocs container.
    """

    if not is_macos():
        return False
    target = _normalise(path)
    try:
        target.relative_to(MOBILE_DOCUMENTS_ROOT.expanduser().absolute())
    except ValueError:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# TCC probing
# ─────────────────────────────────────────────────────────────────────


class TccProbeOutcome(StrEnum):
    """Outcome of a TCC access probe."""

    GRANTED = "granted"  # the read succeeded; the caller has access.
    DENIED = "denied"  # macOS returned EPERM; TCC is blocking us.
    MISSING = "missing"  # the probed path doesn't exist.
    NOT_APPLICABLE = "not_applicable"  # non-macOS host.
    ERROR = "error"  # unexpected I/O error; details in ``message``.


@dataclass(frozen=True)
class TccProbeResult:
    """Structured result of :func:`probe_tcc_access`."""

    outcome: TccProbeOutcome
    path: Path
    message: str = ""

    @property
    def granted(self) -> bool:
        """``True`` when the caller can read the probed path."""

        return self.outcome in (
            TccProbeOutcome.GRANTED,
            TccProbeOutcome.NOT_APPLICABLE,
        )

    @property
    def denied(self) -> bool:
        """``True`` when macOS returned EPERM (TCC block)."""

        return self.outcome is TccProbeOutcome.DENIED


def probe_tcc_access(path: str | os.PathLike[str]) -> TccProbeResult:
    """Non-destructively probe whether the caller can read ``path``.

    Strategy:

    1. On non-macOS hosts, return ``NOT_APPLICABLE`` — there is no TCC.
    2. If ``path`` doesn't exist (or is a directory we can list), we
       can't probe its content. Walk it: try to read 1 byte from the
       first file inside. If none exists, return ``MISSING``.
    3. Open the file for read, pull 1 byte, close. ``PermissionError``
       with ``errno == 1`` (Operation not permitted) → ``DENIED``;
       any other ``OSError`` → ``ERROR``.

    Files in iCloud-evicted state appear as ``FileNotFoundError`` to
    POSIX ``open()`` calls — those land in ``ERROR`` rather than
    ``DENIED``, which is the right surface for the install handshake
    (TCC and eviction are different problems with different fixes).
    """

    if not is_macos():
        return TccProbeResult(
            outcome=TccProbeOutcome.NOT_APPLICABLE,
            path=_normalise(path),
            message="not a macOS host; no TCC layer to probe",
        )

    target = _normalise(path)
    probe_target = _resolve_probe_target(target)
    if probe_target is None:
        return TccProbeResult(
            outcome=TccProbeOutcome.MISSING,
            path=target,
            message=(
                f"path {target} does not exist or contains no readable files; "
                "cannot probe TCC access without a real file to read"
            ),
        )

    try:
        with probe_target.open("rb") as fh:
            fh.read(1)
    except PermissionError as exc:
        if exc.errno == 1:
            return TccProbeResult(
                outcome=TccProbeOutcome.DENIED,
                path=probe_target,
                message=(
                    "macOS TCC blocked the read with [Errno 1] Operation "
                    "not permitted. Grant Full Disk Access to your "
                    "terminal app (or the corpus-forge binary) in "
                    "System Settings → Privacy & Security."
                ),
            )
        return TccProbeResult(
            outcome=TccProbeOutcome.ERROR,
            path=probe_target,
            message=f"PermissionError(errno={exc.errno}): {exc}",
        )
    except OSError as exc:
        return TccProbeResult(
            outcome=TccProbeOutcome.ERROR,
            path=probe_target,
            message=f"OSError(errno={exc.errno}): {exc}",
        )

    return TccProbeResult(
        outcome=TccProbeOutcome.GRANTED,
        path=probe_target,
        message="probe read 1 byte successfully",
    )


def _resolve_probe_target(path: Path) -> Path | None:
    """Find a real file at or under ``path`` we can read 1 byte from.

    Walks the directory shallowly (at most 64 entries per level, 4
    levels deep) so corpus-forge doesn't burn time recursing huge
    trees just to probe a permission. Returns ``None`` when nothing
    readable is reachable.
    """

    if path.is_file():
        return path
    if not path.is_dir():
        return None
    # Breadth-first, shallow walk. Stop at first hit.
    queue: list[tuple[Path, int]] = [(path, 0)]
    max_depth = 4
    max_entries_per_dir = 64
    while queue:
        current, depth = queue.pop(0)
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            # Likely TCC at the directory level — surface as a denial
            # by returning the directory itself; ``open()`` on it will
            # produce the canonical ``EPERM`` shape.
            return current
        for entry in entries[:max_entries_per_dir]:
            if entry.is_file():
                return entry
            if entry.is_dir() and depth < max_depth:
                queue.append((entry, depth + 1))
    return None


# ─────────────────────────────────────────────────────────────────────
# Privacy pane opener
# ─────────────────────────────────────────────────────────────────────


class PrivacyPane(StrEnum):
    """Which Privacy pane to open in System Settings."""

    FULL_DISK_ACCESS = "AllFiles"
    FILES_AND_FOLDERS = "FilesAndFolders"


_PRIVACY_PANE_URLS: dict[PrivacyPane, str] = {
    PrivacyPane.FULL_DISK_ACCESS: (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles"
    ),
    PrivacyPane.FILES_AND_FOLDERS: (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_FilesAndFolders"
    ),
}


def open_privacy_settings(pane: PrivacyPane = PrivacyPane.FULL_DISK_ACCESS) -> bool:
    """Open System Settings to the requested Privacy pane.

    Returns ``True`` when the open command was issued successfully,
    ``False`` otherwise. On non-macOS hosts this is a no-op that
    returns ``False`` (nothing to open).
    """

    if not is_macos():
        return False
    url = _PRIVACY_PANE_URLS.get(pane, _PRIVACY_PANE_URLS[PrivacyPane.FULL_DISK_ACCESS])
    try:
        subprocess.run(["open", url], check=False, capture_output=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# Install-time handshake
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FullDiskAccessRequest:
    """Outcome of :func:`request_full_disk_access`.

    Carries the probe result, whether System Settings was opened, and
    a human-readable instruction string the caller can show to the
    user verbatim.
    """

    probe: TccProbeResult
    opened_settings: bool
    instruction: str

    @property
    def granted(self) -> bool:
        return self.probe.granted


def request_full_disk_access(
    paths: Iterable[str | os.PathLike[str]],
    *,
    open_settings_on_denial: bool = True,
) -> FullDiskAccessRequest:
    """Probe each path; on TCC denial, open System Settings and tell the user.

    Picks the first path that actually triggers a denial (or the first
    iCloud-rooted path if none do) for the probe report. Returns a
    structured result the caller can render with whatever UI they have
    (rich console, plain print, JSON for agent mode).

    The ``instruction`` field always names the absolute binary path of
    the running interpreter so the user has the exact entry to add to
    the Full Disk Access list. It also names ``corpus-forge`` itself
    when the binary is on ``PATH``, since some users want to grant
    access to that specifically rather than their terminal.
    """

    if not is_macos():
        return FullDiskAccessRequest(
            probe=TccProbeResult(
                outcome=TccProbeOutcome.NOT_APPLICABLE,
                path=Path("/"),
                message="not a macOS host",
            ),
            opened_settings=False,
            instruction="No action needed — TCC only applies on macOS.",
        )

    normalised = [_normalise(p) for p in paths]
    icloud_paths = [p for p in normalised if is_iclouddrive_managed(p)]
    candidates = icloud_paths or normalised
    if not candidates:
        return FullDiskAccessRequest(
            probe=TccProbeResult(
                outcome=TccProbeOutcome.GRANTED,
                path=Path.home(),
                message="no paths to probe; nothing to do",
            ),
            opened_settings=False,
            instruction="No iCloud-rooted paths configured; TCC handshake skipped.",
        )

    chosen: TccProbeResult | None = None
    for candidate in candidates:
        result = probe_tcc_access(candidate)
        if result.denied:
            chosen = result
            break
        chosen = chosen or result

    assert chosen is not None  # ``candidates`` is non-empty.

    if chosen.granted:
        return FullDiskAccessRequest(
            probe=chosen,
            opened_settings=False,
            instruction=(
                "Full Disk Access already granted — corpus-forge can read "
                "the configured iCloud Drive paths."
            ),
        )

    opened = False
    if open_settings_on_denial:
        opened = open_privacy_settings(PrivacyPane.FULL_DISK_ACCESS)

    interpreter = Path(sys.executable).resolve()
    corpus_forge_bin = shutil.which("corpus-forge")
    binary_lines = [f"    Python interpreter:  {interpreter}"]
    if corpus_forge_bin:
        binary_lines.append(f"    corpus-forge binary: {corpus_forge_bin}")

    instruction = "\n".join(
        [
            "macOS TCC is blocking corpus-forge from reading the configured",
            "iCloud Drive paths. To fix:",
            "",
            "  1. Open System Settings → Privacy & Security → Full Disk Access.",
            ("     (Already opened for you.)" if opened else "     (Open it manually.)"),
            "  2. Click the [+] button and add YOUR TERMINAL APP",
            "     (iTerm2 / Terminal / Ghostty / Alacritty / Warp / ...).",
            "     Granting the terminal's bundle is the most reliable fix",
            "     because corpus-forge inherits TCC from its parent process.",
            "  3. Restart that terminal (and any running corpus-forge processes)",
            "     so the new grant applies.",
            "",
            "If you'd rather grant the corpus-forge binary directly, add:",
            *binary_lines,
            "",
            "Verify with:  corpus-forge doctor  (the icloud_access check),",
            "or simply re-run  corpus-forge setup  to repeat the handshake.",
        ]
    )

    return FullDiskAccessRequest(
        probe=chosen,
        opened_settings=opened,
        instruction=instruction,
    )


# ─────────────────────────────────────────────────────────────────────
# Eviction (brctl download) — proactive companion to PR #19
# ─────────────────────────────────────────────────────────────────────


def download_if_evicted(
    path: str | os.PathLike[str],
    *,
    timeout_s: float = 120.0,
) -> bool:
    """Best-effort ``brctl download`` of an iCloud-evicted file.

    Returns ``True`` when the file is locally available after the call
    (either it already was, or the download succeeded), ``False``
    otherwise. On non-macOS hosts this is a no-op that returns
    ``True`` (no iCloud to manage).

    ``brctl`` is the macOS BSD-licensed cloud-doc tool. It was
    soft-deprecated in macOS 13 but still works on Sequoia (15.x)
    and Sonoma (14.x) at the time of writing. The deprecation is
    tracked upstream; when Apple removes the binary, this helper
    will degrade to a no-op (``brctl`` missing) and the existing
    eviction-tolerance in :class:`FilesystemSource` (PR #19) will
    keep the ingest from crashing.
    """

    if not is_macos():
        return True

    target = _normalise(path)
    if target.exists():
        return True
    if not is_iclouddrive_managed(target):
        # Not an iCloud path; ``brctl`` won't help. Surface the
        # caller's existing "missing file" handling.
        return False

    brctl = shutil.which("brctl")
    if brctl is None:
        return target.exists()

    try:
        subprocess.run(
            [brctl, "download", str(target)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return target.exists()

    return target.exists()
