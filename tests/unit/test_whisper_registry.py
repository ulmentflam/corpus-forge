"""Phase G (G-01) — `WhisperBackend` Protocol + `WhisperRegistry`.

Mirrors :mod:`tests.unit.test_vlm_registry`. The Whisper layer is the
audio-transcription plug-in surface for Phase G's AudioExtractor and
VideoExtractor.

No live network here. The local + remote backends have dedicated unit
tests with fully-mocked ``faster_whisper`` / ``requests``.
"""

from __future__ import annotations

import os
from typing import runtime_checkable
from unittest.mock import patch

import pytest

from corpus_forge.whisper import (
    NoopWhisper,
    WhisperBackend,
    WhisperError,
    WhisperRegistry,
    WhisperResponseError,
    WhisperTimeoutError,
    WhisperUnavailableError,
    get_active_whisper,
    registry,
)

# ── Protocol surface ────────────────────────────────────────────────────


def test_whisper_backend_is_runtime_checkable() -> None:
    assert getattr(WhisperBackend, "_is_runtime_protocol", False) is True


def test_whisper_backend_protocol_isinstance_stub() -> None:
    class _Stub:
        name = "stub"

        def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
            return "stub"

        def warmup(self) -> None:
            return None

    assert isinstance(_Stub(), WhisperBackend)


def test_whisper_backend_protocol_rejects_missing_methods() -> None:
    class _Incomplete:
        name = "incomplete"

        def warmup(self) -> None:
            return None

    assert not isinstance(_Incomplete(), WhisperBackend)


# ── Exception hierarchy ─────────────────────────────────────────────────


class TestExceptionHierarchy:
    def test_unavailable_is_whisper_error(self) -> None:
        assert issubclass(WhisperUnavailableError, WhisperError)

    def test_timeout_is_whisper_error(self) -> None:
        assert issubclass(WhisperTimeoutError, WhisperError)

    def test_response_is_whisper_error(self) -> None:
        assert issubclass(WhisperResponseError, WhisperError)

    def test_whisper_error_is_exception(self) -> None:
        assert issubclass(WhisperError, Exception)

    def test_distinct_classes(self) -> None:
        assert WhisperUnavailableError is not WhisperTimeoutError
        assert WhisperTimeoutError is not WhisperResponseError
        assert WhisperResponseError is not WhisperUnavailableError


# ── NoopWhisper ─────────────────────────────────────────────────────────


class TestNoopWhisper:
    def test_name_is_none(self) -> None:
        assert NoopWhisper().name == "none"

    def test_satisfies_protocol(self) -> None:
        assert isinstance(NoopWhisper(), WhisperBackend)

    def test_transcribe_raises(self) -> None:
        with pytest.raises(WhisperUnavailableError):
            NoopWhisper().transcribe(b"\x00")

    def test_transcribe_with_language_raises(self) -> None:
        with pytest.raises(WhisperUnavailableError):
            NoopWhisper().transcribe(b"\x00", language="en")

    def test_warmup_raises(self) -> None:
        with pytest.raises(WhisperUnavailableError):
            NoopWhisper().warmup()


# ── WhisperRegistry ─────────────────────────────────────────────────────


def _stub_backend(name: str = "stub") -> WhisperBackend:
    class _Stub:
        def __init__(self, nm: str) -> None:
            self.name = nm

        def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
            return f"{self.name}:transcribe"

        def warmup(self) -> None:
            return None

    return _Stub(name)


class TestWhisperRegistry:
    def test_empty_get_returns_none(self) -> None:
        r = WhisperRegistry()
        assert r.get("missing") is None

    def test_register_then_get(self) -> None:
        r = WhisperRegistry()
        b = _stub_backend("local")
        r.register(b)
        assert r.get("local") is b

    def test_register_last_write_wins(self) -> None:
        r = WhisperRegistry()
        first = _stub_backend("local")
        second = _stub_backend("local")
        r.register(first)
        r.register(second)
        assert r.get("local") is second

    def test_list_names(self) -> None:
        r = WhisperRegistry()
        r.register(_stub_backend("local"))
        r.register(_stub_backend("remote"))
        assert sorted(r.list_names()) == ["local", "remote"]

    def test_clear(self) -> None:
        r = WhisperRegistry()
        r.register(_stub_backend("local"))
        r.clear()
        assert r.get("local") is None
        assert r.list_names() == []

    def test_module_singleton_exists(self) -> None:
        assert isinstance(registry, WhisperRegistry)


# ── get_active_whisper (factory off Config.whisper) ─────────────────────


def _build_config(whisper_kwargs: dict | None = None):
    from corpus_forge.config import (
        BackendConfig,
        Config,
        DaemonConfig,
        DatasetConfig,
        DatasetSourceConfig,
        EmbedderConfig,
        WhisperConfig,
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
        whisper=WhisperConfig(**(whisper_kwargs or {})),
    )


class TestGetActiveWhisper:
    def teardown_method(self) -> None:
        registry.clear()

    def test_default_config_returns_noop(self) -> None:
        v = get_active_whisper(_build_config())
        assert isinstance(v, NoopWhisper)

    def test_noop_transcribe_raises(self) -> None:
        v = get_active_whisper(_build_config())
        with pytest.raises(WhisperUnavailableError):
            v.transcribe(b"\x00")

    def test_local_backend(self) -> None:
        from corpus_forge.whisper.local import LocalWhisper

        cfg = _build_config({"backend": "local", "model": "tiny"})
        v = get_active_whisper(cfg)
        assert isinstance(v, LocalWhisper)
        assert v.model == "tiny"

    def test_local_backend_passes_compute_type(self) -> None:
        from corpus_forge.whisper.local import LocalWhisper

        cfg = _build_config({"backend": "local", "local_compute_type": "int8"})
        v = get_active_whisper(cfg)
        assert isinstance(v, LocalWhisper)
        assert v.compute_type == "int8"

    def test_remote_backend(self) -> None:
        from corpus_forge.whisper.remote import RemoteWhisper

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            cfg = _build_config({"backend": "remote", "model": "whisper-1"})
            v = get_active_whisper(cfg)
        assert isinstance(v, RemoteWhisper)
        assert v.api_key == "sk-test"
        assert v.model == "whisper-1"

    def test_remote_passes_base_url_and_timeout(self) -> None:
        from corpus_forge.whisper.remote import RemoteWhisper

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            cfg = _build_config(
                {
                    "backend": "remote",
                    "remote_base_url": "https://api.groq.com/openai/v1",
                    "timeout_s": 120.0,
                }
            )
            v = get_active_whisper(cfg)
        assert isinstance(v, RemoteWhisper)
        assert v.base_url == "https://api.groq.com/openai/v1"
        assert v.timeout_s == 120.0

    def test_remote_without_key_raises_unavailable(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            cfg = _build_config({"backend": "remote"})
            with pytest.raises(WhisperUnavailableError):
                get_active_whisper(cfg)

    def test_remote_custom_env_var_name(self) -> None:
        from corpus_forge.whisper.remote import RemoteWhisper

        with patch.dict(os.environ, {"GROQ_API_KEY": "sk-groq"}, clear=False):
            cfg = _build_config({"backend": "remote", "remote_api_key_env": "GROQ_API_KEY"})
            v = get_active_whisper(cfg)
        assert isinstance(v, RemoteWhisper)
        assert v.api_key == "sk-groq"

    def test_active_backend_is_registered(self) -> None:
        from corpus_forge.whisper.local import LocalWhisper

        cfg = _build_config({"backend": "local"})
        v = get_active_whisper(cfg)
        assert isinstance(v, LocalWhisper)
        assert registry.get("local") is v

    def test_local_module_missing_raises_unavailable(self) -> None:
        cfg = _build_config({"backend": "local"})
        with (
            patch("corpus_forge.whisper.registry._load_class", return_value=None),
            pytest.raises(WhisperUnavailableError, match=r"(?i)whisper|install|import"),
        ):
            get_active_whisper(cfg)

    def test_remote_module_missing_raises_unavailable(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            cfg = _build_config({"backend": "remote"})
            with (
                patch("corpus_forge.whisper.registry._load_class", return_value=None),
                pytest.raises(WhisperUnavailableError, match=r"(?i)whisper|install|import"),
            ):
                get_active_whisper(cfg)

    def test_unknown_backend_raises_unavailable(self) -> None:
        cfg = _build_config({"backend": "local"})
        object.__setattr__(cfg.whisper, "backend", "frobnicator")  # type: ignore[arg-type]
        with pytest.raises(WhisperUnavailableError, match=r"(?i)unknown"):
            get_active_whisper(cfg)


# ── Module-level public API ─────────────────────────────────────────────


def test_module_exports() -> None:
    from corpus_forge import whisper

    for sym in (
        "WhisperBackend",
        "WhisperRegistry",
        "NoopWhisper",
        "WhisperError",
        "WhisperUnavailableError",
        "WhisperTimeoutError",
        "WhisperResponseError",
        "get_active_whisper",
        "registry",
    ):
        assert hasattr(whisper, sym), f"corpus_forge.whisper missing: {sym}"


def test_import_does_not_require_requests() -> None:
    """Lazy-import discipline — importing :mod:`corpus_forge.whisper` must
    not trigger ``import requests`` or ``import faster_whisper``."""
    import sys
    from pathlib import Path

    import corpus_forge.whisper  # noqa: F401

    init_mod = sys.modules["corpus_forge.whisper"]
    assert init_mod.__file__ is not None
    source = Path(init_mod.__file__).read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("import requests")
            or stripped.startswith("from requests")
            or stripped.startswith("import faster_whisper")
            or stripped.startswith("from faster_whisper")
        ):
            raise AssertionError(
                f"corpus_forge/whisper/__init__.py imports a heavy dep at top level: {stripped!r}"
            )


_ = runtime_checkable
