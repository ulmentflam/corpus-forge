"""Shared UI primitives for the corpus-forge CLI (Phase L Wave 1).

Re-exports the status-line wrappers, the banner renderer, the progress
factory, and the prompt helpers so call sites can ``from corpus_forge
import ui`` and reach everything.

The singleton Rich console lives at ``corpus_forge.ui.console.console``
(intentionally NOT re-exported here so the submodule attribute name
``console`` continues to resolve to the *module* — call sites that do
``import corpus_forge.ui.console as console_mod`` rely on that).
"""

from __future__ import annotations

from . import banner, console, progress
from .banner import render_banner
from .console import error, info, ok, panel, title, warn
from .progress import make_progress
from .prompts import Confirm, Prompt

__all__ = [
    "Confirm",
    "Prompt",
    "banner",
    "console",
    "error",
    "info",
    "make_progress",
    "ok",
    "panel",
    "progress",
    "render_banner",
    "title",
    "warn",
]
