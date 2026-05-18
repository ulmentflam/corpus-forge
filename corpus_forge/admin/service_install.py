"""Platform-specific service-unit generators (Phase L Wave 8).

Three flavours of "tell the OS to run corpus-forge as a service":

- :func:`generate_systemd_unit` — Linux user unit (``~/.config/systemd/user/``).
- :func:`generate_launchd_plist` — macOS LaunchAgent (``~/Library/LaunchAgents/``).
- :func:`generate_schtasks_command` — Windows Task Scheduler argv.

Each function returns plain text / a plain argv list so the caller can
print it (when ``--apply`` is *not* set) or write it to disk (when
``--apply`` *is*).  The actual write + enable dance lives in
:mod:`corpus_forge.admin.service` so this module stays unit-testable
without touching the filesystem.

The templates live alongside this module under ``templates/``; we use
simple ``str.format`` token substitution rather than jinja2 to keep the
runtime dep set lean (jinja2 is not a corpus-forge dep today).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_TEMPLATE_DIR: Path = Path(__file__).parent / "templates"

# Canonical install destinations.  All are *user-scope* — system-scope
# install is intentionally not automated (it requires sudo + a different
# unit-file location and a different enable command).
SYSTEMD_USER_UNIT_PATH: Path = Path.home() / ".config" / "systemd" / "user" / "corpus-forge.service"
LAUNCHD_PLIST_PATH: Path = Path.home() / "Library" / "LaunchAgents" / "com.corpus-forge.plist"
SCHTASKS_TASK_NAME: str = "corpus-forge"


# ── Helpers ──────────────────────────────────────────────────────────────


def resolve_exec_path() -> str:
    """Best-effort resolution of an absolute ``corpus-forge`` exec path.

    Falls back to ``<sys.executable> -m corpus_forge`` so the generated
    unit still works in venvs / uv-tool installs where ``corpus-forge``
    might not be on the system PATH a launchd / systemd unit inherits.
    """

    located = shutil.which("corpus-forge")
    if located:
        return located
    return f"{sys.executable} -m corpus_forge"


def _resolve_config_path(config_path: Path | None) -> str:
    """Resolve the ``CF_CONFIG`` value baked into the generated unit.

    When the caller passes ``None`` we point at the documented default
    (``~/.config/corpus-forge/config.toml``) so the unit is portable
    across machines without modification.
    """

    if config_path is None:
        return str(Path.home() / ".config" / "corpus-forge" / "config.toml")
    return str(config_path)


def _read_template(name: str) -> str:
    """Read a template file from the ``templates/`` directory.

    Raises :class:`FileNotFoundError` when the template is missing, which
    surfaces early as a build / packaging bug rather than a silently
    truncated unit file.
    """

    path = _TEMPLATE_DIR / name
    return path.read_text(encoding="utf-8")


# ── Public generators ────────────────────────────────────────────────────


def generate_systemd_unit(config_path: Path | None = None) -> str:
    """Render the systemd ``corpus-forge.service`` unit.

    Returns the ``.service`` file content as text.  Substitutes
    ``{exec_path}`` and ``{config_path}`` into the template.
    """

    template = _read_template("corpus-forge.service.j2")
    return template.format(
        exec_path=resolve_exec_path(),
        config_path=_resolve_config_path(config_path),
    )


def generate_launchd_plist(config_path: Path | None = None) -> str:
    """Render the launchd ``com.corpus-forge.plist`` XML."""

    template = _read_template("com.corpus-forge.plist.j2")
    return template.format(
        exec_path=resolve_exec_path(),
        config_path=_resolve_config_path(config_path),
    )


def generate_schtasks_command(config_path: Path | None = None) -> list[str]:
    """Build the ``schtasks /create`` argv for a Windows scheduled task.

    The task runs ``corpus-forge service start`` at logon and is named
    ``corpus-forge`` so the matching ``service uninstall`` knows what
    to delete.  We don't shell-out here — the argv is returned for the
    caller to invoke via :mod:`subprocess`.

    The ``CF_CONFIG`` value is embedded in the task's ``/TR`` line by
    way of ``cmd /c set CF_CONFIG=... && <exec> service start``.
    Windows scheduled tasks don't natively carry env vars, so the
    ``cmd /c`` wrapper is the documented escape hatch.
    """

    exec_path = resolve_exec_path()
    config_value = _resolve_config_path(config_path)
    tr_value = f'cmd /c "set CF_CONFIG={config_value} && "{exec_path}" service start"'
    return [
        "schtasks",
        "/create",
        "/SC",
        "ONLOGON",
        "/TN",
        SCHTASKS_TASK_NAME,
        "/TR",
        tr_value,
        "/F",
        "/RL",
        "LIMITED",
    ]


__all__ = [
    "LAUNCHD_PLIST_PATH",
    "SCHTASKS_TASK_NAME",
    "SYSTEMD_USER_UNIT_PATH",
    "generate_launchd_plist",
    "generate_schtasks_command",
    "generate_systemd_unit",
    "resolve_exec_path",
]
