"""Gemma instruct builtin template (<start_of_turn> / <end_of_turn>)."""

from __future__ import annotations

from typing import Any

from jinja2 import Template

NAME = "gemma"

JINJA = (
    "{% for m in messages %}"
    "<start_of_turn>{{ 'user' if m.role == 'system' else m.role }}\n"
    "{{ m.content }}<end_of_turn>\n"
    "{% endfor %}"
    "<start_of_turn>model\n"
)


def render(messages: list[dict[str, Any]]) -> str:
    return Template(JINJA).render(messages=messages)
