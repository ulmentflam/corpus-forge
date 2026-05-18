"""``corpus-forge bug-report`` bundler (Phase L Wave 6).

Produces a redacted zip the user (or an agent triaging an issue)
can attach to a GitHub issue.  Bundle contents — every file passes
through the :mod:`corpus_forge.diagnostics.redact` sweep before
landing in the zip:

- ``README.txt`` — "start here" greeting for the recipient.
- ``manifest.json`` — version, OS, Python, hostname-hash, timestamps.
- ``doctor.json`` — :meth:`DoctorReport.to_json` output.
- ``config.redacted.toml`` — secrets-stripped config (tomlkit walk).
- ``logs/<component>.log.txt`` — tail of each rotating log that exists.
- ``logs/recent_events.txt`` — flushed in-memory ring buffer.
- ``env.txt`` — filtered ``os.environ`` slice (CF_*, OLLAMA_*, agent).
- ``deps.txt`` — ``pip list --format=freeze`` (or importlib fallback).
- ``db_summary.json`` — counts only (no row content). Optional via
  ``--no-db``.

Filename pattern: ``corpus-forge-bugreport-<YYYY-MM-DD>-<short-hash>.zip``
in the CWD by default.  ``<short-hash>`` is the first 8 hex chars of
``sha256(manifest_json_bytes)`` — deterministic from input data, so
identical manifest content yields identical filenames.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from corpus_forge import __version__
from corpus_forge.diagnostics.redact import redact_string
from corpus_forge.logging_config import get_log_dir, get_ring_buffer
from corpus_forge.ui import ok as ui_ok
from corpus_forge.ui.console import console as ui_console

logger = logging.getLogger(__name__)

# Env-var prefixes that are interesting for bug-report (kept verbatim,
# values still pass through ``redact_string``).
_ENV_PREFIXES: tuple[str, ...] = (
    "CF_",
    "OLLAMA_",
    "CLAUDECODE",
    "AI_AGENT",
    "AGENT",
    "OPENCODE",
    "GEMINI_CLI",
    "COPILOT_CLI",
    "CODEX_",
)

# Log components we care about.  Order is deterministic so the zip's
# ``namelist()`` is stable.
_LOG_COMPONENTS: tuple[str, ...] = ("cli", "daemon", "mcp", "embed-worker")

# Tail size (bytes) per rotating log captured into the bundle.
_LOG_TAIL_BYTES: int = 2 * 1024 * 1024

_ISSUE_URL_BASE = "https://github.com/ulmentflam/corpus-forge/issues/new"


@dataclass(frozen=True)
class BugReport:
    """Public result of :func:`collect`."""

    path: Path
    redacted_count: int
    short_hash: str
    issue_url: str


# ── Pure helpers (patched in tests) ─────────────────────────────────


def _now_iso() -> str:
    """Return a UTC ISO-8601 timestamp with millisecond precision."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _hostname_hash() -> str:
    """16-char prefix of ``sha256(socket.gethostname())``."""

    return hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16]


def _short_hash(content: bytes) -> str:
    """8-char prefix of ``sha256(content)``."""

    return hashlib.sha256(content).hexdigest()[:8]


def _collect_env() -> str:
    """Filter ``os.environ`` to interesting keys; redact values."""

    lines: list[str] = []
    for key in sorted(os.environ):
        if any(key.startswith(p) or key == p for p in _ENV_PREFIXES):
            raw_value = os.environ[key]
            value, _ = redact_string(raw_value)
            lines.append(f"{key}={value}")
    return "\n".join(lines) + ("\n" if lines else "")


def _collect_deps() -> str:
    """Try ``pip list``, fall back to ``importlib.metadata``."""

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover — defensive
        pass

    # Fallback: importlib.metadata.distributions()
    try:
        items = sorted(
            (dist.metadata["Name"], dist.version)
            for dist in importlib.metadata.distributions()
            if dist.metadata and dist.metadata.get("Name")
        )
        return "\n".join(f"{name}=={ver}" for name, ver in items) + "\n"
    except Exception as exc:  # pragma: no cover — defensive
        return f"unable to enumerate deps: {exc}\n"


def _collect_doctor_json() -> dict:
    """Snapshot the doctor report. Best-effort — never raises."""

    try:
        from corpus_forge.doctor import run_doctor  # noqa: PLC0415

        return run_doctor().to_json()
    except Exception as exc:  # pragma: no cover — defensive
        return {"unavailable": str(exc)}


def _collect_db_summary() -> dict:
    """Counts-only DB snapshot. Best-effort — backend may be unreachable."""

    try:
        from corpus_forge.config import Config  # noqa: PLC0415

        config = Config.load()
        backend = _resolve_backend(config)
        summary: dict = {
            "datasets": _safe_count(backend, "SELECT COUNT(*) AS n FROM datasets"),
            "documents": _safe_count(backend, "SELECT COUNT(*) AS n FROM documents"),
            "chunks": _safe_count(backend, "SELECT COUNT(*) AS n FROM chunks"),
            "embedders": [],
        }
        try:
            rows = backend._execute("SELECT name, dimension, table_name FROM embedders")
            for row in rows:
                summary["embedders"].append(
                    {
                        "name": row["name"],
                        "dimension": row["dimension"],
                        "table_name": row["table_name"],
                    }
                )
        except Exception as exc:
            summary["embedders"] = [{"unavailable": str(exc)}]
        return summary
    except Exception as exc:
        return {"unavailable": str(exc)}


def _resolve_backend(config):  # pragma: no cover — exercised via test_bug_report_db_path
    """Resolve any backend from a loaded Config (best-effort)."""

    kind = getattr(getattr(config, "backend", None), "kind", "sqlite")
    if kind == "postgres":
        from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415

        return PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
    from corpus_forge.backends.sqlite import SQLiteBackend  # noqa: PLC0415

    return SQLiteBackend(path=getattr(config.backend, "path", ":memory:"))


def _safe_count(backend, sql: str) -> int:  # pragma: no cover — exercised via DB path
    try:
        rows = backend._execute(sql)
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        return 0


def _collect_service_status() -> str:
    """Render ``service status`` to a string for the bug-report bundle.

    Routes the standard status renderer at a captured Rich :class:`Console`
    so the output matches what a user would see locally.  Failures are
    swallowed — bug-report should never fail because the daemon log is
    missing.
    """

    try:
        import io  # noqa: PLC0415

        from rich.console import Console  # noqa: PLC0415

        from corpus_forge.admin.service import render_status  # noqa: PLC0415
        from corpus_forge.ui.theme import build_theme  # noqa: PLC0415

        buffer = io.StringIO()
        console = Console(
            file=buffer,
            width=100,
            force_terminal=False,
            color_system=None,
            theme=build_theme(),
        )
        render_status(console=console)
        return buffer.getvalue() or "(no service status output)\n"
    except Exception as exc:  # pragma: no cover — best-effort.
        return f"(unable to render service status: {exc})\n"


def _collect_recent_events() -> str:
    """Flush the in-memory ring buffer into a text dump."""

    try:
        ring = get_ring_buffer()
        # MemoryHandler keeps records in .buffer; format each.
        formatter = logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        lines = []
        for record in list(ring.buffer)[-200:]:
            try:
                line = formatter.format(record)
            except Exception as exc:  # pragma: no cover — defensive
                line = f"<unformattable record: {exc}>"
            lines.append(line)
        return ("\n".join(lines) + ("\n" if lines else "")) or "(ring buffer empty)\n"
    except Exception as exc:  # pragma: no cover
        return f"unable to read ring buffer: {exc}\n"


def _collect_config_toml() -> str:
    """Snapshot the user's config.toml with secret-named keys redacted.

    Returns the rendered string (empty when the config is missing).
    """

    try:
        import tomlkit  # noqa: PLC0415

        from corpus_forge.diagnostics.redact import redact_toml_dict  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — tomlkit is in deps
        return f"(unable to import tomlkit: {exc})\n"

    # Default: ~/.config/corpus-forge/config.toml (matches Config.load).
    candidates = [
        Path(os.environ.get("CF_CONFIG", "")).expanduser() if os.environ.get("CF_CONFIG") else None,
        Path.home() / ".config" / "corpus-forge" / "config.toml",
    ]
    config_path = next((p for p in candidates if p and p.exists()), None)
    if not config_path:
        return "(no config.toml found)\n"

    try:
        raw = config_path.read_text(encoding="utf-8")
        doc = tomlkit.parse(raw)
        redact_toml_dict(doc)
        rendered, _ = redact_string(tomlkit.dumps(doc))
        return rendered
    except Exception as exc:
        return f"(unable to read {config_path}: {exc})\n"


def _tail_log(path: Path, max_bytes: int = _LOG_TAIL_BYTES) -> str:
    """Return up to ``max_bytes`` from the tail of ``path``.

    Uses ``errors=replace`` so binary garbage doesn't break decoding.
    """

    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(-max_bytes, 2)
            # Drop the first (probably partial) line.
            fh.readline()
        data = fh.read()
    return data.decode("utf-8", errors="replace")


# ── Public API ──────────────────────────────────────────────────────


def collect(
    *,
    out: Path | None = None,
    include_logs: bool = True,
    include_db: bool = True,
    zip_bundle: bool = True,
) -> BugReport:
    """Bundle a redacted bug-report.

    Args:
        out: Destination path (zip file or directory depending on
            ``zip_bundle``).  When None, writes to CWD with the
            deterministic filename pattern.
        include_logs: When False, the ``logs/`` subdir is omitted.
        include_db: When False, ``db_summary.json`` is omitted.
        zip_bundle: When False, the staging directory is returned
            uncompressed (handy when an agent will read files
            individually).

    Returns:
        :class:`BugReport` carrying the produced path, redaction
        count, short hash, and prefilled GitHub issue URL.
    """

    with tempfile.TemporaryDirectory(prefix="cf-bugreport-") as staging:
        staging_dir = Path(staging)
        total_redactions = 0
        redaction_log: list[str] = []

        # 1. doctor.json
        doctor_payload = _collect_doctor_json()
        doctor_text = json.dumps(doctor_payload, default=str, indent=2)
        doctor_text, n = redact_string(doctor_text)
        total_redactions += n
        if n:
            redaction_log.append(f"doctor.json:{n}")
        (staging_dir / "doctor.json").write_text(doctor_text, encoding="utf-8")

        # 2. config.redacted.toml
        config_text = _collect_config_toml()
        config_text, n = redact_string(config_text)
        total_redactions += n
        if n:
            redaction_log.append(f"config.redacted.toml:{n}")
        (staging_dir / "config.redacted.toml").write_text(config_text, encoding="utf-8")

        # 3. logs/
        if include_logs:
            logs_dir = staging_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_dir = get_log_dir()
            for component in _LOG_COMPONENTS:
                src = log_dir / f"{component}.log"
                if not src.exists():
                    continue
                content = _tail_log(src)
                content, n = redact_string(content)
                total_redactions += n
                if n:
                    redaction_log.append(f"logs/{component}.log.txt:{n}")
                (logs_dir / f"{component}.log.txt").write_text(content, encoding="utf-8")
            # Ring buffer
            events_text = _collect_recent_events()
            events_text, n = redact_string(events_text)
            total_redactions += n
            if n:
                redaction_log.append(f"logs/recent_events.txt:{n}")
            (logs_dir / "recent_events.txt").write_text(events_text, encoding="utf-8")

        # 4a. service_status.txt — Wave-8 cross-cut.  Rendered through
        #     the same code path as the live ``service status`` so
        #     triage sees what the user sees.
        service_status_text = _collect_service_status()
        service_status_text, n = redact_string(service_status_text)
        total_redactions += n
        if n:
            redaction_log.append(f"service_status.txt:{n}")
        (staging_dir / "service_status.txt").write_text(service_status_text, encoding="utf-8")

        # 4. env.txt + deps.txt
        env_text = _collect_env()
        env_text, n = redact_string(env_text)
        total_redactions += n
        if n:
            redaction_log.append(f"env.txt:{n}")
        (staging_dir / "env.txt").write_text(env_text, encoding="utf-8")

        deps_text = _collect_deps()
        deps_text, n = redact_string(deps_text)
        total_redactions += n
        if n:
            redaction_log.append(f"deps.txt:{n}")
        (staging_dir / "deps.txt").write_text(deps_text, encoding="utf-8")

        # 5. db_summary.json
        if include_db:
            db_summary = _collect_db_summary()
            db_text = json.dumps(db_summary, default=str, indent=2)
            db_text, n = redact_string(db_text)
            total_redactions += n
            if n:
                redaction_log.append(f"db_summary.json:{n}")
            (staging_dir / "db_summary.json").write_text(db_text, encoding="utf-8")

        # 6. manifest.json — written AFTER everything else so it can
        #    reference the redaction log + so its content hash captures
        #    the same redactions across runs.
        # Phase L Wave 9 — record the live agent-mode detection so a
        # triaging agent can tell which surface produced these logs.
        try:
            from corpus_forge.ui.agent import (  # noqa: PLC0415
                current_detection as _agent_current,
            )

            _det = _agent_current()
            agent_mode_payload: dict[str, str] | str = {
                "client": _det.client.value,
                "signal": _det.signal,
                "raw_value": _det.raw_value,
            }
        except Exception:  # pragma: no cover — defensive
            agent_mode_payload = "human"

        manifest = {
            "corpus_forge_version": __version__,
            "os": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
            "arch": platform.machine(),
            "ts_utc": _now_iso(),
            "hostname_hash": _hostname_hash(),
            "tool_path": shutil.which("corpus-forge") or sys.argv[0],
            "redaction_log": redaction_log,
            "agent_mode_at_time_of_capture": agent_mode_payload,
        }
        manifest_bytes = json.dumps(manifest, default=str, indent=2).encode("utf-8")
        (staging_dir / "manifest.json").write_bytes(manifest_bytes)

        # 7. Compute the short hash from the manifest contents and write
        #    the README that references it.
        short = _short_hash(manifest_bytes)
        readme = (
            "corpus-forge bug-report bundle\n"
            "==============================\n\n"
            f"Bundle hash: {short}\n"
            f"Generated: {manifest['ts_utc']}\n\n"
            "Reader, start here:\n"
            "  1. manifest.json  — environment + redaction log.\n"
            "  2. doctor.json    — current health (Python, deps, system tools).\n"
            "  3. logs/recent_events.txt — the last 200 INFO+ events.\n"
            "  4. logs/<component>.log.txt — full tail per component.\n"
            "  5. config.redacted.toml — user config with secrets stripped.\n"
            "  6. service_status.txt — what `corpus-forge service status` would print.\n"
            "  7. db_summary.json — counts only (no row content).\n"
            "  8. env.txt / deps.txt — runtime + package versions.\n\n"
            "All secret-shaped strings (DSN passwords, API keys, bearer\n"
            "tokens) have been replaced with the marker `«redacted»`\n"
            "so a single grep over the bundle locates every site.\n"
        )
        (staging_dir / "README.txt").write_text(readme, encoding="utf-8")

        # 8. Output destination.
        date_str = manifest["ts_utc"][:10]  # YYYY-MM-DD
        cwd = Path.cwd()
        default_name_base = f"corpus-forge-bugreport-{date_str}-{short}"
        if zip_bundle:
            if out is None:
                final_path = cwd / f"{default_name_base}.zip"
            else:
                final_path = Path(out)
                if final_path.is_dir():
                    final_path = final_path / f"{default_name_base}.zip"
            _write_zip(staging_dir, final_path)
        else:
            final_path = cwd / default_name_base if out is None else Path(out)
            _copy_dir(staging_dir, final_path)

        issue_title = f"[bug-report {short}]"
        issue_url = f"{_ISSUE_URL_BASE}?template=bug.yml&title={urllib.parse.quote(issue_title)}"

        report = BugReport(
            path=final_path,
            redacted_count=total_redactions,
            short_hash=short,
            issue_url=issue_url,
        )

        # Friendly summary.
        try:
            ui_ok(f"Wrote {final_path.name} ({_human_bytes(_path_bytes(final_path))})")
            ui_ok(f"{total_redactions} secrets redacted")
            ui_console.print("")
            ui_console.print("Attach this file to a new issue at:")
            ui_console.print(f"  [accent.path]{issue_url}[/accent.path]")
        except Exception:  # pragma: no cover — console output is best-effort
            pass

        return report


def _write_zip(src_dir: Path, dest: Path) -> None:
    """Compress every file under ``src_dir`` into ``dest``."""

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(src_dir)))


def _copy_dir(src_dir: Path, dest: Path) -> None:
    """Recursively copy ``src_dir`` to ``dest``."""

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_dir, dest)


def _path_bytes(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


_BYTES_PER_UNIT: int = 1024


def _human_bytes(n: int) -> str:
    """Render ``n`` bytes as a human-readable string."""

    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    for unit in units:
        if size < _BYTES_PER_UNIT or unit == units[-1]:
            return f"{size:.0f} {unit}"
        size /= _BYTES_PER_UNIT
    return f"{size:.0f} GB"  # pragma: no cover


__all__ = [
    "BugReport",
    "collect",
]
