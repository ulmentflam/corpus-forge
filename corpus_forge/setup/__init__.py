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
    NEXT_STEPS,
    Question,
    load_questions,
    render_config_toml,
    render_next_steps,
    run_non_interactive,
    run_quick,
    run_wizard,
)

__all__ = [
    "NEXT_STEPS",
    "Question",
    "load_questions",
    "render_config_toml",
    "render_next_steps",
    "run_non_interactive",
    "run_quick",
    "run_wizard",
]
