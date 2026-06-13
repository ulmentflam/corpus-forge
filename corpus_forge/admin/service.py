"""``corpus-forge service ...`` lifecycle verbs (Phase L Wave 8).

Replaces the bare ``corpus-forge daemon`` command with a proper
lifecycle group:

    service status     — Pid alive? Last log line? Memory? Datasets?
    service start      — Foreground (default) or ``--background``.
    service stop       — SIGTERM → wait 30s → SIGKILL.
    service restart    — Stop + start, preserves foreground/background mode.
    service logs       — Alias for ``corpus-forge logs tail --component daemon``.
    service install    — Generate (and optionally apply) a systemd unit /
                          launchd plist / Windows scheduled task.
    service uninstall  — Reverse of install.

The pid-file helpers + the optional psutil dependency are imported
lazily inside each verb so importing this module is side-effect-free
(matches the Wave 7 ``admin/`` convention).

The bare ``daemon`` command at :mod:`corpus_forge.cli` becomes a thin
deprecation alias that warns once and then forwards to
:func:`start_daemon_foreground`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from corpus_forge.admin import foreground as _fg
from corpus_forge.admin import service_install as _install
from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok
from corpus_forge.ui.console import warn as ui_warn

logger = logging.getLogger(__name__)


service_app = typer.Typer(
    help="Manage the corpus-forge background daemon (start/stop/status/install).",
    add_completion=False,
)


# ── Constants ───────────────────────────────────────────────────────────


DAEMON_COMPONENT: str = "daemon"

# Stop semantics.  SIGTERM polled for up to 30s, then SIGKILL.
_STOP_TIMEOUT_SECS: float = 30.0
_STOP_POLL_INTERVAL_SECS: float = 0.2

# ``signal.SIGKILL`` does not exist on Windows — Python's ``signal`` module
# only exposes ``SIGTERM`` / ``SIGINT`` / ``CTRL_*`` on win32.  ``os.kill``
# on Windows treats ``signal.SIGTERM`` as a graceful ``TerminateProcess``
# shim, which is the closest analog the platform has to SIGKILL.  We
# resolve the constant at module-import time so the escalation path
# becomes a no-op equivalent on Windows.
_SIGKILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)

# Default tail for the "last INFO line" in `service status`.
_LAST_LINE_TAIL_LINES: int = 200

# Match the Wave-1 log format prefix shape.
_DAEMON_LOG_LINE_RE = re.compile(r"^(?P<ts>\S+ \S+) \[(?P<level>[A-Z]+)\s*\]\s+\S+:\s+(?P<msg>.*)$")

# Human-time bucket thresholds (seconds).
_SECS_PER_MIN: int = 60
_SECS_PER_HOUR: int = 60 * 60
_SECS_PER_DAY: int = 24 * 60 * 60


# ── Mode state file (used by `service restart`) ─────────────────────────


def _mode_state_path() -> Path:
    """Where we stash the foreground/background flag from the last ``start``.

    Lives alongside ``daemon.pid`` so cleaning up the state dir takes both.
    """

    return _fg.state_dir() / f"{DAEMON_COMPONENT}.mode"


def _write_mode(mode: Literal["foreground", "background"]) -> None:
    _mode_state_path().write_text(mode, encoding="utf-8")


def _read_mode() -> Literal["foreground", "background"]:
    """Return the last recorded start mode (defaults to ``background``).

    ``background`` is the safer default: ``service restart`` without a
    state file should never block on Ctrl-C from a user who didn't
    intend to attach.
    """

    path = _mode_state_path()
    if not path.exists():
        return "background"
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "background"
    if raw == "foreground":
        return "foreground"
    return "background"


# ── psutil-optional helpers ─────────────────────────────────────────────


def _try_psutil():
    """Return the :mod:`psutil` module if importable, else ``None``."""

    try:
        import psutil

        return psutil
    except ImportError:
        return None


def _uptime_seconds(pid: int, pid_file: Path) -> float | None:
    """Best-effort uptime in seconds for ``pid``.

    Order of preference:

    1. ``psutil.Process(pid).create_time()`` if available.
    2. ``pid_file.stat().st_mtime`` — when the pid file was written.
    3. ``None`` if neither works.
    """

    psutil = _try_psutil()
    if psutil is not None:
        with contextlib.suppress(Exception):
            create = psutil.Process(pid).create_time()
            return max(0.0, time.time() - create)

    try:
        return max(0.0, time.time() - pid_file.stat().st_mtime)
    except OSError:
        return None


def _rss_bytes(pid: int) -> int | None:
    """Resident set size for ``pid`` in bytes, or ``None`` when psutil is missing."""

    psutil = _try_psutil()
    if psutil is None:
        return None
    try:
        return int(psutil.Process(pid).memory_info().rss)
    except Exception:
        return None


# ── Time / size formatting (tiny — kept here to avoid cross-module hop) ─


def _format_uptime(secs: float) -> str:
    """Render seconds as a short human string (``42s`` / ``5m`` / ``2h`` / ``3d``)."""

    s = int(secs)
    if s < _SECS_PER_MIN:
        return f"{s}s"
    if s < _SECS_PER_HOUR:
        return f"{s // _SECS_PER_MIN}m"
    if s < _SECS_PER_DAY:
        return f"{s // _SECS_PER_HOUR}h"
    return f"{s // _SECS_PER_DAY}d"


_KIB: int = 1024
_MIB: int = _KIB * _KIB


def _format_bytes(n: int) -> str:
    """Render ``n`` bytes as MB / GB with one decimal."""

    mb = n / _MIB
    if mb < _KIB:
        return f"{mb:.1f} MB"
    return f"{mb / _KIB:.2f} GB"


# ── daemon.log tail / last-INFO parser (reused from doctor) ─────────────


def _daemon_log_path() -> Path:
    """Return ``<log_dir>/daemon.log`` (matches the diagnostics helper)."""

    from corpus_forge.logging_config import get_log_dir

    return get_log_dir() / "daemon.log"


def _last_info_line(log_path: Path) -> tuple[str, str] | None:
    """Return ``(timestamp, message)`` of the most-recent INFO line, or ``None``.

    Mirrors :func:`corpus_forge.doctor.checks._check_daemon_activity`
    parse logic so the user sees the same string from both surfaces.
    """

    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        m = _DAEMON_LOG_LINE_RE.match(line)
        if not m or m.group("level") != "INFO":
            continue
        return m.group("ts"), m.group("msg")
    return None


def _relative_age(ts_str: str) -> str:
    """Convert a ``YYYY-MM-DD HH:MM:SS`` timestamp into ``Ns / Nm / Nh / Nd ago``."""

    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts_str
    delta = datetime.now() - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return ts_str
    if secs < _SECS_PER_MIN:
        return f"{secs}s ago"
    if secs < _SECS_PER_HOUR:
        return f"{secs // _SECS_PER_MIN}m ago"
    if secs < _SECS_PER_DAY:
        return f"{secs // _SECS_PER_HOUR}h ago"
    return f"{secs // _SECS_PER_DAY}d ago"


# ── Status renderer ─────────────────────────────────────────────────────


def _active_datasets() -> list[str]:
    """Pull configured dataset names (best-effort, never raises)."""

    try:
        from corpus_forge.config import Config

        cfg = Config.load()
        return [ds.name for ds in cfg.datasets]
    except Exception:
        # Best-effort — config may be missing or invalid.
        return []


def _embed_drain_lanes() -> list[str]:
    """Embedder lanes this host is configured to drain (best-effort, never raises).

    Mirrors :meth:`EmbedDrainLoop.resolve_lanes` *read-only*: every active
    configured embedder intersected with the host's ``[embed] lanes``
    (empty lanes → all active, the backcompat bar). Pure config — no
    embedder warm-up, no backend round-trip — so ``service status`` stays
    DB-free and safe to script. The drain loop's *runtime* state (running?
    last-claim age) lives in the daemon process / ``corpus.embed_claims``
    and is deferred to fleet-5 item 2 (the ``[service]`` config + a
    DB-backed status query) so we don't regress the no-IO contract here.
    """

    try:
        from corpus_forge.config import Config
        from corpus_forge.embed import filter_embedders_by_lanes

        cfg = Config.load()
        active = [ec for ec in getattr(cfg, "embedders", []) if getattr(ec, "active", True)]
        embed_cfg = getattr(cfg, "embed", None)
        lanes_cfg = list(getattr(embed_cfg, "lanes", []) or [])
        kept = set(filter_embedders_by_lanes([ec.name for ec in active], lanes_cfg))
        # Preserve config/declaration order.
        return [ec.name for ec in active if ec.name in kept]
    except Exception:
        # Best-effort — config may be missing or invalid.
        return []


def render_status(console: Console | None = None) -> None:
    """Render the ``service status`` table to ``console`` (or the UI console).

    Pure renderer — never sys.exits; the verb wrapper always returns
    0 because status is intended to be safe to script.  Reuses the
    standard themed singleton so the colored output matches the rest
    of the CLI.
    """

    target = console if console is not None else ui_console

    daemon_pid = _fg.read_pid(DAEMON_COMPONENT)
    embed_worker_pid = _fg.read_pid("embed-worker")
    log_path = _daemon_log_path()

    table = Table(title="corpus-forge service")
    table.add_column("Field", style="muted", no_wrap=True)
    table.add_column("Value")

    # Daemon row.
    if daemon_pid is None:
        table.add_row("daemon", "[warn]not running[/warn]")
    else:
        table.add_row("daemon pid", f"{daemon_pid}")
        uptime = _uptime_seconds(daemon_pid, _fg.pid_path(DAEMON_COMPONENT))
        if uptime is not None:
            table.add_row("uptime", _format_uptime(uptime))
        rss = _rss_bytes(daemon_pid)
        table.add_row("memory (rss)", _format_bytes(rss) if rss is not None else "n/a")

    # Log location is always available.
    table.add_row("log file", str(log_path))

    # Last INFO line.
    last = _last_info_line(log_path)
    if last is not None:
        ts, msg = last
        table.add_row("last activity", f"{_relative_age(ts)} — {msg}")
    elif log_path.exists():
        table.add_row("last activity", "[muted]log present but no INFO lines parsed[/muted]")
    else:
        table.add_row("last activity", "[muted]no daemon.log yet[/muted]")

    # Active datasets.
    datasets = _active_datasets()
    if datasets:
        table.add_row("datasets", ", ".join(datasets))
    else:
        table.add_row("datasets", "[muted](none configured)[/muted]")

    # Embed-drain lanes (read-only, config-derived — see _embed_drain_lanes).
    drain_lanes = _embed_drain_lanes()
    if drain_lanes:
        table.add_row("embed drain lanes", ", ".join(drain_lanes))
    else:
        table.add_row("embed drain lanes", "[muted](no active embedders configured)[/muted]")

    # Background embed-worker.
    if embed_worker_pid is None:
        table.add_row("embed-worker", "[muted]not running[/muted]")
    else:
        table.add_row("embed-worker", f"pid={embed_worker_pid}")

    target.print(table)


# ── Lifecycle primitives (also reused by the deprecated ``daemon`` alias)


def start_daemon_foreground() -> int:
    """Run the daemon in the foreground, in-process.

    Refuses to start if a live pid is already present.  Writes the
    current process pid to ``daemon.pid`` before invoking the daemon
    main loop, and clears the pid on clean exit (best-effort — if the
    process is SIGKILLed, the next ``read_pid`` will report ``None``
    via the liveness check anyway).
    """

    existing = _fg.read_pid(DAEMON_COMPONENT)
    if existing is not None:
        ui_error(f"Already running: pid={existing}. Stop with `corpus-forge service stop`.")
        return 1

    # Record our pid + mode so `service status` / `service restart`
    # can see us.
    _fg.write_pid(DAEMON_COMPONENT, os.getpid())
    _write_mode("foreground")

    try:
        # Lazy import — keeps the module import-time-clean and avoids
        # a circular `cli -> daemon -> cli` loop.
        from corpus_forge.daemon import main as _daemon_main

        try:
            _daemon_main()
        except SystemExit as exc:
            # Daemon main calls sys.exit(0) on clean shutdown.
            code = exc.code if isinstance(exc.code, int) else 0
            return int(code)
        except KeyboardInterrupt:
            ui_info("Stopping daemon (SIGINT received).")
            return 0
        return 0
    finally:
        _fg.clear_pid(DAEMON_COMPONENT)


def start_daemon_background() -> int:
    """Detach the daemon to a background process; write the pid.

    Refuses to start if a live pid is already present.  Records the
    chosen mode so ``service restart`` preserves it.
    """

    existing = _fg.read_pid(DAEMON_COMPONENT)
    if existing is not None:
        ui_error(f"Already running: pid={existing}. Stop with `corpus-forge service stop`.")
        return 1

    # Spawn `python -m corpus_forge daemon` (the deprecated alias) so
    # the child re-enters this codebase via the documented entry point.
    # The pid file is written by `run_attached` once the child is up.
    rc = _fg.run_attached(
        [sys.executable, "-m", "corpus_forge", "daemon"],
        component=DAEMON_COMPONENT,
        background=True,
    )
    if rc == 0:
        _write_mode("background")
    return rc


def stop_daemon() -> int:
    """SIGTERM the daemon, wait up to 30s, then SIGKILL.

    Idempotent: returns 0 with an info message if no pid file exists.
    Clears the pid file on success.
    """

    pid = _fg.read_pid(DAEMON_COMPONENT)
    if pid is None:
        ui_info("Daemon not running")
        # Make sure any stale pid file is gone.
        _fg.clear_pid(DAEMON_COMPONENT)
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Already dead — treat as success.
        _fg.clear_pid(DAEMON_COMPONENT)
        ui_info("Daemon was not running (stale pid cleared)")
        return 0
    except PermissionError:
        ui_error(f"Permission denied sending SIGTERM to pid={pid}")
        return 1

    # Poll for clean exit.  Two terminal conditions count as success:
    #   1. The pid file is gone or names a dead process (``read_pid``
    #      returns ``None``) — the original daemon exited cleanly.
    #   2. The pid file now names a *different* live pid — launchd's
    #      ``KeepAlive=true`` (or systemd ``Restart=always``) respawned
    #      the daemon after the SIGTERM'd one exited.  The pid we
    #      asked to stop IS dead; the new process is launchd's
    #      replacement, not the one we were polling.  Without this
    #      check, the polling loop would re-target the new pid every
    #      iteration and time out at the 30 s grace before SIGKILLing
    #      the (newly-spawned) respawn.
    deadline = time.monotonic() + _STOP_TIMEOUT_SECS
    while time.monotonic() < deadline:
        current = _fg.read_pid(DAEMON_COMPONENT)
        if current is None:
            _fg.clear_pid(DAEMON_COMPONENT)
            ui_ok(f"Stopped daemon (pid={pid})")
            return 0
        if current != pid:
            # The original pid is dead — what's running now is a
            # respawn.  Leave its pid file alone (the respawn owns it)
            # and report success against the pid we were asked to stop.
            ui_ok(f"Stopped daemon (pid={pid}); supervisor respawned new pid={current}")
            return 0
        # `read_pid` returns None when the process has exited; otherwise
        # the pid is still live.  Sleep a bit and re-probe.
        time.sleep(_STOP_POLL_INTERVAL_SECS)

    # SIGKILL escalation (on Windows ``_SIGKILL`` collapses to SIGTERM —
    # ``os.kill`` then issues a ``TerminateProcess`` under the hood, which
    # is the strongest stop signal the platform offers).
    ui_warn(f"Daemon did not exit within {_STOP_TIMEOUT_SECS:.0f}s; sending SIGKILL")
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, _SIGKILL)
    _fg.clear_pid(DAEMON_COMPONENT)
    ui_ok(f"Killed daemon (pid={pid})")
    return 0


# ── Typer verbs ─────────────────────────────────────────────────────────


@service_app.command("status")
def service_status_cmd() -> None:
    """Print the daemon's pid, uptime, memory, datasets, last log line."""

    render_status()


@service_app.command("start")
def service_start_cmd(
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            "-b",
            help="Detach the daemon (write pid file, return immediately).",
        ),
    ] = False,
    foreground: Annotated[
        bool,
        typer.Option(
            "--foreground",
            help="Force foreground mode (the default; explicit for clarity).",
        ),
    ] = False,
) -> None:
    """Start the corpus-forge daemon.

    Foreground is the default; ``--background`` / ``-b`` detaches.  The
    two flags are mutually exclusive — passing both is a usage error.
    """

    if background and foreground:
        ui_error("Pass only one of `--background` / `--foreground`.")
        raise typer.Exit(code=2)

    rc = start_daemon_background() if background else start_daemon_foreground()
    if rc != 0:
        raise typer.Exit(code=rc)


@service_app.command("stop")
def service_stop_cmd() -> None:
    """Stop a running daemon (SIGTERM → 30s wait → SIGKILL)."""

    rc = stop_daemon()
    if rc != 0:
        raise typer.Exit(code=rc)


@service_app.command("restart")
def service_restart_cmd() -> None:
    """Stop and start, preserving the last recorded foreground/background mode."""

    mode = _read_mode()
    rc_stop = stop_daemon()
    if rc_stop != 0:
        raise typer.Exit(code=rc_stop)
    rc_start = start_daemon_foreground() if mode == "foreground" else start_daemon_background()
    if rc_start != 0:
        raise typer.Exit(code=rc_start)


@service_app.command("logs")
def service_logs_cmd(
    n: Annotated[
        int,
        typer.Option("-n", "--lines", help="Trailing lines to print (default 200)."),
    ] = 200,
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Keep streaming new lines."),
    ] = False,
) -> None:
    """Alias for ``corpus-forge logs tail --component daemon``."""

    # Reach into the logs sub-app's underlying function so we get the
    # same themed renderer (rather than spawning a subprocess).
    from corpus_forge.diagnostics.logs import logs_tail_cmd

    logs_tail_cmd(component=DAEMON_COMPONENT, n=n, follow=follow)


# ── install / uninstall ─────────────────────────────────────────────────


def _detect_platform() -> Literal["systemd", "launchd", "schtasks"]:
    """Map ``platform.system()`` to the preferred service flavour.

    Linux → systemd (user unit).
    Darwin → launchd (LaunchAgent).
    Windows → schtasks (scheduled task).
    """

    sysname = platform.system().lower()
    if sysname == "darwin":
        return "launchd"
    if sysname == "windows":
        return "schtasks"
    return "systemd"


def _resolve_kind(
    systemd: bool, launchd: bool, schtasks: bool, auto: bool
) -> Literal["systemd", "launchd", "schtasks"]:
    """Pick the active flavour from the mutually-exclusive flag set."""

    chosen = [
        name
        for name, val in (
            ("systemd", systemd),
            ("launchd", launchd),
            ("schtasks", schtasks),
            ("auto", auto),
        )
        if val
    ]
    # Default: auto.
    if not chosen or chosen == ["auto"]:
        return _detect_platform()
    if len(chosen) > 1:
        raise typer.BadParameter("Pass at most one of --systemd / --launchd / --schtasks / --auto.")
    only = chosen[0]
    if only == "auto":
        return _detect_platform()
    return only  # type: ignore[return-value]


def _apply_systemd(unit_text: str) -> None:
    """Write the systemd user unit and run ``systemctl --user enable --now``."""

    target = _install.SYSTEMD_USER_UNIT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(unit_text, encoding="utf-8")
    ui_ok(f"Wrote {target}")
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
            capture_output=True,
        )
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "corpus-forge.service"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ui_ok("Enabled corpus-forge.service")
        else:
            ui_warn(
                f"systemctl enable returned non-zero (stderr: {result.stderr.strip() or '<empty>'})"
            )
    except FileNotFoundError:
        ui_warn(
            "systemctl not found on PATH — unit written but not enabled. "
            "Run `systemctl --user enable --now corpus-forge.service` manually."
        )


def _apply_launchd(plist_text: str) -> None:
    """Write the launchd plist and run ``launchctl load -w <path>``."""

    target = _install.LAUNCHD_PLIST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plist_text, encoding="utf-8")
    ui_ok(f"Wrote {target}")
    try:
        result = subprocess.run(
            ["launchctl", "load", "-w", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ui_ok("Loaded com.corpus-forge LaunchAgent")
        else:
            ui_warn(
                f"launchctl load returned non-zero (stderr: {result.stderr.strip() or '<empty>'})"
            )
    except FileNotFoundError:
        ui_warn(
            "launchctl not found on PATH — plist written but not loaded. "
            f"Run `launchctl load -w {target}` manually."
        )

    _launchd_tcc_handshake()


def _launchd_tcc_handshake() -> None:
    """Probe iCloud TCC access after a fresh ``launchctl load``.

    The freshly-loaded LaunchAgent inherits TCC from launchd itself,
    not from whichever terminal ran ``service install --apply``. If
    that grant is missing, the agent will keep dying on the first
    iCloud read until the user adds the corpus-forge binary to Full
    Disk Access. Surface that requirement now — while we still have
    the user's attention — instead of letting them discover it later
    by tailing the daemon log.

    On non-macOS hosts (defensive — ``_apply_launchd`` only fires on
    macOS) this is a no-op. When no iCloud-rooted source is
    configured, the handshake skips silently.
    """

    from corpus_forge import macos_tcc

    if not macos_tcc.is_macos():
        return

    try:
        from corpus_forge.config import Config

        cfg_path = Path.home() / ".config" / "corpus-forge" / "config.toml"
        if not cfg_path.exists():
            return
        cfg = Config.load(config_path=cfg_path, secrets_path=cfg_path.parent / "secrets.env")
    except Exception:
        # Config load failure isn't a launchd-install problem; the
        # ``corpus-forge doctor`` command already reports config issues.
        return

    icloud_paths: list[Path] = []
    for dataset in cfg.datasets:
        for source in dataset.sources:
            if getattr(source, "plugin", None) != "filesystem":
                continue
            root = getattr(source, "root", None) or getattr(source, "vault_root", None)
            if root is None:
                continue
            root_path = Path(root).expanduser()
            if macos_tcc.is_iclouddrive_managed(root_path):
                icloud_paths.append(root_path)

    if not icloud_paths:
        return

    result = macos_tcc.request_full_disk_access(icloud_paths)
    if result.granted:
        ui_ok(
            "TCC: Full Disk Access already granted "
            f"for {len(icloud_paths)} iCloud-rooted source(s)."
        )
        return

    ui_warn(
        "The LaunchAgent has been installed, BUT macOS TCC is currently "
        "blocking corpus-forge from reading the configured iCloud Drive "
        "paths. The daemon will keep dying on the first iCloud read "
        "until the access grant lands. Recovery:"
    )
    # ``ui_warn`` only takes one line cleanly; print the multi-line
    # instruction to stderr afterwards so the user sees it.
    print(result.instruction, file=sys.stderr)


def _apply_schtasks(argv: list[str]) -> None:
    """Invoke ``schtasks /create`` to register the Windows task."""

    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            ui_ok(f"Registered scheduled task `{_install.SCHTASKS_TASK_NAME}`")
        else:
            ui_warn(f"schtasks returned non-zero (stderr: {result.stderr.strip() or '<empty>'})")
    except FileNotFoundError:
        ui_warn(
            "schtasks.exe not found on PATH — task not registered. "
            "Run the printed command manually from an Admin shell."
        )


@service_app.command("install")
def service_install_cmd(
    systemd: Annotated[
        bool,
        typer.Option("--systemd", help="Force systemd flavour (Linux user unit)."),
    ] = False,
    launchd: Annotated[
        bool,
        typer.Option("--launchd", help="Force launchd flavour (macOS LaunchAgent)."),
    ] = False,
    schtasks: Annotated[
        bool,
        typer.Option("--schtasks", help="Force Windows Task Scheduler."),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option("--auto", help="Detect platform automatically (default)."),
    ] = False,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Write the unit to disk + enable it (user-scope only).",
        ),
    ] = False,
    system: Annotated[
        bool,
        typer.Option(
            "--system",
            help="System-wide install (refused — use sudo + redirect to /etc/...).",
        ),
    ] = False,
) -> None:
    """Generate (and optionally install) the platform's service unit.

    Without ``--apply``, prints the unit text / argv to stdout so the
    user can review or pipe it elsewhere.  With ``--apply``, writes
    user-scope only (``~/.config/systemd/user/...`` /
    ``~/Library/LaunchAgents/...`` / Windows Task Scheduler).
    """

    if system:
        ui_error(
            "System-wide install requires sudo — generate the unit and "
            "install it yourself: "
            "`corpus-forge service install --auto > /etc/systemd/system/corpus-forge.service`"
        )
        raise typer.Exit(code=2)

    kind = _resolve_kind(systemd, launchd, schtasks, auto)

    if kind == "systemd":
        unit_text = _install.generate_systemd_unit()
        if apply:
            _apply_systemd(unit_text)
        else:
            # Data line — bare print so output is pipe-friendly.
            print(unit_text, end="" if unit_text.endswith("\n") else "\n")
    elif kind == "launchd":
        plist_text = _install.generate_launchd_plist()
        if apply:
            _apply_launchd(plist_text)
        else:
            print(plist_text, end="" if plist_text.endswith("\n") else "\n")
    else:  # schtasks
        argv = _install.generate_schtasks_command()
        if apply:
            _apply_schtasks(argv)
        else:
            print(" ".join(argv))


@service_app.command("uninstall")
def service_uninstall_cmd(
    systemd: Annotated[
        bool,
        typer.Option("--systemd", help="Force systemd flavour."),
    ] = False,
    launchd: Annotated[
        bool,
        typer.Option("--launchd", help="Force launchd flavour."),
    ] = False,
    schtasks: Annotated[
        bool,
        typer.Option("--schtasks", help="Force Windows Task Scheduler."),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option("--auto", help="Detect platform automatically (default)."),
    ] = False,
) -> None:
    """Stop the daemon, disable + remove the unit / plist / scheduled task."""

    # Always best-effort stop first; ignore errors so unbroken cleanup can
    # still complete.
    with contextlib.suppress(Exception):
        stop_daemon()

    kind = _resolve_kind(systemd, launchd, schtasks, auto)

    if kind == "systemd":
        with contextlib.suppress(FileNotFoundError):
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "corpus-forge.service"],
                check=False,
                capture_output=True,
            )
        target = _install.SYSTEMD_USER_UNIT_PATH
        if target.exists():
            target.unlink()
            ui_ok(f"Removed {target}")
        else:
            ui_info(f"No unit at {target}")
    elif kind == "launchd":
        target = _install.LAUNCHD_PLIST_PATH
        with contextlib.suppress(FileNotFoundError):
            subprocess.run(
                ["launchctl", "unload", "-w", str(target)],
                check=False,
                capture_output=True,
            )
        if target.exists():
            target.unlink()
            ui_ok(f"Removed {target}")
        else:
            ui_info(f"No plist at {target}")
    else:  # schtasks
        try:
            result = subprocess.run(
                ["schtasks", "/delete", "/TN", _install.SCHTASKS_TASK_NAME, "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                ui_ok(f"Deleted scheduled task `{_install.SCHTASKS_TASK_NAME}`")
            else:
                ui_info(
                    f"schtasks /delete returned non-zero "
                    f"(stderr: {result.stderr.strip() or '<empty>'})"
                )
        except FileNotFoundError:
            ui_warn("schtasks.exe not found on PATH — nothing to do")


__all__ = [
    "DAEMON_COMPONENT",
    "render_status",
    "service_app",
    "start_daemon_background",
    "start_daemon_foreground",
    "stop_daemon",
]
