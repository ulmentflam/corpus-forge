"""Phase I — post-install setup wizard.

``corpus-forge setup`` is the Python-side companion to the
``install.sh`` / ``install.ps1`` shell installers. The shells handle
provisioning (uv install, pip extras), then hand off to this module
to render ``~/.config/corpus-forge/config.toml`` and
``~/.config/corpus-forge/secrets.env`` based on the user's answers to
``packaging/install/questions.toml``.

Public entry points:

- :func:`run_wizard` — interactive walk through the question tree.
- :func:`run_non_interactive` — reads answers from ``CF_*`` env vars
  for CI / unattended use.
- :func:`load_questions` — parses ``questions.toml`` and returns the
  raw question list (re-used by tests and by the shell installers'
  smoke-test harness).
"""

from .wizard import (
    JOIN_NEXT_STEPS,
    NEXT_STEPS,
    JoinError,
    Question,
    load_questions,
    render_config_toml,
    render_join_config,
    render_join_next_steps,
    render_next_steps,
    run_join,
    run_non_interactive,
    run_quick,
    run_wizard,
)

__all__ = [
    "JOIN_NEXT_STEPS",
    "NEXT_STEPS",
    "JoinError",
    "Question",
    "load_questions",
    "render_config_toml",
    "render_join_config",
    "render_join_next_steps",
    "render_next_steps",
    "run_join",
    "run_non_interactive",
    "run_quick",
    "run_wizard",
]
