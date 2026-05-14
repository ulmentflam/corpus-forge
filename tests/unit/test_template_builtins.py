"""Unit tests for corpus_forge.templates.builtins — G-02 RED.

Golden-render tests for each of the six canonical chat-format builtins.
Golden strings are hand-rolled by reading the canonical template definitions
for each format; assertions use substring containment (not exact equality)
so minor whitespace variations don't break the tests.

Run command:
    uv run pytest tests/unit/test_template_builtins.py -v
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

_FIXTURE_MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is 2+2?"},
    {"role": "assistant", "content": "4"},
]

_USER_ONLY = [{"role": "user", "content": "Hello"}]


# ---------------------------------------------------------------------------
# ChatML  (<|im_start|> / <|im_end|>)
# ---------------------------------------------------------------------------


class TestChatmlBuiltin:
    """corpus_forge.templates.builtins.chatml — ChatML format."""

    def test_chatml_renders_system_turn(self):
        from corpus_forge.templates.builtins.chatml import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_start|>system" in output
        assert "You are helpful." in output

    def test_chatml_renders_user_turn(self):
        from corpus_forge.templates.builtins.chatml import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_start|>user" in output
        assert "What is 2+2?" in output

    def test_chatml_renders_assistant_turn(self):
        from corpus_forge.templates.builtins.chatml import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_start|>assistant" in output
        assert "4" in output

    def test_chatml_renders_end_tokens(self):
        from corpus_forge.templates.builtins.chatml import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_end|>" in output

    def test_chatml_name_constant(self):
        from corpus_forge.templates.builtins.chatml import NAME

        assert NAME == "chatml"

    def test_chatml_jinja_constant_is_string(self):
        from corpus_forge.templates.builtins.chatml import JINJA

        assert isinstance(JINJA, str)
        assert len(JINJA) > 0

    def test_chatml_user_only_renders(self):
        from corpus_forge.templates.builtins.chatml import render

        output = render(_USER_ONLY)
        assert "<|im_start|>user" in output
        assert "Hello" in output


# ---------------------------------------------------------------------------
# Llama 3  (<|begin_of_text|> / <|start_header_id|>)
# ---------------------------------------------------------------------------


class TestLlama3Builtin:
    """corpus_forge.templates.builtins.llama3 — Llama-3 instruct format."""

    def test_llama3_renders_begin_of_text(self):
        from corpus_forge.templates.builtins.llama3 import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|begin_of_text|>" in output

    def test_llama3_renders_user_header(self):
        from corpus_forge.templates.builtins.llama3 import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|start_header_id|>user<|end_header_id|>" in output

    def test_llama3_renders_system_header(self):
        from corpus_forge.templates.builtins.llama3 import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|start_header_id|>system<|end_header_id|>" in output

    def test_llama3_renders_assistant_header(self):
        from corpus_forge.templates.builtins.llama3 import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|start_header_id|>assistant<|end_header_id|>" in output

    def test_llama3_renders_eot_id(self):
        from corpus_forge.templates.builtins.llama3 import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|eot_id|>" in output

    def test_llama3_name_constant(self):
        from corpus_forge.templates.builtins.llama3 import NAME

        assert NAME == "llama3"

    def test_llama3_jinja_constant_is_string(self):
        from corpus_forge.templates.builtins.llama3 import JINJA

        assert isinstance(JINJA, str)


# ---------------------------------------------------------------------------
# Alpaca  (### Instruction: / ### Response:)
# ---------------------------------------------------------------------------


class TestAlpacaBuiltin:
    """corpus_forge.templates.builtins.alpaca — Alpaca instruct format."""

    def test_alpaca_renders_instruction_header(self):
        from corpus_forge.templates.builtins.alpaca import render

        output = render(_FIXTURE_MESSAGES)
        assert "### Instruction:" in output

    def test_alpaca_renders_response_header(self):
        from corpus_forge.templates.builtins.alpaca import render

        output = render(_FIXTURE_MESSAGES)
        assert "### Response:" in output

    def test_alpaca_contains_user_content(self):
        from corpus_forge.templates.builtins.alpaca import render

        output = render(_FIXTURE_MESSAGES)
        assert "What is 2+2?" in output

    def test_alpaca_contains_assistant_content(self):
        from corpus_forge.templates.builtins.alpaca import render

        output = render(_FIXTURE_MESSAGES)
        assert "4" in output

    def test_alpaca_name_constant(self):
        from corpus_forge.templates.builtins.alpaca import NAME

        assert NAME == "alpaca"

    def test_alpaca_jinja_constant_is_string(self):
        from corpus_forge.templates.builtins.alpaca import JINJA

        assert isinstance(JINJA, str)

    def test_alpaca_user_only_renders(self):
        from corpus_forge.templates.builtins.alpaca import render

        output = render(_USER_ONLY)
        assert "Hello" in output
        assert len(output) > 0


# ---------------------------------------------------------------------------
# Vicuna  (USER: / ASSISTANT:)
# ---------------------------------------------------------------------------


class TestVicunaBuiltin:
    """corpus_forge.templates.builtins.vicuna — Vicuna chat format."""

    def test_vicuna_renders_user_prefix(self):
        from corpus_forge.templates.builtins.vicuna import render

        output = render(_FIXTURE_MESSAGES)
        assert "USER:" in output

    def test_vicuna_renders_assistant_prefix(self):
        from corpus_forge.templates.builtins.vicuna import render

        output = render(_FIXTURE_MESSAGES)
        assert "ASSISTANT:" in output

    def test_vicuna_contains_user_content(self):
        from corpus_forge.templates.builtins.vicuna import render

        output = render(_FIXTURE_MESSAGES)
        assert "What is 2+2?" in output

    def test_vicuna_contains_assistant_content(self):
        from corpus_forge.templates.builtins.vicuna import render

        output = render(_FIXTURE_MESSAGES)
        assert "4" in output

    def test_vicuna_name_constant(self):
        from corpus_forge.templates.builtins.vicuna import NAME

        assert NAME == "vicuna"

    def test_vicuna_jinja_constant_is_string(self):
        from corpus_forge.templates.builtins.vicuna import JINJA

        assert isinstance(JINJA, str)


# ---------------------------------------------------------------------------
# Gemma  (<start_of_turn> / <end_of_turn>)
# ---------------------------------------------------------------------------


class TestGemmaBuiltin:
    """corpus_forge.templates.builtins.gemma — Gemma instruct format."""

    def test_gemma_renders_start_of_turn_user(self):
        from corpus_forge.templates.builtins.gemma import render

        output = render(_FIXTURE_MESSAGES)
        assert "<start_of_turn>user" in output

    def test_gemma_renders_end_of_turn(self):
        from corpus_forge.templates.builtins.gemma import render

        output = render(_FIXTURE_MESSAGES)
        assert "<end_of_turn>" in output

    def test_gemma_renders_model_turn(self):
        from corpus_forge.templates.builtins.gemma import render

        output = render(_FIXTURE_MESSAGES)
        # Gemma uses 'model' for the assistant turn
        assert "<start_of_turn>model" in output

    def test_gemma_contains_user_content(self):
        from corpus_forge.templates.builtins.gemma import render

        output = render(_FIXTURE_MESSAGES)
        assert "What is 2+2?" in output

    def test_gemma_contains_assistant_content(self):
        from corpus_forge.templates.builtins.gemma import render

        output = render(_FIXTURE_MESSAGES)
        assert "4" in output

    def test_gemma_name_constant(self):
        from corpus_forge.templates.builtins.gemma import NAME

        assert NAME == "gemma"

    def test_gemma_jinja_constant_is_string(self):
        from corpus_forge.templates.builtins.gemma import JINJA

        assert isinstance(JINJA, str)


# ---------------------------------------------------------------------------
# Qwen  (ChatML-style with im_start/im_end)
# ---------------------------------------------------------------------------


class TestQwenBuiltin:
    """corpus_forge.templates.builtins.qwen — Qwen chat format (ChatML-style markers)."""

    def test_qwen_renders_user_marker(self):
        from corpus_forge.templates.builtins.qwen import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_start|>user" in output

    def test_qwen_renders_assistant_marker(self):
        from corpus_forge.templates.builtins.qwen import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_start|>assistant" in output

    def test_qwen_renders_end_tokens(self):
        from corpus_forge.templates.builtins.qwen import render

        output = render(_FIXTURE_MESSAGES)
        assert "<|im_end|>" in output

    def test_qwen_contains_user_content(self):
        from corpus_forge.templates.builtins.qwen import render

        output = render(_FIXTURE_MESSAGES)
        assert "What is 2+2?" in output

    def test_qwen_contains_assistant_content(self):
        from corpus_forge.templates.builtins.qwen import render

        output = render(_FIXTURE_MESSAGES)
        assert "4" in output

    def test_qwen_name_constant(self):
        from corpus_forge.templates.builtins.qwen import NAME

        assert NAME == "qwen"

    def test_qwen_jinja_constant_is_string(self):
        from corpus_forge.templates.builtins.qwen import JINJA

        assert isinstance(JINJA, str)

    def test_qwen_system_handling_differs_from_chatml(self):
        """Qwen may handle the system role differently than pure ChatML.

        Both use im_start/im_end markers; the distinction is in system-role
        rendering (Qwen may fold it into the first user turn or use a special
        prefix). This test just verifies the system content appears somewhere.
        """
        from corpus_forge.templates.builtins.qwen import render

        output = render(_FIXTURE_MESSAGES)
        assert "You are helpful." in output


# ---------------------------------------------------------------------------
# Cross-builtin contract
# ---------------------------------------------------------------------------


class TestBuiltinContracts:
    """All builtins must satisfy the shared module contract."""

    _BUILTIN_NAMES = ("chatml", "llama3", "alpaca", "vicuna", "gemma", "qwen")

    @pytest.mark.parametrize("builtin_name", _BUILTIN_NAMES)
    def test_each_builtin_has_name_jinja_render(self, builtin_name):
        import importlib

        mod = importlib.import_module(f"corpus_forge.templates.builtins.{builtin_name}")
        assert hasattr(mod, "NAME"), f"{builtin_name} missing NAME"
        assert hasattr(mod, "JINJA"), f"{builtin_name} missing JINJA"
        assert hasattr(mod, "render"), f"{builtin_name} missing render()"
        assert callable(mod.render), f"{builtin_name}.render is not callable"

    @pytest.mark.parametrize("builtin_name", _BUILTIN_NAMES)
    def test_each_builtin_name_matches_module(self, builtin_name):
        import importlib

        mod = importlib.import_module(f"corpus_forge.templates.builtins.{builtin_name}")
        assert builtin_name == mod.NAME

    @pytest.mark.parametrize("builtin_name", _BUILTIN_NAMES)
    def test_render_returns_non_empty_string(self, builtin_name):
        import importlib

        mod = importlib.import_module(f"corpus_forge.templates.builtins.{builtin_name}")
        result = mod.render(_FIXTURE_MESSAGES)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("builtin_name", _BUILTIN_NAMES)
    def test_render_with_empty_messages_does_not_raise(self, builtin_name):
        """Rendering an empty message list should not raise (may return empty string)."""
        import importlib

        mod = importlib.import_module(f"corpus_forge.templates.builtins.{builtin_name}")
        # Should not raise, result can be empty or minimal
        result = mod.render([])
        assert isinstance(result, str)

    @pytest.mark.parametrize("builtin_name", _BUILTIN_NAMES)
    def test_jinja_template_uses_messages_variable(self, builtin_name):
        """JINJA string should reference 'messages' so it's renderable by jinja2."""
        import importlib

        mod = importlib.import_module(f"corpus_forge.templates.builtins.{builtin_name}")
        assert "messages" in mod.JINJA
