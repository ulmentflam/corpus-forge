"""Static check that ``install.sh`` hands off to ``corpus-forge setup``
with ``--non-interactive``.

Background — the install.sh post-install handoff used to wrap the
``corpus-forge setup`` call in a conditional that only added
``--non-interactive`` when ``CF_NON_INTERACTIVE=1`` was set by the
caller. In every other case (the common ``curl | sh`` invocation
from a TTY) it called plain ``corpus-forge setup`` without the flag.

That was broken: the Python wizard's stdin was already consumed by
install.sh's interactive prompts (or never a TTY when piped via
``curl | sh``), so the wizard's re-prompts received empty input and
silently took defaults — writing a config that ignored every answer
the user had just typed.

The fix is to ALWAYS pass ``--non-interactive`` to setup. install.sh
exports every collected answer as a ``CF_*`` env var first; setup's
non-interactive path reads from there.

A black-box invocation test would need a fully wired install
environment plus a fake corpus-forge binary on PATH; instead this is a
static check on the script text to catch accidental reverts.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"


def test_install_sh_calls_setup_with_non_interactive_flag() -> None:
    """``install.sh`` must invoke ``corpus-forge setup --non-interactive``
    after the install step."""
    script_text = INSTALL_SH.read_text(encoding="utf-8")
    assert "corpus-forge setup --non-interactive" in script_text, (
        "install.sh handoff to corpus-forge setup must pass --non-interactive "
        "so the wizard reads from the CF_* env vars install.sh exported "
        "rather than re-prompting on an already-consumed stdin."
    )


def test_install_sh_does_not_call_plain_setup_unconditionally() -> None:
    """The conditional fallback that called ``corpus-forge setup`` (without
    ``--non-interactive``) must be gone.

    Earlier revisions had::

        if [ "${CF_NON_INTERACTIVE:-0}" = "1" ]; then
            corpus-forge setup --non-interactive
        else
            corpus-forge setup
        fi

    The plain ``corpus-forge setup`` line discarded user answers. Pin
    that we no longer ship it.
    """
    script_text = INSTALL_SH.read_text(encoding="utf-8")
    bad_pattern = "\n        corpus-forge setup\n"  # specific to the old fallback
    assert bad_pattern not in script_text, (
        "install.sh still contains the bare `corpus-forge setup` fallback "
        "that discards every CF_* env var the script just set. Replace it "
        "with `corpus-forge setup --non-interactive`."
    )
