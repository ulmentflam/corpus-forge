"""Vicuna chat builtin template (USER: / ASSISTANT:)."""

from __future__ import annotations

from typing import Any

from jinja2 import Template

NAME = "vicuna"

JINJA = (
    "{% for m in messages %}"
    "{% if m.role == 'system' %}{{ m.content }}\n\n"
    "{% elif m.role == 'user' %}USER: {{ m.content }}\n"
    "{% elif m.role == 'assistant' %}ASSISTANT: {{ m.content }}\n"
    "{% endif %}"
    "{% endfor %}"
    "ASSISTANT:"
)


def render(messages: list[dict[str, Any]]) -> str:
    return Template(JINJA).render(messages=messages)
