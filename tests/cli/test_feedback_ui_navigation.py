"""Phase Q Wave 3 — ``corpus-forge feedback`` CLI subgroup navigation tests.

RED suite for Q3-T1 (navigation / smoke surface).

Every test in this file MUST fail until the Coder ships
``corpus_forge/cli_feedback.py`` and wires it into ``corpus_forge/cli.py``.

Contracts tested:
- ``corpus-forge feedback --help`` lists exactly four subcommands.
- ``corpus-forge feedback start --help`` exits 0 and exposes the expected flags.
- ``corpus-forge feedback start --dataset demo --no-tui --action quit`` exits 0.
- Traversal: ``--action skip --action next --action quit`` traverses chunks then quits.

IO contract (Phase L Wave 2): all user-visible output uses ``print()`` for data
lines.  Error messages go to ``sys.stderr``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from corpus_forge.cli import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FEEDBACK_SUBCOMMANDS = ["start", "resume", "list-sessions", "export-session"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner() -> CliRunner:
    return CliRunner()


def _env(feedback_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env dict with CORPUS_FORGE_FEEDBACK_DIR pointed at a tmp dir."""
    e = {**os.environ, "CORPUS_FORGE_FEEDBACK_DIR": str(feedback_dir)}
    if extra:
        e.update(extra)
    return e


def _make_fake_config(db_path: Path | None = None) -> MagicMock:
    """Return a MagicMock that looks enough like ``corpus_forge.config.Config``."""
    backend_cfg = MagicMock()
    backend_cfg.kind = "sqlite"
    backend_cfg.dsn = str(db_path) if db_path else ":memory:"
    backend_cfg.schema = "corpus"

    cfg = MagicMock()
    cfg.backend = backend_cfg
    cfg.datasets = []
    return cfg


# ---------------------------------------------------------------------------
# T1 — ``feedback --help`` lists all four subcommands
# ---------------------------------------------------------------------------


def test_feedback_help_lists_four_subcommands() -> None:
    """``corpus-forge feedback --help`` must list all four subcommands."""
    result = _runner().invoke(app, ["feedback", "--help"])
    combined = (result.output or "") + (result.stderr or "")

    assert result.exit_code == 0, f"feedback --help exited {result.exit_code}.\noutput={combined!r}"

    for sub in _FEEDBACK_SUBCOMMANDS:
        assert sub in combined, (
            f"subcommand '{sub}' not found in feedback --help output.\n"
            f"exit_code={result.exit_code}\noutput={combined!r}"
        )


# ---------------------------------------------------------------------------
# T2 — ``feedback start --help`` exits 0 with expected flags
# ---------------------------------------------------------------------------


def test_feedback_start_help_exits_zero() -> None:
    """``corpus-forge feedback start --help`` exits 0."""
    result = _runner().invoke(app, ["feedback", "start", "--help"])
    assert result.exit_code == 0, (
        f"feedback start --help exited {result.exit_code}.\noutput={result.output!r}"
    )


def test_feedback_start_help_shows_dataset_flag() -> None:
    """``feedback start --help`` output mentions ``--dataset``."""
    result = _runner().invoke(app, ["feedback", "start", "--help"])
    combined = (result.output or "") + (result.stderr or "")
    assert "--dataset" in combined, (
        f"--dataset not found in feedback start --help.\noutput={combined!r}"
    )


def test_feedback_start_help_shows_no_tui_flag() -> None:
    """``feedback start --help`` output mentions ``--no-tui``."""
    result = _runner().invoke(app, ["feedback", "start", "--help"])
    combined = (result.output or "") + (result.stderr or "")
    assert "--no-tui" in combined, (
        f"--no-tui not found in feedback start --help.\noutput={combined!r}"
    )


def test_feedback_start_help_shows_action_flag() -> None:
    """``feedback start --help`` output mentions ``--action``."""
    result = _runner().invoke(app, ["feedback", "start", "--help"])
    combined = (result.output or "") + (result.stderr or "")
    assert "--action" in combined, (
        f"--action not found in feedback start --help.\noutput={combined!r}"
    )


# ---------------------------------------------------------------------------
# T3 — ``feedback start --no-tui --action quit`` exits 0 cleanly
# ---------------------------------------------------------------------------


def test_feedback_start_no_tui_quit_exits_zero(tmp_path: Path) -> None:
    """``feedback start --dataset demo --no-tui --action quit`` exits 0."""
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config()

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        result = _runner().invoke(
            app,
            ["feedback", "start", "--dataset", "demo", "--no-tui", "--action", "quit"],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"feedback start --no-tui --action quit exited {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# T4 — Traversal: skip + next + quit processes chunks then quits
# ---------------------------------------------------------------------------


def test_feedback_start_no_tui_skip_next_quit_exits_zero(tmp_path: Path) -> None:
    """``feedback start --no-tui --action skip --action next --action quit`` exits 0."""
    feedback_dir = tmp_path / "feedback"
    cfg = _make_fake_config()

    with patch("corpus_forge.config.Config.load", return_value=cfg):
        result = _runner().invoke(
            app,
            [
                "feedback",
                "start",
                "--dataset",
                "demo",
                "--no-tui",
                "--action",
                "skip",
                "--action",
                "next",
                "--action",
                "quit",
            ],
            env=_env(feedback_dir),
        )

    assert result.exit_code == 0, (
        f"feedback start traversal exited {result.exit_code}.\n"
        f"output={result.output!r}\nstderr={result.stderr!r}"
    )


def test_feedback_registered_on_root_app() -> None:
    """``corpus-forge --help`` must mention the ``feedback`` subgroup."""
    result = _runner().invoke(app, ["--help"])
    combined = (result.output or "") + (result.stderr or "")
    assert "feedback" in combined, f"'feedback' not found in root --help.\noutput={combined!r}"
