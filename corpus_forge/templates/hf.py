"""Dynamic chat-template lookup via HF tokenizer."""

from __future__ import annotations

_TEMPLATE_CACHE: dict[str, str] = {}


def hf_template(model_id: str) -> str:
    """Return the Jinja chat_template string for *model_id*.

    Results are cached in ``_TEMPLATE_CACHE`` per model_id.  Callers can clear
    the cache between tests via ``hf_mod._TEMPLATE_CACHE.clear()``.
    """
    if model_id in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[model_id]

    try:
        from transformers import AutoTokenizer  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "transformers not installed; install with `pip install 'corpus-forge[hf]'` "
            "or pass a `custom_jinja` instead of `model_id`."
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    chat_template = getattr(tokenizer, "chat_template", None)
    if not chat_template:
        raise RuntimeError(
            f"Tokenizer for model {model_id!r} has no chat_template attribute. "
            "Try a custom_jinja or a builtin."
        )

    _TEMPLATE_CACHE[model_id] = chat_template
    return chat_template


def clear_cache() -> None:
    """Clear the template cache (convenience wrapper for tests / CLI reloads)."""
    _TEMPLATE_CACHE.clear()
