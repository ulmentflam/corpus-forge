"""Qwen chat builtin template (ChatML-style markers)."""

from __future__ import annotations

from typing import Any

from jinja2 import Template

NAME = "qwen"

JINJA = (
    "{% for m in messages %}<|im_start|>{{ m.role }}\n"
    "{{ m.content }}<|im_end|>\n"
    "{% endfor %}<|im_start|>assistant\n"
)


def render(messages: list[dict[str, Any]]) -> str:
    return Template(JINJA).render(messages=messages)
