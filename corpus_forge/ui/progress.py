"""Single ``rich.progress.Progress`` factory for the CLI.

Bounded ops get a Bar + MofN + time columns; unbounded ops swap the
Bar for a rate column.  When a logger is passed the factory emits
bookending INFO records + sparse milestones every ~10% so the rotating
log captures progress even when the user is not watching the TTY.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .console import console as _default_console


class _MilestoneListener:
    """Watches ``Progress`` tasks and emits ~every-10% INFO records."""

    def __init__(self, logger: logging.Logger, description: str) -> None:
        self._logger = logger
        self._description = description
        # task_id -> last-emitted milestone index (0..10).
        self._emitted: dict[TaskID, int] = {}

    def observe(self, progress: Progress) -> None:
        for task in progress.tasks:
            total = task.total
            if total is None or total <= 0:
                continue
            completed = task.completed
            pct = completed / total
            milestone = min(10, int(pct * 10))
            previous = self._emitted.get(task.id, 0)
            if milestone > previous:
                for step in range(previous + 1, milestone + 1):
                    self._logger.info(
                        f"{self._description} progress: {step * 10}% ({completed:.0f}/{total:.0f})"
                    )
                self._emitted[task.id] = milestone


class _LoggingProgress(Progress):
    """Progress that fires a milestone listener on every task mutation."""

    def __init__(self, *columns, listener: _MilestoneListener | None = None, **kwargs):
        super().__init__(*columns, **kwargs)
        self._listener = listener

    def _observe(self) -> None:
        if self._listener is not None:
            self._listener.observe(self)

    def refresh(self) -> None:  # type: ignore[override]
        super().refresh()
        self._observe()

    def update(self, task_id, **kwargs):  # type: ignore[override]
        super().update(task_id, **kwargs)
        self._observe()

    def advance(self, task_id, advance: float = 1) -> None:  # type: ignore[override]
        super().advance(task_id, advance)
        self._observe()


def _build_columns(*, bounded: bool) -> list:
    if bounded:
        return [
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ]
    # Unbounded: spinner + description + elapsed (no bar, no ETA).
    return [
        SpinnerColumn(),
        TextColumn("[muted]{task.description}[/muted]"),
        TimeElapsedColumn(),
    ]


@contextmanager
def make_progress(
    description: str,
    *,
    total: int | None,
    console: Console | None = None,
    logger: logging.Logger | None = None,
) -> Iterator[Progress]:
    """Return a configured ``Progress`` context manager.

    Parameters
    ----------
    description:
        Short human-facing label (e.g. ``"Embedding chunks"``).  Used in
        log bookends and milestone messages.
    total:
        ``int`` for bounded ops; ``None`` for unbounded.
    console:
        Override the default Rich console (mostly used in tests).
    logger:
        Optional ``logging.Logger``.  When provided the factory emits
        an ``INFO`` ``"<desc> started: <N> items"`` line on entry, a
        completion line on exit (with elapsed + rate), AND a sparse
        ~every-10% milestone for bounded ops.
    """

    bounded = total is not None
    target_console = console if console is not None else _default_console
    columns = _build_columns(bounded=bounded)
    listener = _MilestoneListener(logger, description) if logger is not None else None
    progress = _LoggingProgress(
        *columns,
        console=target_console,
        listener=listener,
        transient=True,
    )
    started = time.perf_counter()
    if logger is not None:
        items = str(total) if total is not None else "unbounded"
        logger.info(f"{description} started: {items} items")
    try:
        with progress:
            yield progress
    finally:
        elapsed = time.perf_counter() - started
        if logger is not None:
            done = 0
            if progress.tasks:
                done = int(sum(t.completed for t in progress.tasks))
            rate = (done / elapsed) if elapsed > 0 else 0.0
            logger.info(
                f"{description} complete: {done} items in {elapsed:.1f}s (rate {rate:.0f}/s)"
            )


if TYPE_CHECKING:  # pragma: no cover
    __all__ = ["make_progress"]
else:
    __all__ = ["make_progress"]
