"""Unit tests for the code-enrichment protocol + registry + factory.

Phase H / H-01.

Covers:

- :class:`CodeChunkEnrichment` dataclass invariants (confidence range,
  ``to_metadata`` round-trip, frozen semantics).
- :class:`CodeEnricher` Protocol is runtime-checkable.
- :class:`NoopEnricher` raises :class:`EnricherUnavailableError` on
  every operational call.
- :class:`EnricherRegistry` last-write-wins on name collisions; ordered
  ``names()``; ``get(name)`` returns ``None`` for misses.
- :func:`_parse_enrichment_response` graceful-fallback semantics:
  malformed JSON, non-dict, missing keys, out-of-range confidence,
  null docstring round-trip.
- :func:`get_active_enricher` factory: ``"none"`` → NoopEnricher;
  ``"local"`` constructs ``QwenCoderLocal`` with config fields;
  ``"remote"`` constructs ``QwenCoderRemote`` with the API shape.
"""

from __future__ import annotations

import pytest

from corpus_forge.chunkers.base import TextChunk
from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
    EnricherConfig,
)
from corpus_forge.enrichers import get_active_enricher
from corpus_forge.enrichers.base import (
    CodeChunkEnrichment,
    CodeEnricher,
    EnricherError,
    EnricherResponseError,
    EnricherTimeoutError,
    EnricherUnavailableError,
    NoopEnricher,
    _parse_enrichment_response,
)
from corpus_forge.enrichers.qwen_local import QwenCoderLocal
from corpus_forge.enrichers.qwen_remote import QwenCoderRemote
from corpus_forge.enrichers.registry import EnricherRegistry

# ---------------------------------------------------------------------------
# Dataclass invariants
# ---------------------------------------------------------------------------


class TestCodeChunkEnrichment:
    def test_round_trip_via_to_metadata(self) -> None:
        e = CodeChunkEnrichment(
            docstring="Synthesised.",
            summary="Adds.",
            symbols=["foo", "bar"],
            model="qwen3.6:35b-a3b-instruct",
            confidence=0.7,
        )
        md = e.to_metadata()
        assert md == {
            "docstring": "Synthesised.",
            "summary": "Adds.",
            "symbols": ["foo", "bar"],
            "model": "qwen3.6:35b-a3b-instruct",
            "confidence": 0.7,
        }

    def test_null_docstring_round_trips_as_none(self) -> None:
        e = CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=0.5)
        assert e.to_metadata()["docstring"] is None

    def test_is_frozen(self) -> None:
        e = CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=0.5)
        with pytest.raises((AttributeError, Exception)):
            e.summary = "other"  # type: ignore[misc]

    def test_rejects_confidence_below_zero(self) -> None:
        with pytest.raises(ValueError):
            CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=-0.1)

    def test_rejects_confidence_above_one(self) -> None:
        with pytest.raises(ValueError):
            CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=1.5)

    def test_boundary_values_accepted(self) -> None:
        CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=0.0)
        CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=1.0)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class TestCodeEnricherProtocol:
    def test_noop_is_a_code_enricher(self) -> None:
        assert isinstance(NoopEnricher(), CodeEnricher)

    def test_qwen_local_is_a_code_enricher(self) -> None:
        assert isinstance(QwenCoderLocal(), CodeEnricher)

    def test_qwen_remote_is_a_code_enricher(self) -> None:
        assert isinstance(QwenCoderRemote(), CodeEnricher)


# ---------------------------------------------------------------------------
# NoopEnricher
# ---------------------------------------------------------------------------


class TestNoopEnricher:
    def test_name_is_noop(self) -> None:
        assert NoopEnricher().name == "noop"

    def test_enrich_raises_unavailable(self) -> None:
        chunk = TextChunk(text="def foo(): pass")
        with pytest.raises(EnricherUnavailableError, match="disabled"):
            NoopEnricher().enrich(chunk, language="python")

    def test_warmup_is_noop(self) -> None:
        # Should not raise.
        NoopEnricher().warmup()


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_unavailable_is_an_enricher_error(self) -> None:
        assert issubclass(EnricherUnavailableError, EnricherError)

    def test_timeout_is_an_enricher_error(self) -> None:
        assert issubclass(EnricherTimeoutError, EnricherError)

    def test_response_is_an_enricher_error(self) -> None:
        assert issubclass(EnricherResponseError, EnricherError)


# ---------------------------------------------------------------------------
# EnricherRegistry
# ---------------------------------------------------------------------------


class _DummyEnricher:
    def __init__(self, name: str) -> None:
        self.name = name

    def enrich(self, chunk: TextChunk, *, language: str) -> CodeChunkEnrichment:
        return CodeChunkEnrichment(
            docstring=None, summary="ok", symbols=[], model="dummy", confidence=0.5
        )

    def warmup(self) -> None:
        return None


class TestEnricherRegistry:
    def test_empty_registry(self) -> None:
        reg = EnricherRegistry()
        assert len(reg) == 0
        assert reg.names() == []
        assert reg.get("missing") is None

    def test_register_appends_in_order(self) -> None:
        reg = EnricherRegistry()
        reg.register(_DummyEnricher("a"))
        reg.register(_DummyEnricher("b"))
        assert reg.names() == ["a", "b"]

    def test_get_returns_registered_enricher(self) -> None:
        reg = EnricherRegistry()
        a = _DummyEnricher("a")
        reg.register(a)
        assert reg.get("a") is a

    def test_register_last_write_wins_on_name(self) -> None:
        reg = EnricherRegistry()
        first = _DummyEnricher("dup")
        second = _DummyEnricher("dup")
        reg.register(first)
        reg.register(second)
        assert reg.names() == ["dup"]
        assert reg.get("dup") is second

    def test_clear(self) -> None:
        reg = EnricherRegistry()
        reg.register(_DummyEnricher("a"))
        reg.clear()
        assert len(reg) == 0

    def test_len(self) -> None:
        reg = EnricherRegistry()
        reg.register(_DummyEnricher("a"))
        reg.register(_DummyEnricher("b"))
        assert len(reg) == 2


# ---------------------------------------------------------------------------
# Inner JSON parser
# ---------------------------------------------------------------------------


class TestParseEnrichmentResponse:
    def test_happy_path_full_payload(self) -> None:
        raw = (
            '{"docstring": "Add two ints.", "summary": "Adds.", '
            '"symbols": ["a", "b"], "confidence": 0.9}'
        )
        e = _parse_enrichment_response(raw, "qwen3.6")
        assert e.docstring == "Add two ints."
        assert e.summary == "Adds."
        assert e.symbols == ["a", "b"]
        assert e.confidence == pytest.approx(0.9)
        assert e.model == "qwen3.6"

    def test_null_docstring_preserved(self) -> None:
        raw = '{"docstring": null, "summary": "s", "symbols": [], "confidence": 0.5}'
        e = _parse_enrichment_response(raw, "m")
        assert e.docstring is None

    def test_confidence_above_one_clamped(self) -> None:
        raw = '{"docstring": null, "summary": "s", "symbols": [], "confidence": 1.5}'
        e = _parse_enrichment_response(raw, "m")
        assert e.confidence == pytest.approx(1.0)

    def test_confidence_below_zero_clamped(self) -> None:
        raw = '{"docstring": null, "summary": "s", "symbols": [], "confidence": -0.2}'
        e = _parse_enrichment_response(raw, "m")
        assert e.confidence == pytest.approx(0.0)

    def test_invalid_json_falls_back(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING", logger="corpus_forge.enrichers.base"):
            e = _parse_enrichment_response("not json at all", "m")
        assert e.summary == "invalid LLM output"
        assert e.confidence == 0.0
        assert e.model == "m"
        assert any("invalid" in rec.message.lower() for rec in caplog.records)

    def test_empty_string_falls_back(self) -> None:
        e = _parse_enrichment_response("", "m")
        assert e.summary == "invalid LLM output"
        assert e.confidence == 0.0

    def test_non_dict_falls_back(self) -> None:
        e = _parse_enrichment_response("[1, 2, 3]", "m")
        assert e.summary == "invalid LLM output"

    def test_missing_symbols_defaults_to_empty_list(self) -> None:
        raw = '{"docstring": null, "summary": "s", "confidence": 0.5}'
        e = _parse_enrichment_response(raw, "m")
        assert e.symbols == []

    def test_non_list_symbols_coerced(self) -> None:
        raw = '{"docstring": null, "summary": "s", "symbols": "foo", "confidence": 0.5}'
        e = _parse_enrichment_response(raw, "m")
        # Coerce — single-element list rather than dropping the signal.
        assert e.symbols == ["foo"]

    def test_non_string_symbols_coerced_to_str(self) -> None:
        raw = '{"docstring": null, "summary": "s", "symbols": [1, 2], "confidence": 0.5}'
        e = _parse_enrichment_response(raw, "m")
        assert e.symbols == ["1", "2"]

    def test_non_string_docstring_coerced(self) -> None:
        raw = '{"docstring": 42, "summary": "s", "symbols": [], "confidence": 0.5}'
        e = _parse_enrichment_response(raw, "m")
        assert e.docstring == "42"

    def test_non_string_summary_coerced(self) -> None:
        raw = '{"docstring": null, "summary": 1, "symbols": [], "confidence": 0.5}'
        e = _parse_enrichment_response(raw, "m")
        assert e.summary == "1"

    def test_non_float_confidence_defaults(self) -> None:
        raw = '{"docstring": null, "summary": "s", "symbols": [], "confidence": "high"}'
        e = _parse_enrichment_response(raw, "m")
        assert e.confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# get_active_enricher factory
# ---------------------------------------------------------------------------


def _build_config(**kwargs) -> Config:
    """Build a minimum-viable Config with an EnricherConfig override."""
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
        code_enricher=EnricherConfig(**kwargs),
    )


class TestGetActiveEnricher:
    def test_none_returns_noop(self) -> None:
        cfg = _build_config(backend="none")
        e = get_active_enricher(cfg)
        assert isinstance(e, NoopEnricher)
        assert e.name == "noop"

    def test_local_returns_qwen_coder_local(self) -> None:
        cfg = _build_config(
            backend="local",
            local_model="qwen3.6:35b-a3b-instruct",
            local_url="http://localhost:11434",
        )
        e = get_active_enricher(cfg)
        assert isinstance(e, QwenCoderLocal)
        assert e.model == "qwen3.6:35b-a3b-instruct"
        assert e.llm_url == "http://localhost:11434"

    def test_remote_ollama_returns_qwen_coder_remote(self) -> None:
        cfg = _build_config(
            backend="remote",
            remote_api_shape="ollama",
            remote_model="qwen3.6:35b-a3b-instruct",
            remote_url="http://remote.example.com:11434",
        )
        e = get_active_enricher(cfg)
        assert isinstance(e, QwenCoderRemote)
        assert e.api_shape == "ollama"
        assert e.base_url == "http://remote.example.com:11434"

    def test_remote_openai_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No env var set → factory raises (constructor enforces it).
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = _build_config(
            backend="remote",
            remote_api_shape="openai",
            remote_url="https://api.openai.com/v1",
            remote_api_key_env="OLLAMA_API_KEY",
        )
        with pytest.raises(EnricherUnavailableError, match="api_key"):
            get_active_enricher(cfg)

    def test_remote_openai_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-test")
        cfg = _build_config(
            backend="remote",
            remote_api_shape="openai",
            remote_url="https://api.openai.com/v1",
            remote_api_key_env="OLLAMA_API_KEY",
        )
        e = get_active_enricher(cfg)
        assert isinstance(e, QwenCoderRemote)
        assert e.api_shape == "openai"
        assert e.api_key == "sk-test"
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    def test_default_backend_is_none(self) -> None:
        cfg = _build_config()
        # Default for EnricherConfig.backend is "none".
        assert cfg.code_enricher.backend == "none"
        assert isinstance(get_active_enricher(cfg), NoopEnricher)

    def test_resolve_code_enricher_api_key_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "value-123")
        cfg = _build_config(remote_api_key_env="MY_KEY")
        assert cfg.resolve_code_enricher_api_key() == "value-123"

    def test_resolve_code_enricher_api_key_missing_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NEVER_SET_KEY", raising=False)
        cfg = _build_config(remote_api_key_env="NEVER_SET_KEY")
        assert cfg.resolve_code_enricher_api_key() is None
