"""Drift panel renderer + 3-way prompt helper (Phase L Wave 5).

Render policy:

- ``non_interactive=True`` and ``background=True`` → return ``"now"``
  (auto-run the rerun in the background, no panel).
- ``non_interactive=True`` and ``background=False`` → return ``"later"``
  (don't ask, don't run; record the drift for the next foreground run).
- Otherwise: render the panel via the corpus-forge console and ask the
  user via :class:`corpus_forge.ui.prompts.Prompt` with the 3-way
  ``choices=["now", "later", "skip"]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from rich.console import Console
from rich.panel import Panel

from corpus_forge.ui.console import console as _default_console
from corpus_forge.ui.prompts import Prompt

if TYPE_CHECKING:
    from corpus_forge.embedders.fingerprint import EmbedderDrift


def _format_drift_line(d: EmbedderDrift) -> str:
    minutes = max(1, int(d.est_seconds // 60))
    return (
        f"Was:  {d.name} ({d.was_dimension}-dim, model={d.was_model_id})  fp={d.fingerprint_was}…\n"
        f"Now:  {d.name} ({d.now_dimension}-dim, model={d.now_model_id})  fp={d.fingerprint_now}…\n"
        f"{d.chunks_to_rerun:,} chunks need re-embedding (~{minutes} min)"
    )


def prompt_for_drift(
    drifts: list[EmbedderDrift],
    *,
    background: bool,
    non_interactive: bool,
    console: Console | None = None,
) -> Literal["now", "later", "skip"]:
    """Render the drift panel and prompt the user (or auto-resolve)."""

    if not drifts:
        return "skip"
    if non_interactive and background:
        return "now"
    if non_interactive and not background:
        return "later"

    target = console if console is not None else _default_console

    body = "\n\n".join(_format_drift_line(d) for d in drifts)
    panel = Panel(body, title="Embedder changed", border_style="brand.forge")
    target.print(panel)

    answer = Prompt.ask(
        "Rerun now, later, or skip?",
        choices=["now", "later", "skip"],
        default="now",
        console=target,
    )
    return answer  # type: ignore[return-value]


__all__ = ["prompt_for_drift"]
