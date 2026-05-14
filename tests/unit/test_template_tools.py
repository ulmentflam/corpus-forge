"""Unit tests for corpus_forge.templates.tools — format_tool_calls coverage."""

from __future__ import annotations

from typing import ClassVar

import pytest

from corpus_forge.templates.tools import format_tool_calls

pytestmark = pytest.mark.unit


class TestFormatToolCallsEmpty:
    def test_format_tool_calls_empty_list_returns_empty_string(self) -> None:
        """Empty tool_calls list must produce empty string (early-return branch)."""
        result = format_tool_calls("chatml", [])
        assert result == ""

    def test_format_tool_calls_empty_list_any_template(self) -> None:
        """Empty list returns '' regardless of template name."""
        for name in ("chatml", "llama3", "qwen", "alpaca", "vicuna", "gemma", "unknown"):
            assert format_tool_calls(name, []) == ""


class TestFormatToolCallsModernTemplates:
    """chatml / llama3 / qwen use <tool_call>…</tool_call> blocks."""

    _CALL: ClassVar[dict] = {"name": "search", "arguments": {"query": "hello"}}

    def test_format_tool_calls_chatml_uses_tool_call_blocks(self) -> None:
        result = format_tool_calls("chatml", [self._CALL])
        assert "<tool_call>" in result
        assert "</tool_call>" in result

    def test_format_tool_calls_chatml_embeds_json(self) -> None:
        result = format_tool_calls("chatml", [self._CALL])
        # The JSON payload must appear inside the block
        assert '"search"' in result

    def test_format_tool_calls_chatml_multiple_calls(self) -> None:
        calls = [
            {"name": "fn_a", "arguments": {}},
            {"name": "fn_b", "arguments": {"x": 1}},
        ]
        result = format_tool_calls("chatml", calls)
        assert result.count("<tool_call>") == 2
        assert result.count("</tool_call>") == 2

    def test_format_tool_calls_llama3_uses_tool_call_blocks(self) -> None:
        result = format_tool_calls("llama3", [self._CALL])
        assert "<tool_call>" in result
        assert "</tool_call>" in result

    def test_format_tool_calls_qwen_uses_tool_call_blocks(self) -> None:
        result = format_tool_calls("qwen", [self._CALL])
        assert "<tool_call>" in result
        assert "</tool_call>" in result

    def test_format_tool_calls_modern_no_function_call_prose(self) -> None:
        """Modern templates must NOT fall back to [function call] prose."""
        result = format_tool_calls("llama3", [self._CALL])
        assert "[function call]" not in result


class TestFormatToolCallsOlderTemplates:
    """alpaca / vicuna / gemma collapse to [function call] prose."""

    _CALL: ClassVar[dict] = {"name": "calculator", "arguments": {"a": 1, "b": 2}}

    def test_format_tool_calls_alpaca_collapses_to_function_call_prose(self) -> None:
        result = format_tool_calls("alpaca", [self._CALL])
        assert "[function call]" in result
        assert "calculator" in result

    def test_format_tool_calls_vicuna_collapses_to_function_call_prose(self) -> None:
        result = format_tool_calls("vicuna", [self._CALL])
        assert "[function call]" in result
        assert "calculator" in result

    def test_format_tool_calls_gemma_collapses_to_function_call_prose(self) -> None:
        result = format_tool_calls("gemma", [self._CALL])
        assert "[function call]" in result
        assert "calculator" in result

    def test_format_tool_calls_older_no_tool_call_tags(self) -> None:
        """Older templates must NOT emit <tool_call> tags."""
        result = format_tool_calls("alpaca", [self._CALL])
        assert "<tool_call>" not in result

    def test_format_tool_calls_older_missing_name_falls_back_to_question_mark(self) -> None:
        """Tool call without 'name' key renders as '?' in prose output."""
        result = format_tool_calls("alpaca", [{"arguments": {}}])
        assert "[function call]" in result
        assert "?" in result

    def test_format_tool_calls_older_multiple_calls(self) -> None:
        calls = [
            {"name": "fn_a", "arguments": {}},
            {"name": "fn_b", "arguments": {"x": 1}},
        ]
        result = format_tool_calls("alpaca", calls)
        assert result.count("[function call]") == 2


class TestFormatToolCallsUnknownTemplate:
    def test_format_tool_calls_unknown_template_falls_back_to_prose(self) -> None:
        """Unknown template names fall through to the prose branch."""
        call = {"name": "mystery_fn", "arguments": {"key": "val"}}
        result = format_tool_calls("totally_unknown_template_xyz", [call])
        assert "[function call]" in result
        assert "mystery_fn" in result
        assert "<tool_call>" not in result
