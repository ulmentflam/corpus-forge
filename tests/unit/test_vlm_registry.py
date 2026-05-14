"""Phase D / Wave 4 (E-01) — `VLMBackend` Protocol + `VLMRegistry`.

This test module pins the public surface of the new VLM (vision-language
model) layer that Wave 5 will dispatch into for PDF rasterised-page OCR
and image-extraction. The shape mirrors the embedder layer:

- A runtime-checkable :class:`Protocol` with three methods —
  ``describe_image``, ``extract_page``, ``warmup``.
- A flat registry keyed on ``backend.name`` (last-write-wins, mirrors
  :class:`corpus_forge.embedders.registry.EmbedderRegistry`).
- A module-level ``registry`` singleton + a ``get_active_vlm(config)``
  factory that resolves the active backend from
  :class:`corpus_forge.config.VLMConfig`.
- A :class:`NoopVLM` that raises :class:`VLMUnavailableError` on every
  operational call — explicit "no backend configured" beats silent
  fall-through.

No live network in this test module. The Ollama and Mistral backends
have their own dedicated unit tests with fully-mocked ``requests``.
"""

from __future__ import annotations

import os
from typing import runtime_checkable
from unittest.mock import patch

import pytest

from corpus_forge.vlm import (
    NoopVLM,
    VLMBackend,
    VLMError,
    VLMRegistry,
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
    get_active_vlm,
    registry,
)

# ── Protocol surface ────────────────────────────────────────────────────


def test_vlm_backend_is_runtime_checkable():
    """The Protocol must be ``@runtime_checkable`` so ``isinstance``
    works in dispatch code paths."""
    # The decorator stamps `__runtime_checkable__` on the class.
    assert getattr(VLMBackend, "_is_runtime_protocol", False) is True


def test_vlm_backend_protocol_isinstance_stub():
    """A duck-typed class satisfies the Protocol structurally."""

    class _Stub:
        name = "stub"

        def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
            return "stub"

        def extract_page(self, image: bytes, *, page_number: int) -> str:
            return "stub"

        def warmup(self) -> None:
            return None

    assert isinstance(_Stub(), VLMBackend)


def test_vlm_backend_protocol_rejects_missing_methods():
    """A class missing ``extract_page`` does NOT satisfy the Protocol."""

    class _Incomplete:
        name = "incomplete"

        def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
            return ""

        def warmup(self) -> None:
            return None

    assert not isinstance(_Incomplete(), VLMBackend)


# ── Exception hierarchy ─────────────────────────────────────────────────


class TestExceptionHierarchy:
    """The four custom exceptions must form a clean hierarchy so callers
    can ``except VLMError`` once and get every operational failure."""

    def test_unavailable_is_vlm_error(self):
        assert issubclass(VLMUnavailableError, VLMError)

    def test_timeout_is_vlm_error(self):
        assert issubclass(VLMTimeoutError, VLMError)

    def test_response_is_vlm_error(self):
        assert issubclass(VLMResponseError, VLMError)

    def test_vlm_error_is_exception(self):
        assert issubclass(VLMError, Exception)

    def test_distinct_classes(self):
        """The three operational subclasses must be distinct so callers
        can discriminate on the failure mode."""
        assert VLMUnavailableError is not VLMTimeoutError
        assert VLMTimeoutError is not VLMResponseError
        assert VLMResponseError is not VLMUnavailableError


# ── NoopVLM ─────────────────────────────────────────────────────────────


class TestNoopVLM:
    """When ``config.vlm.backend == "none"``, ``get_active_vlm`` returns
    a :class:`NoopVLM`. Every operational call MUST raise so callers
    fail loud at the point of attempted OCR instead of silently
    returning empty Markdown."""

    def test_name_is_none(self):
        assert NoopVLM().name == "none"

    def test_satisfies_protocol(self):
        assert isinstance(NoopVLM(), VLMBackend)

    def test_describe_image_raises(self):
        v = NoopVLM()
        with pytest.raises(VLMUnavailableError):
            v.describe_image(b"\x89PNG")

    def test_describe_image_with_prompt_raises(self):
        v = NoopVLM()
        with pytest.raises(VLMUnavailableError):
            v.describe_image(b"\x89PNG", prompt="anything")

    def test_extract_page_raises(self):
        v = NoopVLM()
        with pytest.raises(VLMUnavailableError):
            v.extract_page(b"\x89PNG", page_number=1)

    def test_warmup_raises(self):
        """``warmup()`` is the canonical health check; a Noop must fail
        loudly there too so misconfiguration is caught early."""
        v = NoopVLM()
        with pytest.raises(VLMUnavailableError):
            v.warmup()


# ── VLMRegistry ─────────────────────────────────────────────────────────


def _stub_backend(name: str = "stub") -> VLMBackend:
    class _Stub:
        def __init__(self, nm: str):
            self.name = nm

        def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
            return f"{self.name}:describe"

        def extract_page(self, image: bytes, *, page_number: int) -> str:
            return f"{self.name}:page{page_number}"

        def warmup(self) -> None:
            return None

    return _Stub(name)


class TestVLMRegistry:
    def test_empty_get_returns_none(self):
        r = VLMRegistry()
        assert r.get("missing") is None

    def test_register_then_get(self):
        r = VLMRegistry()
        b = _stub_backend("ollama")
        r.register(b)
        assert r.get("ollama") is b

    def test_register_last_write_wins(self):
        r = VLMRegistry()
        first = _stub_backend("ollama")
        second = _stub_backend("ollama")
        r.register(first)
        r.register(second)
        assert r.get("ollama") is second

    def test_list_names(self):
        r = VLMRegistry()
        r.register(_stub_backend("ollama"))
        r.register(_stub_backend("mistral"))
        names = sorted(r.list_names())
        assert names == ["mistral", "ollama"]

    def test_clear(self):
        r = VLMRegistry()
        r.register(_stub_backend("ollama"))
        r.clear()
        assert r.get("ollama") is None
        assert r.list_names() == []

    def test_module_singleton_exists(self):
        """A module-level ``registry`` singleton MUST exist (mirrors
        :data:`corpus_forge.embedders.registry.registry`)."""
        assert isinstance(registry, VLMRegistry)


# ── get_active_vlm (factory off Config.vlm) ─────────────────────────────


def _build_config(vlm_kwargs: dict | None = None):
    """Minimal :class:`Config` with a single VLM block."""
    from corpus_forge.config import (
        BackendConfig,
        Config,
        DaemonConfig,
        DatasetConfig,
        DatasetSourceConfig,
        EmbedderConfig,
        VLMConfig,
    )

    return Config(
        backend=BackendConfig(kind="postgres", dsn="postgresql://localhost/test"),
        daemon=DaemonConfig(),
        datasets=[
            DatasetConfig(
                name="d",
                kind="text",
                sources=[DatasetSourceConfig(plugin="markdown_vault", chunker="markdown")],
            )
        ],
        embedders=[
            EmbedderConfig(name="e", provider="sentence_transformers", model_id="any", dimension=8)
        ],
        vlm=VLMConfig(**(vlm_kwargs or {})),
    )


class TestGetActiveVlm:
    def test_default_config_returns_noop(self):
        """``Config()`` with no ``[vlm]`` block defaults backend="none"
        → :class:`NoopVLM`."""
        cfg = _build_config()
        v = get_active_vlm(cfg)
        assert isinstance(v, NoopVLM)

    def test_noop_extract_page_raises(self):
        cfg = _build_config()
        v = get_active_vlm(cfg)
        with pytest.raises(VLMUnavailableError):
            v.extract_page(b"\x89PNG", page_number=1)

    def test_ollama_backend(self):
        """``backend = "ollama"`` constructs :class:`OllamaVLM`."""
        from corpus_forge.vlm.ollama import OllamaVLM

        cfg = _build_config({"backend": "ollama", "ollama_model": "qwen2.5vl:7b"})
        v = get_active_vlm(cfg)
        assert isinstance(v, OllamaVLM)
        assert v.model == "qwen2.5vl:7b"

    def test_ollama_passes_url_and_timeout(self):
        from corpus_forge.vlm.ollama import OllamaVLM

        cfg = _build_config(
            {
                "backend": "ollama",
                "ollama_url": "http://example.invalid:11434",
                "timeout_s": 30.0,
            }
        )
        v = get_active_vlm(cfg)
        assert isinstance(v, OllamaVLM)
        assert v.ollama_url == "http://example.invalid:11434"
        assert v.timeout_s == 30.0

    def test_mistral_backend(self):
        """``backend = "mistral"`` with the api-key env var set
        constructs :class:`MistralOCR`."""
        from corpus_forge.vlm.mistral import MistralOCR

        with patch.dict(os.environ, {"MISTRAL_API_KEY": "sk-test"}, clear=False):
            cfg = _build_config({"backend": "mistral"})
            v = get_active_vlm(cfg)
        assert isinstance(v, MistralOCR)
        assert v.api_key == "sk-test"

    def test_mistral_without_key_raises_unavailable(self):
        """Missing api-key env var → :class:`VLMUnavailableError` at
        ``get_active_vlm`` time (not at request time)."""
        # Make sure the env var is absent.
        env = {k: v for k, v in os.environ.items() if k != "MISTRAL_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            cfg = _build_config({"backend": "mistral"})
            with pytest.raises(VLMUnavailableError):
                get_active_vlm(cfg)

    def test_mistral_custom_env_var_name(self):
        """A non-default ``mistral_api_key_env`` is honoured."""
        from corpus_forge.vlm.mistral import MistralOCR

        with patch.dict(os.environ, {"MY_KEY": "sk-other"}, clear=False):
            cfg = _build_config({"backend": "mistral", "mistral_api_key_env": "MY_KEY"})
            v = get_active_vlm(cfg)
        assert isinstance(v, MistralOCR)
        assert v.api_key == "sk-other"

    def test_active_backend_is_registered(self):
        """``get_active_vlm`` registers the constructed backend in the
        module-level ``registry`` so callers can retrieve by name."""
        cfg = _build_config({"backend": "ollama"})
        v = get_active_vlm(cfg)
        assert registry.get("ollama") is v
        # Cleanup so other tests don't see stale state.
        registry.clear()

    def test_ollama_module_missing_raises_unavailable(self):
        """If the [ocr] extra isn't installed, ``_load_class`` returns
        ``None`` and the factory raises :class:`VLMUnavailableError`
        with an "install the extra" hint."""
        cfg = _build_config({"backend": "ollama"})
        with (
            patch("corpus_forge.vlm.registry._load_class", return_value=None),
            pytest.raises(VLMUnavailableError, match=r"(?i)ocr|install|import"),
        ):
            get_active_vlm(cfg)

    def test_mistral_module_missing_raises_unavailable(self):
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "sk-test"}, clear=False):
            cfg = _build_config({"backend": "mistral"})
            with (
                patch("corpus_forge.vlm.registry._load_class", return_value=None),
                pytest.raises(VLMUnavailableError, match=r"(?i)ocr|install|import"),
            ):
                get_active_vlm(cfg)

    def test_unknown_backend_raises_unavailable(self):
        """Belt-and-suspenders: even if a non-Literal value sneaks past
        pydantic (attribute assignment after construction, hand-built
        config in tests), the factory raises :class:`VLMUnavailableError`
        rather than silently producing an unusable backend."""
        cfg = _build_config({"backend": "ollama"})
        # Bypass pydantic by mutating the model after construction.
        object.__setattr__(cfg.vlm, "backend", "frobnicator")  # type: ignore[arg-type]
        with pytest.raises(VLMUnavailableError, match=r"(?i)unknown"):
            get_active_vlm(cfg)
        registry.clear()


# ── Module-level public API ─────────────────────────────────────────────


def test_module_exports():
    """The vlm package must export the documented public surface."""
    from corpus_forge import vlm

    for sym in (
        "VLMBackend",
        "VLMRegistry",
        "NoopVLM",
        "VLMError",
        "VLMUnavailableError",
        "VLMTimeoutError",
        "VLMResponseError",
        "get_active_vlm",
        "registry",
    ):
        assert hasattr(vlm, sym), f"corpus_forge.vlm missing: {sym}"


def test_import_does_not_require_requests():
    """Lazy-import discipline — importing :mod:`corpus_forge.vlm` must
    not trigger ``import requests``. (We can't easily un-import a module
    that's already been imported by a sibling test, so the check is
    inverted: ``corpus_forge.vlm`` must not appear in the sys.modules
    dotted-children of ``requests``.)"""
    import sys
    from pathlib import Path

    import corpus_forge.vlm  # noqa: F401  — re-import is fine

    # The package init must not pull in `requests`. We can verify by
    # ensuring the package re-imports cleanly via importlib.reload
    # without `requests` in the module locals. The simplest test that
    # doesn't fight pytest's collection is: package __init__ source must
    # not contain a top-level `import requests`.
    init_mod = sys.modules["corpus_forge.vlm"]
    init_source = init_mod.__file__
    assert init_source is not None
    source = Path(init_source).read_text()
    # The init file must not have a top-level `import requests`.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import requests") or stripped.startswith("from requests"):
            raise AssertionError(
                f"corpus_forge/vlm/__init__.py imports requests at top level: {stripped!r}"
            )


# Pyrefly satisfaction — keep the unused decorator import alive.
_ = runtime_checkable
