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
from corpus_forge.acceleration import detect_accelerator
from corpus_forge.mcp.lifecycle import discover_mcp_servers

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


# ── 2026-05-27 — global managed-ignore drift check ────────────────────


def _check_global_ignore(cfg: Config | None = None) -> CheckResult:
    """Flag a stale managed block in the user-global ignore file.

    The global ignore lives at ``~/.config/corpus-forge/ignore``
    (honoring ``CF_GLOBAL_IGNORE_FILE``). A fresh ``corpus-forge setup``
    bakes the conservative managed template into it — including the
    dev/build junk patterns (``.venv/`` / ``node_modules/`` /
    ``__pycache__/`` …) that otherwise drown the scanner. On EXISTING
    installs whose global block predates a template change, the block
    silently lags behind. This check surfaces that drift.

    Status logic (mirrors :func:`_check_embedder_indexes`):

    - ``SKIP`` when the global file doesn't exist (fresh / no global
      config yet) or has no managed-block sentinels (a hand-rolled file
      we don't own — nothing to compare).
    - ``WARN`` when the managed block is missing any pattern the current
      template would emit, with the exact one-command fix
      (``corpus-forge ignore sync --also-global``). We WARN-not-mutate:
      the audit path never silently rewrites user config — that's the
      ``sync`` verb's job.
    - ``OK`` when the managed block already carries the full template.

    Feature flags come from ``cfg`` when supplied; otherwise the
    conservative all-off preset is used (the same preset the wizard /
    ``ignore sync`` write the global file with), so the junk patterns —
    which are unconditional — are always part of the expected set.
    """
    from corpus_forge.ignore_defaults import (  # noqa: PLC0415
        default_managed_lines,
        feature_flags_from_config,
        parse_managed_lines,
    )
    from corpus_forge.ignore_lifecycle import _resolve_global_path  # noqa: PLC0415

    global_path = _resolve_global_path()
    if not global_path.exists():
        return CheckResult(
            "global_ignore",
            CheckStatus.SKIP,
            f"{global_path} not present (no global ignore configured)",
        )

    try:
        text = global_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CheckResult(
            "global_ignore",
            CheckStatus.SKIP,
            f"unable to read {global_path}: {exc}",
        )

    body = parse_managed_lines(text)
    if body is None:
        # No sentinels — a hand-rolled global file we don't manage.
        return CheckResult(
            "global_ignore",
            CheckStatus.SKIP,
            f"{global_path}: no corpus-forge managed block (not managed by us)",
        )

    if cfg is not None:
        features = feature_flags_from_config(cfg)
    else:
        features = {
            "whisper": False,
            "image_extractor": False,
            "code_enricher": False,
            "vlm": False,
        }
    expected = set(default_managed_lines(features))
    present = {line for line in body if not line.startswith("#")}
    missing = expected - present

    if missing:
        max_sample = 6
        sample = ", ".join(sorted(missing)[:max_sample])
        more = "" if len(missing) <= max_sample else f" (+{len(missing) - max_sample} more)"
        return CheckResult(
            "global_ignore",
            CheckStatus.WARN,
            (
                f"{global_path}: managed block is stale — missing "
                f"{len(missing)} pattern(s): {sample}{more}. "
                f"Run `corpus-forge ignore sync --also-global` to refresh it."
            ),
        )

    return CheckResult(
        "global_ignore",
        CheckStatus.OK,
        f"{global_path}: managed block matches the current template",
    )


# ── Phase M Wave 4 — Zotero check ─────────────────────────────────────


def _check_zotero(cfg: Config) -> CheckResult:
    """Validate every Zotero source's reachability per its mode.

    Status logic:

    - ``SKIP`` when no source has ``plugin == "zotero"``.
    - For each Zotero source:
        - local mode  → ``OK`` when ``library_path`` opens read-only,
          ``FAIL`` when missing.
        - web mode    → ``OK`` when ``api_key_env`` is set, ``WARN`` when
          unset.
        - both mode   → degrades gracefully: ``WARN`` if local path
          missing but web is configured; ``FAIL`` only when BOTH fail.
    - Worst-status wins across multiple sources.
    """
    import os as _os  # noqa: PLC0415
    import sqlite3 as _sqlite3  # noqa: PLC0415

    zotero_sources = []
    for ds in cfg.datasets:
        for src in ds.sources:
            if getattr(src, "plugin", None) == "zotero":
                z = getattr(src, "zotero", None)
                if z is not None:
                    zotero_sources.append(z)

    if not zotero_sources:
        return CheckResult(
            "zotero",
            CheckStatus.SKIP,
            "no Zotero source configured",
        )

    worst = CheckStatus.OK
    details: list[str] = []
    for z in zotero_sources:
        mode = getattr(z, "mode", "local")
        # local-side probe.
        #
        # ``library_path`` is OPTIONAL for local mode — when unset, the
        # connector resolves a platform default via
        # ``ZoteroLocalReader.default_library_path()``. Treat the unset
        # case as a non-fatal informational note (caller may have
        # configured a non-standard install but not all errors propagate
        # back here). Only flag FAIL when the user explicitly named a
        # path that doesn't exist or doesn't open.
        local_ok = False
        local_explicit_fail = False
        local_msg = ""
        library_path = getattr(z, "library_path", None)
        if mode in ("local", "both"):
            if not library_path:
                local_msg = f"mode={mode}: library_path not set (will resolve a platform default)"
                local_ok = True
            elif not Path(library_path).exists():
                local_msg = f"mode={mode}: library_path missing ({library_path})"
                local_explicit_fail = True
            else:
                try:
                    conn = _sqlite3.connect(f"file:{library_path}?mode=ro&immutable=1", uri=True)
                    conn.close()
                    local_ok = True
                except Exception as exc:
                    local_msg = f"mode={mode}: open failed ({exc})"
                    local_explicit_fail = True

        # web-side probe (env var presence only — no live HTTP)
        web_ok = False
        web_msg = ""
        if mode in ("web", "both"):
            api_key_env = getattr(z, "api_key_env", "ZOTERO_API_KEY")
            user_id = getattr(z, "user_id", None)
            key = _os.environ.get(api_key_env)
            if not key:
                web_msg = f"mode={mode}: {api_key_env} env var unset"
            elif not user_id:
                web_msg = f"mode={mode}: user_id not set"
            else:
                web_ok = True

        if mode == "local":
            if local_ok and not local_msg:
                details.append("local OK")
            elif local_ok:
                # Path-unset informational note: OK status, informative
                # detail string. No status downgrade.
                details.append(local_msg)
            else:
                details.append(local_msg or "local FAIL")
                if local_explicit_fail:
                    worst = CheckStatus.FAIL
                elif worst != CheckStatus.FAIL:
                    worst = CheckStatus.WARN
        elif mode == "web":
            if web_ok:
                details.append("web OK")
            else:
                details.append(web_msg or "web WARN")
                if worst != CheckStatus.FAIL:
                    worst = CheckStatus.WARN
        elif mode == "both":
            if local_ok and web_ok:
                details.append("both OK")
            elif not local_ok and not web_ok:
                details.append(f"both FAIL ({local_msg}; {web_msg})")
                worst = CheckStatus.FAIL
            else:
                details.append(f"both degraded ({local_msg or '-'}; {web_msg or '-'})")
                if worst != CheckStatus.FAIL:
                    worst = CheckStatus.WARN

    return CheckResult("zotero", worst, "; ".join(details))


def _check_embedder_drift(cfg: Config) -> CheckResult:
    """Detect ``corpus.embedders`` rows whose name isn't in the active config.

    Renaming an embedder in ``config.toml`` (or deleting one) leaves
    the original ``corpus.embedders`` row + per-embedder
    ``embeddings_<name>`` table behind. ``corpus-forge embedder list``
    reads config state, not DB state, so these orphans are silently
    invisible until something like ``embedder list --verify`` or this
    check catches them. The maintainer's instance hit this on
    2026-05-22: ``qwen3-2000`` → ``qwen3-4096`` rename left a 209 MB
    orphan table on top of a 342 MB real-data DB.

    Status logic:

    - ``SKIP`` for SQLite backends (drift is rare in single-host
      sqlite installs; the audit helper short-circuits for them).
    - ``SKIP`` when the backend isn't reachable (we don't want
      ``doctor`` to wedge on a temporarily-down Postgres).
    - ``SKIP`` when ``corpus.embedders`` doesn't exist yet
      (pre-migrate state).
    - ``OK`` when every DB-side embedder name matches one in config.
    - ``WARN`` when at least one DB row is orphaned, with the
      ``corpus-forge embedder gc --apply`` recovery command and a
      reclaimable-bytes total.

    ``WARN``-not-``FAIL`` because the orphan doesn't break the active
    ingest — it just bloats the DB and confuses readers of the
    ``embedders`` catalog.
    """
    if cfg.backend.kind == "sqlite":
        return CheckResult(
            "embedder_drift",
            CheckStatus.SKIP,
            "sqlite backend (drift uncommon; not audited)",
        )

    try:
        from corpus_forge.admin.embedder import audit_embedder_drift  # noqa: PLC0415
        from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415
    except ImportError as exc:
        return CheckResult(
            "embedder_drift",
            CheckStatus.SKIP,
            f"postgres backend unavailable: {exc}",
        )

    try:
        backend = PostgresBackend(dsn=cfg.backend.dsn, schema=cfg.backend.schema)
    except Exception as exc:
        return CheckResult(
            "embedder_drift",
            CheckStatus.SKIP,
            f"backend unreachable: {exc}",
        )

    try:
        orphans = audit_embedder_drift(backend, cfg)
    except Exception as exc:
        return CheckResult(
            "embedder_drift",
            CheckStatus.SKIP,
            f"audit failed (likely pre-migrate state): {exc}",
        )

    if not orphans:
        return CheckResult(
            "embedder_drift",
            CheckStatus.OK,
            "every corpus.embedders row matches an active config embedder",
        )

    total_bytes = sum(o.table_size_bytes or 0 for o in orphans)
    total_rows = sum(o.row_count or 0 for o in orphans)
    names = ", ".join(o.name for o in orphans)
    detail = (
        f"{len(orphans)} orphan embedder row(s) — names {{{names}}}; "
        f"~{total_bytes // (1024 * 1024)} MB / {total_rows} rows reclaimable. "
        f"Run `corpus-forge embedder gc --apply` to drop the stale "
        f"tables and catalog rows."
    )
    return CheckResult("embedder_drift", CheckStatus.WARN, detail)


def _check_icloud_access(cfg: Config) -> CheckResult:
    """Probe TCC access for each configured iCloud-rooted source.

    macOS gates ``~/Library/Mobile Documents/...`` behind the TCC
    permission system. corpus-forge's filesystem source can configure
    iCloud Drive paths (CloudDocs, Obsidian, etc.); when the running
    process's terminal hasn't been granted Full Disk Access (or the
    granular Files and Folders → iCloud Drive toggle), every read
    fails with ``[Errno 1] Operation not permitted`` — and the
    ingest crashes on the first file.

    Status logic:

    - ``SKIP`` on non-macOS hosts (no TCC layer to probe).
    - ``SKIP`` when no filesystem source resolves to an
      iCloud-managed path.
    - ``OK`` when every iCloud root probes ``GRANTED`` (or
      ``MISSING``, which is a separate problem the corpusignore /
      source checks surface).
    - ``WARN`` when at least one probe returns ``DENIED``. Not
      ``FAIL`` because corpus-forge ships with a graceful eviction
      handler (PR #19) — an unreachable iCloud root degrades the
      run, but doesn't have to abort it.
    """

    from corpus_forge import macos_tcc  # noqa: PLC0415 — keeps cold start fast

    if not macos_tcc.is_macos():
        return CheckResult(
            "icloud_access",
            CheckStatus.SKIP,
            "not a macOS host; TCC does not apply",
        )

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
        return CheckResult(
            "icloud_access",
            CheckStatus.SKIP,
            "no iCloud-rooted filesystem sources configured",
        )

    denials: list[str] = []
    for path in icloud_paths:
        probe = macos_tcc.probe_tcc_access(path)
        if probe.denied:
            denials.append(str(path))

    if not denials:
        return CheckResult(
            "icloud_access",
            CheckStatus.OK,
            f"TCC grants access to {len(icloud_paths)} iCloud-rooted source(s)",
        )

    detail = (
        f"{len(denials)} of {len(icloud_paths)} iCloud root(s) blocked by macOS TCC. "
        "Run `corpus-forge setup` to open the Privacy pane, or grant "
        "Full Disk Access to your terminal app manually in "
        "System Settings → Privacy & Security."
    )
    return CheckResult("icloud_access", CheckStatus.WARN, detail)


# ── orchestrator ──────────────────────────────────────────────────────────────────


def _check_embedder_acceleration() -> CheckResult:
    """Surface the detected accelerator + recommended embedder lane.

    Informational only — CPU is not a failure, just a slower lane.
    The detail line carries the device blurb plus the recommended
    ``model_id`` so operators can spot a config that's leaving a
    freshly-attached GPU on the table.  See
    :mod:`corpus_forge.acceleration` for the detection +
    recommendation logic.

    Wrapped in a broad ``except Exception`` so any future change to
    ``detect_accelerator`` / ``recommend_embedder_preset`` (or a
    runtime quirk like a wedged ``nvidia-smi`` that escapes the
    internal timeout) can never crash ``corpus-forge doctor``.  The
    fallback result still uses ``CheckStatus.OK`` so the overall
    report stays healthy — the operator just sees that detection
    was unavailable.
    """

    from corpus_forge.acceleration import (  # noqa: PLC0415
        recommend_embedder_preset,
    )

    try:
        info = detect_accelerator()
        preset = recommend_embedder_preset(info)
    except Exception as exc:
        return CheckResult(
            "embedder_acceleration",
            CheckStatus.OK,
            f"detection unavailable: {exc}",
        )
    return CheckResult("embedder_acceleration", CheckStatus.OK, preset.summary)


def _check_mcp_servers() -> CheckResult:
    """Surface running ``corpus-forge mcp serve`` children + flag stale ones.

    Two failure modes the operator hits most often:

    1. After a ``uv tool install --force`` of a fixed wheel, the
       client-spawned MCP server is still running on the OLD binding
       (writes-disabled, NaN bug, …) because the MCP client never
       restarted its child.  Doctor can't tell from outside which
       wheel the child loaded, so it does the conservative thing —
       reports the live pids, recommends ``corpus-forge mcp restart``
       at OK level so the operator can act if they JUST upgraded.

    2. ``--no-writes`` left in argv from a debug session.  WARN
       because that's the most common "why isn't this commit_curation
       call landing" mystery; recommends ``corpus-forge mcp restart``
       (with the default re-enabling writes).

    Wrapped in a broad except so a flaky ``ps`` invocation can never
    crash doctor.
    """
    try:
        servers = list(discover_mcp_servers())
    except Exception as exc:  # discovery is best-effort
        return CheckResult(
            "mcp_servers",
            CheckStatus.OK,
            f"detection unavailable: {exc}",
        )
    if not servers:
        return CheckResult(
            "mcp_servers",
            CheckStatus.OK,
            "no corpus-forge mcp serve processes detected",
        )
    no_writes_pids = [s.pid for s in servers if s.writes_disabled]
    if no_writes_pids:
        return CheckResult(
            "mcp_servers",
            CheckStatus.WARN,
            f"{len(no_writes_pids)} server(s) running with --no-writes "
            f"(pids={no_writes_pids}); writes disabled — run "
            "`corpus-forge mcp restart` to relaunch under the default "
            "(writes enabled).",
        )
    pids = [s.pid for s in servers]
    return CheckResult(
        "mcp_servers",
        CheckStatus.OK,
        f"{len(servers)} server(s) running (pids={pids}); "
        "run `corpus-forge mcp restart` if you just upgraded the wheel.",
    )


_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _check_python_version,
    _check_uv,
    _check_poppler,
    _check_ffmpeg,
    _check_daemon_activity,
    _check_embedder_acceleration,
    _check_mcp_servers,
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


def _check_embedder_indexes(cfg: Config) -> CheckResult:
    """Detect per-embedder HNSW index drift against the configured dim.

    Status logic:

    - ``SKIP`` for SQLite backends (sqlite-vec doesn't use HNSW).
    - ``SKIP`` when the backend isn't reachable — we don't want
      ``doctor`` to wedge on a temporarily-down Postgres.
    - ``SKIP`` when no per-embedder tables exist yet (fresh install,
      pre-ingest state).
    - ``WARN`` when any embedder's index strategy doesn't match its
      configured dimension. The user-actionable fix is
      ``corpus-forge embedder repair-indexes --apply`` or rerunning
      ``corpus-forge migrate`` (revision 0015 does the same rebuild).
    - ``OK`` when every per-embedder table's HNSW index matches the
      strategy ``_dense_index_strategy`` would produce for its
      embedder's dim.
    """
    if cfg.backend.kind == "sqlite":
        return CheckResult(
            "embedder_indexes",
            CheckStatus.SKIP,
            "sqlite backend (no HNSW indexes)",
        )

    # Lazy imports keep doctor's import-time cheap when the postgres
    # extras aren't installed.
    try:
        from corpus_forge.admin.embedder import audit_embedder_indexes  # noqa: PLC0415
        from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415
    except ImportError as exc:
        return CheckResult(
            "embedder_indexes",
            CheckStatus.SKIP,
            f"postgres backend unavailable: {exc}",
        )

    backend = None
    try:
        backend = PostgresBackend(dsn=cfg.backend.dsn, schema=cfg.backend.schema)
    except Exception as exc:
        return CheckResult(
            "embedder_indexes",
            CheckStatus.SKIP,
            f"backend unreachable: {exc}",
        )

    try:
        rows = audit_embedder_indexes(backend)
    except Exception as exc:
        return CheckResult(
            "embedder_indexes",
            CheckStatus.SKIP,
            f"audit failed (likely pre-migrate state): {exc}",
        )

    if not rows:
        return CheckResult(
            "embedder_indexes",
            CheckStatus.SKIP,
            "no embedders registered yet",
        )

    # ``TABLE_MISSING`` is also a non-healthy state — the embedder row
    # exists but the per-embedder chunks table doesn't, so dense
    # search against that embedder will fail. The doctor check has to
    # surface that just like DRIFT / MISSING so the operator runs
    # ``ingest`` (or ``embed``) to re-create it. Anything that isn't
    # OK gets reported.
    drifted = [r for r in rows if r.status != "OK"]
    if not drifted:
        names = ", ".join(f"{r.name}({r.dimension}d)" for r in rows)
        return CheckResult(
            "embedder_indexes",
            CheckStatus.OK,
            f"all HNSW indexes match configured dim: {names}",
        )

    detail = "; ".join(f"{r.name}({r.dimension}d) = {r.status}" for r in drifted)
    return CheckResult(
        "embedder_indexes",
        CheckStatus.WARN,
        (
            f"{len(drifted)} embedder(s) need an index rebuild — "
            f"run `corpus-forge migrate` (or `corpus-forge embedder "
            f"repair-indexes --apply` for a targeted fix). Drifted: {detail}"
        ),
    )


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
        results.append(
            CheckResult(
                "embedder_indexes",
                CheckStatus.SKIP,
                "skipped (config not loaded)",
            )
        )
        results.append(
            CheckResult(
                "embedder_drift",
                CheckStatus.SKIP,
                "skipped (config not loaded)",
            )
        )
        results.append(
            CheckResult(
                "icloud_access",
                CheckStatus.SKIP,
                "skipped (config not loaded)",
            )
        )
    else:
        results.append(_check_corpusignore(loaded_cfg))
        results.append(_check_zotero(loaded_cfg))
        results.append(_check_embedder_indexes(loaded_cfg))
        results.append(_check_embedder_drift(loaded_cfg))
        results.append(_check_icloud_access(loaded_cfg))
    # The global ignore drift check is independent of whether the config
    # loaded — the global file lives at ~/.config/corpus-forge/ignore
    # regardless. Pass the (possibly None) config for feature derivation;
    # the check falls back to the conservative all-off preset.
    results.append(_check_global_ignore(loaded_cfg))
    return DoctorReport(results=results)
