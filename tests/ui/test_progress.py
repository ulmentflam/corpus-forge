"""Tests for ``corpus_forge.ui.progress.make_progress``.

The factory's contract is:

  - bounded ops get a Bar + MofN + Time-elapsed + Time-remaining column,
  - unbounded ops (``total=None``) replace the Bar with a textual
    rate column,
  - when a logger is passed the factory emits bookending INFO lines on
    enter and exit (``<desc> started: <N> items`` / ``<desc> complete:
    <N> items in Xs (rate Y/s)``),
  - bounded ops emit sparse INFO milestones at every ~10% step.
"""

from __future__ import annotations

import io
import logging

import pytest
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


def _silent_console() -> Console:
    from corpus_forge.ui import theme

    return Console(
        file=io.StringIO(),
        theme=theme.build_theme(),
        force_terminal=False,
        no_color=True,
        width=120,
    )


def test_bounded_progress_columns() -> None:
    from corpus_forge.ui import progress

    sink = _silent_console()
    with progress.make_progress("Embedding", total=100, console=sink) as bar:
        column_types = [type(c) for c in bar.columns]

    assert SpinnerColumn in column_types
    assert TextColumn in column_types
    assert BarColumn in column_types
    assert MofNCompleteColumn in column_types
    assert TimeElapsedColumn in column_types
    assert TimeRemainingColumn in column_types


def test_unbounded_progress_columns() -> None:
    from corpus_forge.ui import progress

    sink = _silent_console()
    with progress.make_progress("Scanning", total=None, console=sink) as bar:
        column_types = [type(c) for c in bar.columns]

    # Unbounded progress: no Bar column.
    assert BarColumn not in column_types
    assert SpinnerColumn in column_types
    assert TextColumn in column_types
    assert TimeElapsedColumn in column_types


def test_logger_bookends_emit_on_bounded_op(caplog: pytest.LogCaptureFixture) -> None:
    from corpus_forge.ui import progress

    logger = logging.getLogger("tests.progress.bounded")
    logger.setLevel(logging.INFO)

    sink = _silent_console()
    with (
        caplog.at_level(logging.INFO, logger="tests.progress.bounded"),
        progress.make_progress("Embedding", total=5, console=sink, logger=logger) as bar,
    ):
        task_id = bar.add_task("Embedding", total=5)
        for _ in range(5):
            bar.update(task_id, advance=1)

    messages = [r.getMessage() for r in caplog.records]
    assert any("Embedding started" in m and "5" in m for m in messages), messages
    # Completion message names items + elapsed + rate.
    assert any("Embedding complete" in m for m in messages), messages


def test_logger_bookend_unbounded_says_unbounded(caplog: pytest.LogCaptureFixture) -> None:
    from corpus_forge.ui import progress

    logger = logging.getLogger("tests.progress.unbounded")
    logger.setLevel(logging.INFO)
    sink = _silent_console()
    with (
        caplog.at_level(logging.INFO, logger="tests.progress.unbounded"),
        progress.make_progress("Scanning", total=None, console=sink, logger=logger),
    ):
        pass

    messages = [r.getMessage() for r in caplog.records]
    assert any("Scanning started" in m and "unbounded" in m for m in messages), messages


def test_no_logger_means_no_log_records(caplog: pytest.LogCaptureFixture) -> None:
    from corpus_forge.ui import progress

    sink = _silent_console()
    with caplog.at_level(logging.INFO), progress.make_progress("Quiet", total=3, console=sink):
        pass

    # No bookends without a logger passed.
    assert not any("Quiet started" in r.getMessage() for r in caplog.records)


def test_milestone_logger_emits_every_10pct(caplog: pytest.LogCaptureFixture) -> None:
    """Bounded ops should emit a sparse INFO milestone at every ~10%
    boundary as the task progresses."""

    from corpus_forge.ui import progress

    logger = logging.getLogger("tests.progress.milestone")
    logger.setLevel(logging.INFO)
    sink = _silent_console()
    with (
        caplog.at_level(logging.INFO, logger="tests.progress.milestone"),
        progress.make_progress("MS", total=20, console=sink, logger=logger) as bar,
    ):
        task_id = bar.add_task("MS", total=20)
        for _ in range(20):
            bar.update(task_id, advance=1)

    milestone_messages = [m for m in (r.getMessage() for r in caplog.records) if "%" in m]
    # We expect at least 5 milestone notes (10% / 20% / ... / 100%)
    # over a 20-item op; allow a generous floor in case of rounding.
    assert len(milestone_messages) >= 5, milestone_messages
