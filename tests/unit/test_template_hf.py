"""Unit tests for corpus_forge.templates.hf — G-02 RED.

Tests hf_template() lazy load, caching, error path, and the
transformers-availability gate.

Run command:
    uv run pytest tests/unit/test_template_hf.py -v
"""

from __future__ import annotations

import pytest

# Ensure transformers is present in the test environment; if not, every test
# in this file is automatically skipped (not failed).
transformers = pytest.importorskip("transformers")


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _StubTokenizer:
    """Minimal stand-in for AutoTokenizer whose chat_template is settable."""

    def __init__(
        self, chat_template: str | None = "{% for m in messages %}{{ m['content'] }}{% endfor %}"
    ):
        self.chat_template = chat_template


# ---------------------------------------------------------------------------
# hf_template — happy path
# ---------------------------------------------------------------------------


class TestHfTemplateHappyPath:
    """hf_template(model_id) calls from_pretrained and returns the chat_template string."""

    def test_hf_template_calls_AutoTokenizer_from_pretrained(self, monkeypatch):
        """from_pretrained is called with the model_id; the chat_template is returned."""
        import corpus_forge.templates.hf as hf_mod

        stub = _StubTokenizer("stub-jinja-{{ messages }}")
        call_log: list[str] = []

        def _fake_from_pretrained(model_id, *args, **kwargs):
            call_log.append(model_id)
            return stub

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            _fake_from_pretrained,
        )
        # Clear the module-level cache so previous test state doesn't bleed.
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        result = hf_mod.hf_template("meta-llama/Llama-3.1-8B-Instruct")

        assert call_log == ["meta-llama/Llama-3.1-8B-Instruct"]
        assert result == "stub-jinja-{{ messages }}"

    def test_hf_template_returns_string(self, monkeypatch):
        """hf_template always returns a str (not None or tokenizer object)."""
        import corpus_forge.templates.hf as hf_mod

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            lambda *a, **kw: _StubTokenizer("some-template"),
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        result = hf_mod.hf_template("some/model")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# hf_template — caching
# ---------------------------------------------------------------------------


class TestHfTemplateCaching:
    """hf_template caches results per model_id — from_pretrained called only once."""

    def test_hf_template_caches_per_model(self, monkeypatch):
        """Calling hf_template twice for the same model_id calls from_pretrained once."""
        import corpus_forge.templates.hf as hf_mod

        call_count = 0

        def _counting_from_pretrained(model_id, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _StubTokenizer("cached-template")

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            _counting_from_pretrained,
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        result1 = hf_mod.hf_template("some/model")
        result2 = hf_mod.hf_template("some/model")

        assert call_count == 1, f"Expected 1 from_pretrained call, got {call_count}"
        assert result1 == result2 == "cached-template"

    def test_hf_template_caches_independently_per_model_id(self, monkeypatch):
        """Different model_ids produce separate cache entries."""
        import corpus_forge.templates.hf as hf_mod

        templates = {
            "model-A": "template-A",
            "model-B": "template-B",
        }
        call_log: list[str] = []

        def _dispatch_from_pretrained(model_id, *args, **kwargs):
            call_log.append(model_id)
            return _StubTokenizer(templates[model_id])

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            _dispatch_from_pretrained,
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        r_a1 = hf_mod.hf_template("model-A")
        r_a2 = hf_mod.hf_template("model-A")
        r_b1 = hf_mod.hf_template("model-B")
        r_b2 = hf_mod.hf_template("model-B")

        assert r_a1 == r_a2 == "template-A"
        assert r_b1 == r_b2 == "template-B"
        # Each model called exactly once
        assert call_log.count("model-A") == 1
        assert call_log.count("model-B") == 1


# ---------------------------------------------------------------------------
# hf_template — error paths
# ---------------------------------------------------------------------------


class TestHfTemplateErrors:
    """hf_template raises a clear error when chat_template is None or missing."""

    def test_hf_template_raises_clear_error_when_chat_template_missing(self, monkeypatch):
        """chat_template=None causes hf_template to raise an error mentioning model name."""
        import corpus_forge.templates.hf as hf_mod

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            lambda *a, **kw: _StubTokenizer(chat_template=None),
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        with pytest.raises((ValueError, AttributeError, RuntimeError)) as exc_info:
            hf_mod.hf_template("no-template/model")

        error_msg = str(exc_info.value).lower()
        # Error must mention the model or "chat_template" to be actionable
        assert (
            "no-template/model" in error_msg
            or "chat_template" in error_msg
            or "template" in error_msg
        )

    def test_hf_template_error_mentions_model_name(self, monkeypatch):
        """The raised error message includes the offending model_id."""
        import corpus_forge.templates.hf as hf_mod

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            lambda *a, **kw: _StubTokenizer(chat_template=None),
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        with pytest.raises(Exception) as exc_info:
            hf_mod.hf_template("my-org/missing-template-model")

        assert "my-org/missing-template-model" in str(exc_info.value)

    def test_hf_template_from_pretrained_exception_propagates(self, monkeypatch):
        """If from_pretrained itself raises (e.g. HF Hub error), the error propagates."""
        import corpus_forge.templates.hf as hf_mod

        def _raise(*a, **kw):
            raise OSError("Repository not found or no access.")

        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            _raise,
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        with pytest.raises(OSError):
            hf_mod.hf_template("non-existent/model")


# ---------------------------------------------------------------------------
# hf_template — offline guard (HF_HUB_OFFLINE)
# ---------------------------------------------------------------------------


class TestHfTemplateOfflineGuard:
    """hf_template raises a clear error when HF_HUB_OFFLINE=1 and model not cached."""

    def test_hf_template_raises_when_offline_and_not_cached(self, monkeypatch):
        """HF_HUB_OFFLINE=1 + no cache => clear error raised before network call."""
        import corpus_forge.templates.hf as hf_mod

        # Simulate offline by making from_pretrained raise an OSError (what HF does)
        def _offline_error(model_id, *a, **kw):
            raise OSError(
                f"Offline mode is enabled, cannot load model {model_id} from HuggingFace Hub."
            )

        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        monkeypatch.setattr(
            "transformers.AutoTokenizer.from_pretrained",
            _offline_error,
        )
        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        with pytest.raises((OSError, ValueError, RuntimeError)):
            hf_mod.hf_template("some/model-not-cached")


# ── coverage backfill — public clear_cache + missing-transformers ────────


class TestClearCachePublic:
    """Exercises the public ``clear_cache()`` wrapper."""

    def test_clear_cache_empties_module_cache(self) -> None:
        import corpus_forge.templates.hf as hf_mod

        hf_mod._TEMPLATE_CACHE["dummy/model"] = "{{ test }}"  # type: ignore[attr-defined]
        assert hf_mod._TEMPLATE_CACHE  # type: ignore[attr-defined]
        hf_mod.clear_cache()
        assert hf_mod._TEMPLATE_CACHE == {}  # type: ignore[attr-defined]

    def test_clear_cache_is_idempotent(self) -> None:
        import corpus_forge.templates.hf as hf_mod

        hf_mod.clear_cache()
        hf_mod.clear_cache()  # no exception on already-empty cache
        assert hf_mod._TEMPLATE_CACHE == {}  # type: ignore[attr-defined]


class TestTransformersMissing:
    """The [hf] extra isn't installed → helpful RuntimeError, not ImportError."""

    def test_raises_runtime_error_with_install_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        import corpus_forge.templates.hf as hf_mod

        hf_mod._TEMPLATE_CACHE.clear()  # type: ignore[attr-defined]

        # Force the lazy `import transformers` inside hf_template to fail.
        # patch.dict won't propagate inside the function's local import unless
        # the module is genuinely missing — use monkeypatch on sys.modules.
        monkeypatch.setitem(sys.modules, "transformers", None)

        with pytest.raises(RuntimeError) as excinfo:
            hf_mod.hf_template("any/model")
        msg = str(excinfo.value)
        assert "transformers not installed" in msg
        # Error names the install incantation so the user can act.
        assert "corpus-forge[hf]" in msg
        # And it preserves the underlying ImportError for `raise … from exc`.
        assert isinstance(excinfo.value.__cause__, ImportError)
