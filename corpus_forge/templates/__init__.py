"""Chat template rendering — pure-Python Jinja + dynamic HF tokenizer support."""

from __future__ import annotations

from typing import Any

from jinja2 import Template

from . import hf as _hf
from .builtins import alpaca, chatml, gemma, llama3, qwen, vicuna

_BUILTINS: dict[str, Any] = {
    "chatml": chatml,
    "llama3": llama3,
    "alpaca": alpaca,
    "vicuna": vicuna,
    "gemma": gemma,
    "qwen": qwen,
}


def list_builtins() -> list[str]:
    """Return the names of all bundled builtin templates."""
    return list(_BUILTINS.keys())


def resolve_template(
    template_name: str,
    *,
    backend: Any = None,
    model_id: str | None = None,
    custom_jinja: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a template name + optional overrides to (model_id, custom_jinja).

    Priority: custom_jinja > model_id > backend.get_chat_template_by_name(name).
    If a registered row is found:
      - source='huggingface' -> model_id from the row
      - source='custom' -> custom_jinja from the row
      - source='builtin' -> falls through (caller dispatches to bundled)

    Returns (resolved_model_id, resolved_custom_jinja) — either may stay None,
    in which case caller uses bundled builtin by template_name.
    """
    if custom_jinja is not None or model_id is not None:
        return (model_id, custom_jinja)
    if backend is None:
        return (None, None)
    row = backend.get_chat_template_by_name(template_name)
    if row is None:
        return (None, None)
    if row.get("source") == "huggingface":
        return (row.get("model_id"), None)
    if row.get("source") == "custom":
        return (None, row.get("jinja"))
    return (None, None)


def render(
    template_name: str,
    messages: list[dict[str, Any]],
    *,
    model_id: str | None = None,
    custom_jinja: str | None = None,
) -> str:
    """Render *messages* under one of three template sources.

    Priority:
      1. ``custom_jinja`` (Jinja string) — render directly.
      2. ``model_id`` — fetch HF tokenizer chat_template via ``hf.hf_template``.
      3. ``template_name`` — look up bundled builtin.

    Raises ``KeyError`` when ``template_name`` is not a known builtin and
    neither ``custom_jinja`` nor ``model_id`` is provided.
    """
    if custom_jinja is not None:
        return Template(custom_jinja).render(messages=messages)

    if model_id is not None:
        jinja_src = _hf.hf_template(model_id)
        return Template(jinja_src).render(messages=messages)

    if template_name in _BUILTINS:
        return _BUILTINS[template_name].render(messages)

    raise KeyError(f"unknown template: {template_name!r}; builtins={list(_BUILTINS)}")
