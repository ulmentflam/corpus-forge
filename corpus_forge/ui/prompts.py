"""Theme-aware ``rich.prompt`` wrappers.

Wraps ``rich.prompt.Prompt`` / ``Confirm`` so they pick up the corpus-
forge brand chevron and the shared console.  Under agent mode (Wave 9)
both classes hard-fail with :class:`RequiresInteractiveError` — agents
must supply values via flags / env vars instead of expecting a TTY.
"""

from __future__ import annotations

from typing import Any

from rich.prompt import Confirm as _RichConfirm
from rich.prompt import Prompt as _RichPrompt

from .agent import RequiresInteractiveError
from .console import _agent_mode_active
from .console import console as _default_console

# U+276F chevron is the brand prompt glyph (see theme.py).
_PROMPT_SUFFIX = "[prompt.glyph]❯[/prompt.glyph] "  # noqa: RUF001


def _raise_if_agent(prompt_text: Any) -> None:
    """Block interactive prompts under agent mode.

    The CLI global error handler catches the raised exception and emits
    a structured ``error`` event with exit code 2.
    """

    if _agent_mode_active():
        raise RequiresInteractiveError(prompt=str(prompt_text))


class Prompt(_RichPrompt):
    """``rich.prompt.Prompt`` pre-bound to the corpus-forge console."""

    prompt_suffix = _PROMPT_SUFFIX

    @classmethod
    def ask(cls, *args: Any, **kwargs: Any):  # type: ignore[override]
        _raise_if_agent(args[0] if args else kwargs.get("prompt", ""))
        kwargs.setdefault("console", _default_console)
        return super().ask(*args, **kwargs)


class Confirm(_RichConfirm):
    """``rich.prompt.Confirm`` pre-bound to the corpus-forge console."""

    prompt_suffix = _PROMPT_SUFFIX

    @classmethod
    def ask(cls, *args: Any, **kwargs: Any):  # type: ignore[override]
        _raise_if_agent(args[0] if args else kwargs.get("prompt", ""))
        kwargs.setdefault("console", _default_console)
        return super().ask(*args, **kwargs)


__all__ = ["Confirm", "Prompt", "RequiresInteractiveError"]
