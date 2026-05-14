"""Llama-3 builtin template."""

from __future__ import annotations

from typing import Any

from jinja2 import Template

NAME = "llama3"

JINJA = (
    "<|begin_of_text|>"
    "{% for m in messages %}"
    "<|start_header_id|>{{ m.role }}<|end_header_id|>\n\n"
    "{{ m.content }}<|eot_id|>"
    "{% endfor %}"
    "<|start_header_id|>assistant<|end_header_id|>\n\n"
)


def render(messages: list[dict[str, Any]]) -> str:
    return Template(JINJA).render(messages=messages)
