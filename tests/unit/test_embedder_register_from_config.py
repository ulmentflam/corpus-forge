"""Regression coverage for the shared ``register_from_config`` helper.

The helper consolidates the per-provider kwarg gating that every
call site (ingest, search-via-cli, admin smoke-test) needs when
turning a Pydantic ``EmbedderConfig`` into a live ``Embedder``.
Drift between the three was responsible for two real production
crashes:

1. ``TypeError: OpenAIEmbedder.__init__() got an unexpected keyword
   argument 'device'`` on every first-run ingest against an
   Ollama-backed OpenAI-compatible endpoint.
2. ``ValueError: API key not found in environment variable
   OPENAI_API_KEY`` on every dense ``corpus-forge search`` against
   the same local Ollama endpoint — the search path was forgetting
   to forward ``base_url`` to the OpenAI client, which made it look
   like the embedder was talking to api.openai.com.

These tests pin the contract at the helper level so every future
call site that routes through it inherits the right policy by
construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from corpus_forge.embedders.openai import OpenAIEmbedder
from corpus_forge.embedders.registry import (
    EmbedderRegistry,
    _per_provider_extras,
    register_from_config,
)
from corpus_forge.embedders.sentence_transformers import SentenceTransformersEmbedder


def _cfg(provider: str, **overrides):
    """Build a minimal MagicMock that mirrors EmbedderConfig's surface."""
    cfg = MagicMock()
    cfg.name = overrides.pop("name", "test")
    cfg.provider = provider
    cfg.model_id = overrides.pop("model_id", "some-model")
    cfg.dimension = overrides.pop("dimension", 768)
    cfg.normalize = overrides.pop("normalize", True)
    cfg.distance = overrides.pop("distance", "cosine")
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── _per_provider_extras ───────────────────────────────────────────────


class TestPerProviderExtras:
    def test_sentence_transformers_gets_device_no_api_key(self) -> None:
        extras = _per_provider_extras(_cfg("sentence_transformers", device="cpu"))
        assert extras["device"] == "cpu"
        assert "api_key_env" not in extras
        assert "base_url" not in extras

    def test_sentence_transformers_defaults_device_to_auto(self) -> None:
        # The MagicMock's auto-generated ``device`` attribute would be
        # a child MagicMock — wipe it so the ``getattr(..., "auto")``
        # default kicks in.
        cfg = _cfg("sentence_transformers")
        del cfg.device
        extras = _per_provider_extras(cfg)
        assert extras["device"] == "auto"

    def test_openai_gets_api_key_env_no_device(self) -> None:
        extras = _per_provider_extras(_cfg("openai", api_key_env="MY_KEY"))
        assert extras["api_key_env"] == "MY_KEY"
        assert "device" not in extras, (
            "OpenAIEmbedder does not accept 'device' — forwarding it "
            "raised TypeError on every first-run ingest"
        )

    def test_openai_defaults_api_key_env_to_openai_api_key(self) -> None:
        cfg = _cfg("openai")
        del cfg.api_key_env
        extras = _per_provider_extras(cfg)
        assert extras["api_key_env"] == "OPENAI_API_KEY"

    def test_openai_forwards_base_url_when_set(self) -> None:
        """The bug that wedged ``corpus-forge search`` — base_url MUST
        round-trip from config to the OpenAI client. If it doesn't,
        the client falls back to api.openai.com and crashes with
        'API key not found in environment variable OPENAI_API_KEY'
        the moment a local-substitution config lands.
        """
        extras = _per_provider_extras(_cfg("openai", base_url="http://localhost:11434/v1"))
        assert extras["base_url"] == "http://localhost:11434/v1"

    def test_openai_omits_base_url_when_none(self) -> None:
        extras = _per_provider_extras(_cfg("openai", base_url=None))
        assert "base_url" not in extras, (
            "An explicit base_url=None must NOT be forwarded — "
            "passing it would override the OpenAI SDK's own default."
        )

    def test_openai_strips_trailing_slash_on_base_url(self) -> None:
        """pydantic's ``AnyHttpUrl`` appends a trailing slash to bare
        hosts and that breaks the OpenAI SDK's path construction.
        """
        extras = _per_provider_extras(_cfg("openai", base_url="http://localhost:11434/v1/"))
        assert extras["base_url"] == "http://localhost:11434/v1"

    def test_model2vec_omits_both_device_and_api_key(self) -> None:
        extras = _per_provider_extras(_cfg("model2vec"))
        assert "device" not in extras
        assert "api_key_env" not in extras
        assert "base_url" not in extras

    def test_common_kwargs_present_for_every_provider(self) -> None:
        common = {"normalized", "distance", "batch_size"}
        for provider in ("sentence_transformers", "openai", "model2vec", "llama-cpp"):
            extras = _per_provider_extras(_cfg(provider))
            assert common.issubset(extras.keys()), (
                f"{provider!r} dropped a common kwarg: missing {common - extras.keys()}"
            )

    # ── llama-cpp ────────────────────────────────────────────────────

    def test_llama_cpp_gets_n_ctx_and_n_gpu_layers(self) -> None:
        """The llama-cpp provider needs its own kwargs (n_ctx, n_gpu_layers).

        These are llama.cpp-specific — context window + Metal/CUDA
        offload layer count — and don't apply to any other backend.
        """
        extras = _per_provider_extras(_cfg("llama-cpp", n_ctx=2048, n_gpu_layers=0))
        assert extras["n_ctx"] == 2048
        assert extras["n_gpu_layers"] == 0

    def test_llama_cpp_omits_device_and_openai_kwargs(self) -> None:
        """llama-cpp is in-process — no device flag, no API key, no base URL."""
        extras = _per_provider_extras(_cfg("llama-cpp"))
        assert "device" not in extras, (
            "LlamaCppEmbedder.__init__ does not accept 'device' — forwarding "
            "it would raise TypeError on every first-run ingest"
        )
        assert "api_key_env" not in extras
        assert "base_url" not in extras

    def test_llama_cpp_forwards_gguf_path_when_set(self) -> None:
        extras = _per_provider_extras(_cfg("llama-cpp", gguf_path="/tmp/x.gguf"))
        assert extras["gguf_path"] == "/tmp/x.gguf"

    def test_llama_cpp_omits_gguf_path_when_none(self) -> None:
        """Passing ``gguf_path=None`` MUST NOT forward — otherwise the
        embedder's ``gguf_path=None`` default would be shadowed and
        the registry could not reuse the same constructor signature.

        With ``gguf_path`` left out of the kwargs, the LlamaCppEmbedder
        default (``None``) fires and the resolver falls back to Ollama
        auto-discover via ``model_id``.
        """
        extras = _per_provider_extras(_cfg("llama-cpp", gguf_path=None))
        assert "gguf_path" not in extras


class TestRegisterFromConfigLlamaCpp:
    """End-to-end: a ``provider="llama-cpp"`` config wires up a real
    ``LlamaCppEmbedder`` whose ``gguf_path`` / ``n_ctx`` / ``n_gpu_layers``
    round-trip from the config.
    """

    def test_llama_cpp_embedder_round_trips(self) -> None:
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        reg = EmbedderRegistry()
        cfg = _cfg(
            "llama-cpp",
            name="qwen3-llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
            gguf_path="/tmp/qwen3-embedding-8b-Q8_0.gguf",
            n_ctx=1024,
            n_gpu_layers=20,
        )
        embedder = register_from_config(reg, cfg)
        assert isinstance(embedder, LlamaCppEmbedder)
        assert embedder.gguf_path == "/tmp/qwen3-embedding-8b-Q8_0.gguf"
        assert embedder.n_ctx == 1024
        assert embedder.n_gpu_layers == 20
        # And the common-kwarg slots round-trip as for every other provider.
        assert embedder.dimension == 4096
        assert embedder.provider == "llama-cpp"

    def test_llama_cpp_embedder_constructed_without_device(self) -> None:
        """``LlamaCppEmbedder.__init__`` does not accept ``device``."""
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        reg = EmbedderRegistry()
        cfg = _cfg(
            "llama-cpp",
            name="ll-2",
            model_id="qwen3-embedding:8b",
            dimension=4096,
            device="auto",  # set on config — must be filtered out
        )
        embedder = register_from_config(reg, cfg)
        assert isinstance(embedder, LlamaCppEmbedder)
        assert not hasattr(embedder, "device") or embedder.__dict__.get("device") is None


# ── register_from_config — end-to-end with a real EmbedderRegistry ────


class TestRegisterFromConfig:
    def test_openai_embedder_receives_base_url_through_helper(self) -> None:
        """End-to-end: a config that names a local base_url must
        produce an OpenAIEmbedder whose ``.base_url`` is set so
        ``_get_client`` takes the local-no-auth path instead of
        raising ``ValueError: API key not found``.
        """
        reg = EmbedderRegistry()
        cfg = _cfg(
            "openai",
            name="local-ollama",
            model_id="qwen3-embedding:8b",
            dimension=2000,
            base_url="http://localhost:11434/v1",
        )
        # MagicMock auto-creates ``api_key_env`` as a child mock; the
        # getattr-default branch in ``_per_provider_extras`` only kicks
        # in when the attribute is genuinely absent.
        del cfg.api_key_env
        embedder = register_from_config(reg, cfg)
        assert isinstance(embedder, OpenAIEmbedder)
        assert embedder.base_url == "http://localhost:11434/v1"
        # And the default api_key_env must be set so ``_get_client``
        # can read it from os.environ when the user does export a key.
        assert embedder.api_key_env == "OPENAI_API_KEY"

    def test_openai_embedder_constructed_without_device(self) -> None:
        """``OpenAIEmbedder.__init__`` does not accept ``device``.
        ``register_from_config`` MUST not pass it. Direct test of
        the failure mode (a TypeError would propagate).
        """
        reg = EmbedderRegistry()
        cfg = _cfg(
            "openai",
            name="oai-2",
            model_id="text-embedding-3-small",
            dimension=1536,
            device="auto",  # set on the config but must be filtered out
        )
        embedder = register_from_config(reg, cfg)
        assert isinstance(embedder, OpenAIEmbedder)
        # Sanity: no device attribute leaked onto the embedder.
        assert not hasattr(embedder, "device") or embedder.__dict__.get("device") is None

    def test_sentence_transformers_embedder_receives_device(self) -> None:
        reg = EmbedderRegistry()
        cfg = _cfg(
            "sentence_transformers",
            name="bge",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
            device="cpu",
            api_key_env="WONT_BE_FORWARDED",
        )
        embedder = register_from_config(reg, cfg)
        assert isinstance(embedder, SentenceTransformersEmbedder)
        assert embedder.device == "cpu"
        # And ``api_key_env`` must NOT have been forwarded — sentence
        # transformers' constructor would TypeError on it.
        assert (
            not hasattr(embedder, "api_key_env")
            or embedder.__dict__.get("api_key_env") != "WONT_BE_FORWARDED"
        )


# ── Call-site parity: every config-to-registry helper uses the shared one ─


class TestCallSiteRouting:
    """Each of the three documented call sites must route through
    ``register_from_config`` so the per-provider policy can't drift
    again. These tests inspect the source rather than trying to
    reproduce every call-site's surrounding setup.
    """

    def test_ingest_get_active_embedders_calls_register_from_config(self) -> None:
        import inspect

        from corpus_forge.ingest import get_active_embedders

        src = inspect.getsource(get_active_embedders)
        assert "register_from_config" in src, (
            "get_active_embedders no longer routes through "
            "register_from_config — the per-provider kwarg gating "
            "would drift from the search and admin paths."
        )

    def test_cli_build_retriever_calls_register_from_config(self) -> None:
        import inspect

        from corpus_forge.cli import _build_retriever_for_eval

        src = inspect.getsource(_build_retriever_for_eval)
        assert "register_from_config" in src, (
            "_build_retriever_for_eval no longer routes through "
            "register_from_config — search will start crashing on "
            "local Ollama OpenAI-compat endpoints again "
            "(ValueError: API key not found in environment variable "
            "OPENAI_API_KEY)."
        )

    def test_admin_smoke_calls_register_from_config(self) -> None:
        import inspect

        from corpus_forge.admin.embedder import run_embedder_smoke

        src = inspect.getsource(run_embedder_smoke)
        assert "register_from_config" in src, (
            "run_embedder_smoke no longer routes through "
            "register_from_config — ``corpus-forge embedder test`` "
            "will crash with the same kwarg-mismatch bugs we hit "
            "on ingest and search."
        )

    def test_embed_backfill_calls_register_from_config(self) -> None:
        """The ``corpus-forge embed`` backfill verb is the fourth and
        final ``EmbedderConfig`` → ``Embedder`` site. The original
        registry-refactor commit (b5538db) missed this one and
        ``corpus-forge embed -e <openai-provider>`` crashed with
        ``TypeError: OpenAIEmbedder.__init__() got an unexpected
        keyword argument 'device'`` — exact same bug as the original
        ingest failure (surfaced again by the E2E #2 dim=4096
        round-trip against live Postgres + Ollama).
        """
        import inspect

        from corpus_forge.embed import backfill_embedder

        src = inspect.getsource(backfill_embedder)
        assert "register_from_config" in src, (
            "corpus_forge.embed.backfill_embedder no longer routes "
            "through register_from_config — "
            "`corpus-forge embed -e <openai-provider>` will crash "
            "with a 'device kwarg' TypeError again."
        )
