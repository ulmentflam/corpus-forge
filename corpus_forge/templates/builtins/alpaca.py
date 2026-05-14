"""Alpaca instruction-following builtin template."""

from __future__ import annotations

from typing import Any

from jinja2 import Template

NAME = "alpaca"

JINJA = (
    "{% for m in messages %}"
    "{% if m.role == 'system' %}{{ m.content }}\n\n"
    "{% elif m.role == 'user' %}### Instruction:\n{{ m.content }}\n\n"
    "{% elif m.role == 'assistant' %}### Response:\n{{ m.content }}\n\n"
    "{% endif %}"
    "{% endfor %}"
    "### Response:\n"
)


def render(messages: list[dict[str, Any]]) -> str:
    return Template(JINJA).render(messages=messages)
