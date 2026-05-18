"""Theme-aware ``rich.prompt`` wrappers.

Wave 1 only re-exports the standard ``Prompt`` / ``Confirm`` bound to
the corpus-forge singleton console with the brand chevron prompt
glyph.  Wave 9 will swap the bodies so they hard-fail under agent
mode.
"""

from __future__ import annotations

from rich.prompt import Confirm as _RichConfirm
from rich.prompt import Prompt as _RichPrompt

from .console import console as _default_console

# U+276F chevron is the brand prompt glyph (see theme.py).
_PROMPT_SUFFIX = "[prompt.glyph]❯[/prompt.glyph] "  # noqa: RUF001


class Prompt(_RichPrompt):
    """``rich.prompt.Prompt`` pre-bound to the corpus-forge console."""

    prompt_suffix = _PROMPT_SUFFIX

    @classmethod
    def ask(cls, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("console", _default_console)
        return super().ask(*args, **kwargs)


class Confirm(_RichConfirm):
    """``rich.prompt.Confirm`` pre-bound to the corpus-forge console."""

    prompt_suffix = _PROMPT_SUFFIX

    @classmethod
    def ask(cls, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("console", _default_console)
        return super().ask(*args, **kwargs)


__all__ = ["Confirm", "Prompt"]
