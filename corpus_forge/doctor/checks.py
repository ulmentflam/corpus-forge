"""Doctor checks — pure functions returning ``CheckResult``."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from corpus_forge import __version__

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "corpus-forge" / "config.toml"


class CheckStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


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
        lines = [f"corpus-forge doctor — version {__version__}", ""]
        for r in self.results:
            lines.append(f"  [{r.status.value:4}] {r.name}: {r.detail}")
        lines.append("")
        lines.append("Healthy" if self.healthy else "Issues detected — see above.")
        return "\n".join(lines)


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


# ── orchestrator ──────────────────────────────────────────────────────


_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _check_python_version,
    _check_uv,
    _check_poppler,
    _check_ffmpeg,
)


def run_doctor(*, config_path: Path | None = None) -> DoctorReport:
    """Run every registered check and return the aggregated report."""
    cfg = config_path if config_path is not None else DEFAULT_CONFIG_PATH
    results = [_check_config_present(cfg)]
    results.extend(check() for check in _CHECKS)
    return DoctorReport(results=results)
