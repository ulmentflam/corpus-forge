"""Doctor checks — pure functions returning ``CheckResult``."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from corpus_forge import __version__

if TYPE_CHECKING:
    from rich.console import Console

    from corpus_forge.config import Config

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "corpus-forge" / "config.toml"


class CheckStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


# Wave-2 mapping from check status to Rich theme style.  Lives alongside
# the enum so a new status auto-fails (KeyError) until the mapping is
# explicit — easier than silently rendering it muted.
_STYLE_BY_STATUS: dict[CheckStatus, str] = {
    CheckStatus.OK: "success",
    CheckStatus.WARN: "warn",
    CheckStatus.FAIL: "error",
    CheckStatus.SKIP: "muted",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""


@dataclass(frozen=True)
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(r.status in (CheckStatus.OK, CheckStatus.SKIP) for r in self.results)

    def render(self) -> str:
        """Return the plain-text report.

        Kept stable across Phase L Wave 2 because
        ``tests/unit/test_doctor.py::test_render_includes_status_markers``
        and any downstream consumers bind to the ``[OK  ]`` /
        ``[WARN]`` / ``[FAIL]`` / ``[SKIP]`` substring contract.  Wave 3
        will split this into ``render_human(console)`` + ``to_json()``
        and may demote ``render`` to a deprecation shim.
        """
        lines = [f"corpus-forge doctor — version {__version__}", ""]
        for r in self.results:
            lines.append(f"  [{r.status.value:4}] {r.name}: {r.detail}")
        lines.append("")
        lines.append("Healthy" if self.healthy else "Issues detected — see above.")
        return "\n".join(lines)

    def _summary(self) -> str:
        """Aggregate status: ``"fail"`` > ``"warn"`` > ``"ok"``.

        SKIP and OK both count as ``"ok"`` for the purpose of the
        summary — they're explicit "this check did not block".
        """
        statuses = {r.status for r in self.results}
        if CheckStatus.FAIL in statuses:
            return "fail"
        if CheckStatus.WARN in statuses:
            return "warn"
        return "ok"

    def to_json(self) -> dict[str, object]:
        """Serialize the report as a JSON-friendly dict.

        Wave 3 shape, intended to be the same payload ``bug-report``
        (Wave 6) snapshots. Status values stay UPPERCASE (matching the
        ``CheckStatus`` enum literals) so downstream consumers can
        switch on the same strings the human render shows.
        """
        return {
            "checks": [
                {"name": r.name, "status": r.status.value, "detail": r.detail} for r in self.results
            ],
            "summary": self._summary(),
            "version": __version__,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }

    def render_styled(self, console: Console) -> None:
        """Print the report to ``console`` with semantic Rich styling.

        Same line content as :meth:`render` — status pills keep the
        ``[OK  ]`` / ``[WARN]`` / ``[FAIL]`` / ``[SKIP]`` shape so
        substring assertions still bind under ``NO_COLOR=1`` — but the
        pill prefix carries a semantic style (success / warn / error /
        info) and the heading + summary are dimmed, so the human
        terminal output gets color.
        """
        console.print(f"[muted]corpus-forge doctor — version {__version__}[/muted]")
        console.print("")
        for r in self.results:
            style = _STYLE_BY_STATUS[r.status]
            pill = f"[{r.status.value:4}]"
            # Markup must escape literal brackets in the detail so a
            # path like ``[ocr]`` does not get parsed as Rich markup.
            safe_detail = r.detail.replace("[", r"\[")
            console.print(f"  [{style}]{pill}[/{style}] {r.name}: {safe_detail}")
        console.print("")
        if self.healthy:
            console.print("[success]Healthy[/success]")
        else:
            console.print("[error]Issues detected — see above.[/error]")


# ── individual checks ─────────────────────────────────────────────────


def _check_python_version() -> CheckResult:
    """Python ≥3.11 + <3.14 (matches pyproject.toml's requires-python)."""
    v = sys.version_info
    if (3, 11) <= (v.major, v.minor) < (3, 14):
        return CheckResult("python", CheckStatus.OK, f"{v.major}.{v.minor}.{v.micro}")
    return CheckResult(
        "python",
        CheckStatus.FAIL,
        f"{v.major}.{v.minor}.{v.micro} — corpus-forge needs >=3.11,<3.14",
    )


def _check_config_present(config_path: Path) -> CheckResult:
    """Config file exists + parses cleanly."""
    if not config_path.exists():
        return CheckResult(
            "config",
            CheckStatus.WARN,
            f"{config_path} not found — run `corpus-forge setup`",
        )
    try:
        tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return CheckResult("config", CheckStatus.FAIL, f"{config_path}: {exc}")
    return CheckResult("config", CheckStatus.OK, str(config_path))


def _check_poppler() -> CheckResult:
    """``poppler-utils`` (provides ``pdftoppm``) for the [ocr] PDF
    rasterisation path. SKIP when no config or no ocr backend.
    """
    if shutil.which("pdftoppm"):
        return CheckResult("poppler", CheckStatus.OK, "pdftoppm on PATH")
    return CheckResult(
        "poppler",
        CheckStatus.WARN,
        "pdftoppm not on PATH — needed for [ocr] extra (PDF rasterisation)",
    )


def _check_ffmpeg() -> CheckResult:
    """``ffmpeg`` for Whisper audio decode. ``imageio-ffmpeg`` bundles
    a static binary; the system one is optional.
    """
    if shutil.which("ffmpeg"):
        return CheckResult("ffmpeg", CheckStatus.OK, "ffmpeg on PATH")
    try:
        # imageio-ffmpeg ships a bundled binary used by faster-whisper.
        # pyrefly: ignore[missing-import]  # optional [whisper] extra
        import imageio_ffmpeg  # noqa: PLC0415

        return CheckResult(
            "ffmpeg",
            CheckStatus.OK,
            f"bundled via imageio-ffmpeg {imageio_ffmpeg.__version__}",
        )
    except ImportError:
        return CheckResult(
            "ffmpeg",
            CheckStatus.WARN,
            "no system ffmpeg + imageio-ffmpeg not installed",
        )


def _check_uv() -> CheckResult:
    """``uv`` itself — needed for ``corpus-forge update`` on uv-tool / source installs."""
    if not shutil.which("uv"):
        return CheckResult("uv", CheckStatus.WARN, "uv not on PATH — needed for self-update")
    try:
        out = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True, timeout=2, check=False
        )
        return CheckResult("uv", CheckStatus.OK, out.stdout.strip() or "uv installed")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return CheckResult("uv", CheckStatus.WARN, f"{exc}")


# ── Phase L Wave 6 — daemon activity ──────────────────────────────────


import re as _re  # noqa: E402

from corpus_forge.logging_config import get_log_dir  # noqa: E402

# Matches the standard Wave-1 rotating-log line shape.
_DAEMON_LINE_RE = _re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?\s+"
    r"\[(?P<level>[A-Z]+)\s*\]\s+\S+:\s+(?P<msg>.*)$"
)

# Human-time bucket thresholds (seconds).
_SECS_PER_MIN: int = 60
_SECS_PER_HOUR: int = 60 * 60
_SECS_PER_DAY: int = 24 * 60 * 60


def _check_daemon_activity() -> CheckResult:
    """Surface the most recent INFO line from ``daemon.log``.

    SKIP when the file doesn't exist (the daemon may simply not be
    running yet, which is the common new-user case).  OK when the
    most-recent INFO line is parseable; WARN when the file exists but
    no INFO line could be parsed (corrupt? truncated?).
    """

    try:
        log_path = get_log_dir() / "daemon.log"
    except Exception as exc:  # pragma: no cover — defensive
        return CheckResult(
            "daemon_activity",
            CheckStatus.SKIP,
            f"no daemon log (unable to resolve log dir: {exc})",
        )

    if not log_path.exists():
        return CheckResult(
            "daemon_activity",
            CheckStatus.SKIP,
            "no daemon log (daemon never started or log rotated away)",
        )

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CheckResult(
            "daemon_activity",
            CheckStatus.WARN,
            f"unable to read {log_path}: {exc}",
        )

    last_info: tuple[str, str] | None = None  # (ts, msg)
    for line in reversed(text.splitlines()):
        m = _DAEMON_LINE_RE.match(line)
        if not m or m.group("level") != "INFO":
            continue
        last_info = (m.group("ts"), m.group("msg"))
        break

    if last_info is None:
        return CheckResult(
            "daemon_activity",
            CheckStatus.SKIP,
            "daemon log present but no INFO lines parsed",
        )

    ts_str, msg = last_info
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        delta = datetime.now() - ts
        secs = int(delta.total_seconds())
        if secs < 0:
            human = ts_str
        elif secs < _SECS_PER_MIN:
            human = f"{secs}s ago"
        elif secs < _SECS_PER_HOUR:
            human = f"{secs // _SECS_PER_MIN}m ago"
        elif secs < _SECS_PER_DAY:
            human = f"{secs // _SECS_PER_HOUR}h ago"
        else:
            human = f"{secs // _SECS_PER_DAY}d ago"
        detail = f"Last activity: {human} — {msg}"
    except (ValueError, OSError):
        detail = f"Last activity: {ts_str} — {msg}"

    return CheckResult("daemon_activity", CheckStatus.OK, detail)


# ── Phase M Wave 1 — .corpusignore check ──────────────────────────────


def _check_corpusignore(cfg: Config) -> CheckResult:
    """Validate every FS-style data root's ``.corpusignore`` file.

    Status logic:

    - ``SKIP`` when no FS-style data root is configured (chat-only or
      Zotero-only datasets).
    - ``FAIL`` when any root's ``.corpusignore`` exists but cannot be
      parsed (``OSError`` or unreadable line).
    - ``WARN`` when any root's file is missing or the managed block has
      drifted from the current feature flags.
    - ``OK`` when every root has a parseable, sentinel-bearing file
      whose managed block matches :func:`default_managed_lines`.
    """
    # Local imports keep doctor's import-time cost bounded and avoid
    # circular imports with corpus_forge.config (which imports doctor
    # in some legacy code paths).
    from corpus_forge.ignore import CorpusIgnore  # noqa: PLC0415
    from corpus_forge.ignore_defaults import (  # noqa: PLC0415
        default_managed_lines,
        feature_flags_from_config,
        parse_managed_lines,
    )
    from corpus_forge.ignore_lifecycle import discover_data_roots  # noqa: PLC0415

    roots = discover_data_roots(cfg)
    if not roots:
        return CheckResult(
            "corpusignore",
            CheckStatus.SKIP,
            "no filesystem-style data root configured",
        )

    features = feature_flags_from_config(cfg)
    expected_lines = set(default_managed_lines(features))

    warnings: list[str] = []
    failures: list[str] = []
    ok_roots: list[Path] = []

    for root in roots:
        target = root / ".corpusignore"
        if not target.exists():
            warnings.append(f"{target} missing — run `corpus-forge ignore init`")
            continue
        try:
            CorpusIgnore.from_file(target, root=root)
        except (OSError, ValueError) as exc:
            failures.append(f"{target}: parse failure on line near {exc}")
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        body = parse_managed_lines(text)
        if body is None:
            warnings.append(
                f"{target}: managed block sentinels not found — "
                "run `corpus-forge ignore sync` to install"
            )
            continue
        body_set = {line for line in body if not line.startswith("#")}
        if not expected_lines.issubset(body_set):
            warnings.append(f"{target}: managed block drifted — run `corpus-forge ignore sync`")
            continue
        # Also flag extras within the managed block as drift — the user
        # may have hand-edited.
        extras = body_set - expected_lines
        # Allow only the timestamp comment + empty marker; non-empty
        # non-comment lines are unexpected.
        if extras:
            warnings.append(f"{target}: managed block drifted — run `corpus-forge ignore sync`")
            continue
        ok_roots.append(root)

    if failures:
        return CheckResult("corpusignore", CheckStatus.FAIL, "; ".join(failures))
    if warnings:
        return CheckResult("corpusignore", CheckStatus.WARN, "; ".join(warnings))
    return CheckResult(
        "corpusignore",
        CheckStatus.OK,
        f"{len(ok_roots)} data root(s) synced",
    )


# ── orchestrator ──────────────────────────────────────────────────────


_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _check_python_version,
    _check_uv,
    _check_poppler,
    _check_ffmpeg,
    _check_daemon_activity,
)


def _try_load_config(config_path: Path) -> Config | None:
    """Best-effort :class:`Config` load for doctor's per-config checks.

    Returns None on any failure (missing file, validation error). The
    individual checks that need a real Config skip themselves when the
    load returns None.
    """
    if not config_path.exists():
        return None
    try:
        from corpus_forge.config import Config  # noqa: PLC0415

        return Config.load(config_path=config_path, secrets_path=config_path.parent / "secrets.env")
    except Exception:  # pragma: no cover — broad-except by design
        return None


def run_doctor(*, config_path: Path | None = None) -> DoctorReport:
    """Run every registered check and return the aggregated report."""
    cfg = config_path if config_path is not None else DEFAULT_CONFIG_PATH
    results = [_check_config_present(cfg)]
    results.extend(check() for check in _CHECKS)
    # Phase M Wave 1: corpusignore check needs a parsed Config to
    # discover FS roots and the active feature flags. Skip silently
    # when the load fails (the config check above already reports it).
    loaded_cfg = _try_load_config(cfg)
    if loaded_cfg is None:
        results.append(
            CheckResult(
                "corpusignore",
                CheckStatus.SKIP,
                "skipped (config not loaded)",
            )
        )
    else:
        results.append(_check_corpusignore(loaded_cfg))
    return DoctorReport(results=results)
