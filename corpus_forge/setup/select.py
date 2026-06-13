"""Arrow-key / fuzzy selection helpers for ``corpus-forge setup``.

These wrap `questionary <https://github.com/tmbo/questionary>`_ to give
the interactive wizard rich, arrow-key-driven prompts (single choice,
multi-select, free text, yes/no) **without** breaking the existing
stream-injected, non-interactive, CI, and test paths.

Design contract — three things make these helpers safe to drop into the
wizard:

1. **Lazy import.** ``questionary`` (and its heavy ``prompt_toolkit``
   dependency) is imported *inside* each function, never at module top.
   The module therefore imports cleanly even when ``questionary`` is
   absent, so importing :mod:`corpus_forge.setup.select` is always free.

2. **Graceful fallback.** Each helper falls back to the same
   line-based prompt logic the wizard already uses
   (:func:`corpus_forge.setup.wizard._read_answer_interactive`) when
   **any** of the following hold:

   - there is no interactive TTY (``stdin``/``stdout`` not a terminal),
   - an explicit ``stream_in``/``stream_out`` seam is supplied (the
     wizard's test/non-interactive injection point), or
   - the ``questionary`` import fails (extra not installed).

   This means the helpers behave *identically* to today's typed-prompt
   wizard under ``--non-interactive`` / CI / tests, and only light up
   the rich UI when a real human is at a real terminal.

3. **One choice shape.** ``choices`` is a list of either plain option
   strings (``["postgres", "sqlite"]``) or ``(label, value)`` pairs
   (``[("PostgreSQL (recommended)", "postgres"), ...]``). The label is
   what the user sees; the value is what the helper returns. Plain
   strings use the string as both label and value.

The fallback path reuses the wizard's :class:`~corpus_forge.setup.wizard.Question`
dataclass + :func:`~corpus_forge.setup.wizard._read_answer_interactive`
so there is exactly one place that owns the typed-prompt protocol.
"""

from __future__ import annotations

import sys
from typing import IO

# Type alias for the accepted choice shape: a bare option string or a
# ``(label, value)`` pair. Documented in the module docstring.
Choice = "str | tuple[str, str]"


def _split_choice(choice: str | tuple[str, str]) -> tuple[str, str]:
    """Return ``(label, value)`` for one entry of a ``choices`` list.

    A bare string maps to ``(s, s)``; a pair passes through unchanged.
    """
    if isinstance(choice, tuple):
        label, value = choice
        return str(label), str(value)
    return str(choice), str(choice)


def _normalise_choices(
    choices: list[str | tuple[str, str]],
) -> tuple[list[str], list[str], dict[str, str]]:
    """Split ``choices`` into parallel label/value lists + a label→value map."""
    labels: list[str] = []
    values: list[str] = []
    by_label: dict[str, str] = {}
    for choice in choices:
        label, value = _split_choice(choice)
        labels.append(label)
        values.append(value)
        by_label[label] = value
    return labels, values, by_label


def _use_fallback(stream_in: IO[str] | None, stream_out: IO[str] | None) -> bool:
    """Decide whether to skip the rich UI and use the typed-prompt path.

    Mirrors the wizard's interactivity model: an explicit stream seam
    (tests / non-interactive injection) always forces the fallback, as
    does the absence of a real TTY on stdin/stdout. The wizard itself
    gates the rich path on ``interactive=True``; this is the
    helper-local equivalent so the helpers stay safe even when called
    without the wizard's surrounding context.
    """
    if stream_in is not None or stream_out is not None:
        return True
    stdin_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return not (stdin_tty and stdout_tty)


def _fallback_prompt(
    prompt: str,
    *,
    qtype: str,
    default: str,
    choices: list[str] | None = None,
    warn: str = "",
    stream_in: IO[str] | None,
    stream_out: IO[str] | None,
) -> str:
    """Route through the wizard's typed-prompt reader.

    Builds an ephemeral :class:`Question` and hands it to
    :func:`_read_answer_interactive` so the fallback path is byte-for-byte
    the existing wizard behaviour (same hint formatting, same re-prompt
    loop, same default handling).
    """
    # Lazy import to avoid a circular import at module load: wizard does
    # not import select, but keeping the dependency one-directional and
    # lazy is the safest posture.
    from corpus_forge.setup.wizard import (  # noqa: PLC0415
        Question,
        _read_answer_interactive,
    )

    q = Question(
        id="_select",
        prompt=prompt,
        type=qtype,
        default=default,
        env="",
        choices=list(choices or []),
        warn=warn,
    )
    return _read_answer_interactive(
        q,
        stream_in=stream_in or sys.stdin,
        stream_out=stream_out or sys.stdout,
    )


def pick_one(
    prompt: str,
    choices: list[str | tuple[str, str]],
    *,
    default: str | None = None,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> str:
    """Single-choice arrow-key picker; returns the chosen *value*.

    Args:
        prompt: The question text.
        choices: Option strings or ``(label, value)`` pairs.
        default: The value selected by default (must be one of the
            choice *values*). Falls back to the first choice's value.
        stream_in / stream_out: Test/non-interactive seam — when either
            is supplied the rich UI is skipped and the typed-prompt
            fallback drives the prompt.

    Returns:
        The selected option's value (a string).
    """
    labels, values, by_label = _normalise_choices(choices)
    resolved_default: str = (
        default if (default is not None and default in values) else (values[0] if values else "")
    )

    if not _use_fallback(stream_in, stream_out):
        try:
            import questionary  # noqa: PLC0415

            default_label = next(
                (lbl for lbl, val in by_label.items() if val == resolved_default),
                None,
            )
            answer = questionary.select(
                prompt,
                choices=labels,
                default=default_label,
            ).ask()
            if answer is not None:
                return by_label.get(answer, answer)
            # Ctrl-C / EOF inside questionary returns None — fall through
            # to the typed-prompt path so we still hand back a value.
        except ImportError:
            pass

    chosen = _fallback_prompt(
        prompt,
        qtype="choice",
        default=resolved_default,
        choices=values,
        stream_in=stream_in,
        stream_out=stream_out,
    )
    return chosen


def pick_many(
    prompt: str,
    choices: list[str | tuple[str, str]],
    *,
    defaults: list[str] | None = None,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> list[str]:
    """Multi-select checkbox picker; returns the chosen *values*.

    Args:
        prompt: The question text.
        choices: Option strings or ``(label, value)`` pairs.
        defaults: Values pre-checked by default.
        stream_in / stream_out: Test/non-interactive seam (see
            :func:`pick_one`).

    Returns:
        The selected values, in choice order.

    Fallback shape: when the rich UI is unavailable the user types a
    comma- or space-separated list of values (e.g. ``"ocr whisper"``).
    Unknown tokens are dropped; the result preserves choice order.
    """
    labels, values, _by_label = _normalise_choices(choices)
    default_set = set(defaults or [])

    if not _use_fallback(stream_in, stream_out):
        try:
            import questionary  # noqa: PLC0415

            checkbox_choices = [
                questionary.Choice(title=label, value=value, checked=value in default_set)
                for label, value in zip(labels, values, strict=True)
            ]
            answer = questionary.checkbox(prompt, choices=checkbox_choices).ask()
            if answer is not None:
                # Preserve choice order regardless of questionary's order.
                return [v for v in values if v in set(answer)]
        except ImportError:
            pass

    raw = _fallback_prompt(
        prompt,
        qtype="text",
        default=" ".join(v for v in values if v in default_set),
        stream_in=stream_in,
        stream_out=stream_out,
    )
    tokens = {tok for tok in raw.replace(",", " ").split() if tok}
    return [v for v in values if v in tokens]


def ask_text(
    prompt: str,
    *,
    default: str | None = None,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> str:
    """Free-text prompt; returns the entered string (or the default).

    Args:
        prompt: The question text.
        default: Returned when the user enters nothing.
        stream_in / stream_out: Test/non-interactive seam (see
            :func:`pick_one`).
    """
    resolved_default = default or ""

    if not _use_fallback(stream_in, stream_out):
        try:
            import questionary  # noqa: PLC0415

            answer = questionary.text(prompt, default=resolved_default).ask()
            if answer is not None:
                return answer
        except ImportError:
            pass

    return _fallback_prompt(
        prompt,
        qtype="text",
        default=resolved_default,
        stream_in=stream_in,
        stream_out=stream_out,
    )


def confirm(
    prompt: str,
    *,
    default: bool = False,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> bool:
    """Yes/no confirmation; returns a bool.

    Args:
        prompt: The question text.
        default: The value when the user accepts the default.
        stream_in / stream_out: Test/non-interactive seam (see
            :func:`pick_one`).
    """
    if not _use_fallback(stream_in, stream_out):
        try:
            import questionary  # noqa: PLC0415

            answer = questionary.confirm(prompt, default=default).ask()
            if answer is not None:
                return bool(answer)
        except ImportError:
            pass

    chosen = _fallback_prompt(
        prompt,
        qtype="yes_no",
        default="yes" if default else "no",
        stream_in=stream_in,
        stream_out=stream_out,
    )
    return chosen == "yes"
