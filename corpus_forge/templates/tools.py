"""Tool-call rendering policy per template family."""

from __future__ import annotations

import json

_MODERN_TEMPLATES = frozenset({"chatml", "qwen", "llama3"})


def format_tool_calls(template_name: str, tool_calls: list[dict]) -> str:
    """Return a block of text rendering *tool_calls* for the template's family.

    Modern templates (chatml, qwen, llama3) use explicit ``<tool_call>`` blocks.
    Older templates (alpaca, vicuna, gemma) collapse to prose function-call notation.
    """
    if not tool_calls:
        return ""
    if template_name in _MODERN_TEMPLATES:
        return "\n".join(
            f"<tool_call>{json.dumps(tc, separators=(',', ':'))}</tool_call>" for tc in tool_calls
        )
    return "\n".join(
        f"[function call] {tc.get('name', '?')}({json.dumps(tc.get('arguments', {}))})"
        for tc in tool_calls
    )
