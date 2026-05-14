"""Unit tests for corpus_forge.templates — G-02 RED.

Tests for the registry public surface:
  render(template_name, messages, *, model_id, custom_jinja) -> str
  list_builtins() -> list[str]

Run command:
    uv run pytest tests/unit/test_template_registry.py -v
"""

from __future__ import annotations

from typing import ClassVar

import pytest

# ---------------------------------------------------------------------------
# list_builtins
# ---------------------------------------------------------------------------


class TestListBuiltins:
    """list_builtins() returns exactly the six canonical builtin names."""

    def test_list_builtins_returns_six_names(self):
        from corpus_forge.templates import list_builtins

        names = list_builtins()
        assert isinstance(names, list)
        assert len(names) == 6
        for expected in ("chatml", "llama3", "alpaca", "vicuna", "gemma", "qwen"):
            assert expected in names, f"Expected '{expected}' in list_builtins(), got {names}"


# ---------------------------------------------------------------------------
# render — builtin dispatch
# ---------------------------------------------------------------------------


class TestRenderBuiltinDispatch:
    """render() dispatches to the correct builtin when template_name matches."""

    _USER_MSG: ClassVar[list[dict]] = [{"role": "user", "content": "hi"}]

    def test_render_dispatches_to_chatml_builtin(self):
        from corpus_forge.templates import render

        result = render("chatml", self._USER_MSG)
        assert isinstance(result, str)
        assert len(result) > 0
        # ChatML markers must appear
        assert "<|im_start|>" in result
        assert "<|im_end|>" in result

    def test_render_dispatches_to_llama3_builtin(self):
        from corpus_forge.templates import render

        result = render("llama3", self._USER_MSG)
        assert isinstance(result, str)
        assert "<|begin_of_text|>" in result or "<|start_header_id|>" in result

    def test_render_dispatches_to_alpaca_builtin(self):
        from corpus_forge.templates import render

        result = render("alpaca", self._USER_MSG)
        assert isinstance(result, str)
        assert "### Instruction:" in result or "### Input:" in result or "### Response:" in result

    def test_render_dispatches_to_vicuna_builtin(self):
        from corpus_forge.templates import render

        result = render("vicuna", self._USER_MSG)
        assert isinstance(result, str)
        assert "USER:" in result or "ASSISTANT:" in result

    def test_render_dispatches_to_gemma_builtin(self):
        from corpus_forge.templates import render

        result = render("gemma", self._USER_MSG)
        assert isinstance(result, str)
        assert "<start_of_turn>" in result or "<end_of_turn>" in result

    def test_render_dispatches_to_qwen_builtin(self):
        from corpus_forge.templates import render

        result = render("qwen", self._USER_MSG)
        assert isinstance(result, str)
        assert "<|im_start|>" in result


# ---------------------------------------------------------------------------
# render — custom_jinja
# ---------------------------------------------------------------------------


class TestRenderCustomJinja:
    """render() uses custom_jinja when provided, ignoring template_name."""

    def test_render_uses_custom_jinja_when_provided(self):
        from corpus_forge.templates import render

        messages = [{"role": "user", "content": "hello-world"}]
        result = render("ignored_name", messages, custom_jinja="{{ messages[0]['content'] }}")
        assert result == "hello-world"

    def test_render_custom_jinja_multi_message(self):
        from corpus_forge.templates import render

        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
        result = render(
            "ignored",
            messages,
            custom_jinja="{{ messages | length }}",
        )
        assert result.strip() == "2"

    def test_render_custom_jinja_takes_precedence_over_builtin_name(self):
        """custom_jinja wins even when template_name matches a known builtin."""
        from corpus_forge.templates import render

        messages = [{"role": "user", "content": "override-test"}]
        result = render("chatml", messages, custom_jinja="{{ messages[0]['content'] }}")
        # Should NOT produce ChatML markers — custom template overrides
        assert result == "override-test"
        assert "<|im_start|>" not in result


# ---------------------------------------------------------------------------
# render — hf path
# ---------------------------------------------------------------------------


class TestRenderHfPath:
    """render() calls hf.hf_template() when model_id is provided."""

    _STUB_JINJA = "{% for m in messages %}{{ m['role'] }}:{{ m['content'] }}{% endfor %}"

    def test_render_uses_hf_when_model_id_provided(self, monkeypatch):
        """render(..., model_id=...) calls hf_template and uses the returned Jinja string."""
        import corpus_forge.templates.hf as hf_mod

        call_log: list[str] = []

        def _stub_hf_template(model_id: str) -> str:
            call_log.append(model_id)
            return self._STUB_JINJA

        monkeypatch.setattr(hf_mod, "hf_template", _stub_hf_template)

        from corpus_forge.templates import render

        messages = [{"role": "user", "content": "hi"}]
        result = render("ignored", messages, model_id="meta-llama/Llama-3.1-8B-Instruct")
        assert call_log == ["meta-llama/Llama-3.1-8B-Instruct"]
        assert "user:hi" in result

    def test_render_model_id_takes_precedence_over_builtin_name(self, monkeypatch):
        """model_id route wins even if template_name also names a builtin."""
        import corpus_forge.templates.hf as hf_mod

        monkeypatch.setattr(hf_mod, "hf_template", lambda _model_id: "HF:{{ messages[0]['role'] }}")

        from corpus_forge.templates import render

        result = render("chatml", [{"role": "user", "content": "x"}], model_id="some/model")
        assert "HF:user" in result
        # Builtin ChatML markers must NOT appear since the HF path was taken
        assert "<|im_start|>" not in result


# ---------------------------------------------------------------------------
# render — invalid template name
# ---------------------------------------------------------------------------


class TestRenderInvalidName:
    """render() raises on unknown template_name with no fallback."""

    def test_render_invalid_template_name_raises(self):
        from corpus_forge.templates import render

        with pytest.raises((KeyError, ValueError)):
            render("totally_unknown_format_xyz", [{"role": "user", "content": "x"}])

    def test_render_empty_string_name_raises(self):
        from corpus_forge.templates import render

        with pytest.raises((KeyError, ValueError)):
            render("", [{"role": "user", "content": "x"}])

    def test_render_none_name_raises(self):
        """Passing None as template_name with no custom_jinja or model_id raises."""
        from corpus_forge.templates import render

        with pytest.raises((KeyError, ValueError, TypeError)):
            render(None, [{"role": "user", "content": "x"}])  # type: ignore[arg-type]
