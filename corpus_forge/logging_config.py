"""Logging bootstrap for every corpus-forge entry point.

The single entry point ``init_logging(component, *, verbose=False,
quiet=False)`` installs three handlers on the ``corpus_forge`` root
logger:

1. A ``RotatingFileHandler`` at ``<cache>/corpus-forge/logs/<component>.log``
   (10 MB x 5), level DEBUG, that is the durable diagnostic substrate
   bug-report ships.
2. A ``rich.logging.RichHandler`` on stderr (level INFO by default,
   DEBUG with ``--verbose``, WARNING with ``--quiet``).  Skipped for
   ``component='mcp'`` + ``CF_TRANSPORT='stdio'`` so the MCP wire stays
   pristine.
3. A ``logging.handlers.MemoryHandler`` (capacity 200, target=NullHandler)
   that Wave 6 will flush into ``recent_events.txt``.

Env-var overrides honored at init: ``CF_LOG_LEVEL`` (overrides verbose
/ quiet) and ``CF_LOG_DIR`` (overrides the platformdirs path).

See ``.planning/tdd/phase_l_cli_ux.md`` Section 2 for the design.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from logging import NullHandler
from logging.handlers import MemoryHandler, RotatingFileHandler
from pathlib import Path
from typing import Final

import platformdirs
from rich.console import Console
from rich.logging import RichHandler

from .ui import theme as _theme

ROOT_LOGGER_NAME: Final[str] = "corpus_forge"


_RING_BUFFER: MemoryHandler | None = None
_LOG_DIR: Path | None = None


def _resolve_log_dir() -> Path:
    """Return the directory rotating log files live in.

    Honors ``CF_LOG_DIR`` (used by ephemeral containers + tests) then
    falls back to ``platformdirs.user_cache_dir('corpus-forge') / logs``.
    """

    override = os.environ.get("CF_LOG_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        path = Path(platformdirs.user_cache_dir("corpus-forge")) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_stderr_level(*, verbose: bool, quiet: bool) -> int:
    """Resolve the stderr handler level.

    ``CF_LOG_LEVEL`` (string like ``DEBUG``/``INFO``/...) overrides
    ``verbose`` / ``quiet``.  ``verbose`` and ``quiet`` are mutually
    exclusive in practice but if both are set ``verbose`` wins (matches
    the principle of least-surprise: explicit ``-v`` is the loud
    signal).
    """

    env_level = os.environ.get("CF_LOG_LEVEL")
    if env_level:
        return logging._nameToLevel.get(env_level.upper(), logging.INFO)
    if verbose:
        return logging.DEBUG
    if quiet:
        return logging.WARNING
    return logging.INFO


def _is_mcp_stdio(component: str) -> bool:
    """Return True iff the caller is the MCP stdio adapter.

    The MCP host owns stdout AND silently captures stderr in INFO-mode;
    skip the RichHandler entirely for that one component so the wire
    stays clean.
    """

    return component == "mcp" and os.environ.get("CF_TRANSPORT", "").lower() == "stdio"


def _clear_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()


def init_logging(
    component: str,
    *,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Install the three corpus-forge log handlers.

    Idempotent: re-calling clears the existing handler set first so
    repeat invocations from tests / re-entrant entry points don't stack
    duplicates.
    """

    global _RING_BUFFER, _LOG_DIR  # noqa: PLW0603 — module-singleton handles by design

    log_dir = _resolve_log_dir()
    log_path = log_dir / f"{component}.log"

    root = logging.getLogger(ROOT_LOGGER_NAME)
    _clear_handlers(root)
    root.setLevel(logging.DEBUG)
    # Keep ``propagate = True`` so ``pytest`` caplog (which attaches to
    # the real root logger) sees ``corpus_forge.*`` records.  In normal
    # operation the real root has no handlers attached so propagation
    # is a no-op.
    root.propagate = True

    # (1) Rotating file: always-on, always DEBUG.
    file_formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    # (2) Stderr RichHandler -- unless we're the MCP stdio adapter.
    if not _is_mcp_stdio(component):
        stderr_console = Console(
            theme=_theme.build_theme(),
            stderr=True,
            file=sys.stderr,
            no_color="NO_COLOR" in os.environ,
            highlight=False,
        )
        rich_handler = RichHandler(
            console=stderr_console,
            show_time=False,
            show_path=False,
            markup=True,
            rich_tracebacks=False,
        )
        rich_handler.setLevel(_resolve_stderr_level(verbose=verbose, quiet=quiet))
        root.addHandler(rich_handler)

    # (3) In-memory ring buffer for bug-report (cap 200, target=NullHandler).
    ring = MemoryHandler(capacity=200, target=NullHandler())
    ring.setLevel(logging.INFO)
    root.addHandler(ring)

    _RING_BUFFER = ring
    _LOG_DIR = log_dir


def get_log_dir() -> Path:
    """Return the directory rotating log files are written to."""

    return _LOG_DIR if _LOG_DIR is not None else _resolve_log_dir()


def get_ring_buffer() -> MemoryHandler:
    """Return the in-memory ``MemoryHandler`` populated by the most
    recent ``init_logging`` call (or a fresh one if init hasn't run)."""

    global _RING_BUFFER  # noqa: PLW0603 — module-singleton handle by design
    if _RING_BUFFER is None:
        _RING_BUFFER = MemoryHandler(capacity=200, target=NullHandler())
    return _RING_BUFFER


__all__ = [
    "ROOT_LOGGER_NAME",
    "get_log_dir",
    "get_ring_buffer",
    "init_logging",
]
