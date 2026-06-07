"""Embedder registry for corpus-forge."""

from typing import TYPE_CHECKING, Any

from .base import Embedder

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import TailscaleConfig
from .llama_cpp import LlamaCppEmbedder
from .model2vec import Model2VecEmbedder
from .openai import OpenAIEmbedder
from .sentence_transformers import SentenceTransformersEmbedder


def _per_provider_extras(
    embedder_config, tailscale: "TailscaleConfig | None" = None
) -> dict[str, Any]:
    """Build the provider-specific kwargs dict for ``EmbedderRegistry.register``.

    Different providers accept different optional kwargs:

    - ``sentence_transformers``: ``device`` (auto-resolves
      ``"auto"`` to mps / cuda / cpu via ``resolve_device``); rejects
      ``api_key_env`` and ``base_url`` (the OpenAI SDK args).
    - ``openai``: ``api_key_env`` + optional ``base_url``; rejects
      ``device`` — the HTTP transport has no local-accelerator
      concept and passing it raised ``TypeError`` on every first-run
      ingest against Ollama's OpenAI-compatible endpoint.
    - ``model2vec``: CPU-only static embeddings; same "no device"
      story as openai.
    - ``llama-cpp``: in-process llama.cpp binding. Accepts the
      llama.cpp-specific knobs ``n_ctx`` + ``n_gpu_layers``, and
      optionally ``gguf_path`` when the user wants to bypass Ollama
      auto-discover. Plus the PR #79 follow-up: ``n_seq_max``
      (default 1 → single-sequence-per-call → full ``n_ctx`` window),
      and optional ``n_batch`` / ``n_ubatch`` (default ``None`` →
      embedder resolves to ``n_ctx`` at construction time). Rejects
      ``device`` / ``api_key_env`` / ``base_url`` — the binding is
      in-process, not HTTP, and the Metal/CUDA offload is controlled
      via ``n_gpu_layers`` instead of a ``device`` string.

    Pulled into a single helper so the three call sites
    (``corpus_forge.ingest.get_active_embedders``,
    ``corpus_forge.cli._build_retriever_for_eval``,
    ``corpus_forge.admin.embedder.run_embedder_smoke``) can't drift
    apart again — they were all reimplementing this in subtly
    different ways and ``_build_retriever_for_eval`` in particular
    was forgetting to forward ``base_url`` + ``api_key_env``, which
    broke every dense ``corpus-forge search`` against a local
    Ollama OpenAI-compat endpoint.
    """
    extras: dict[str, Any] = {
        "normalized": getattr(embedder_config, "normalize", True),
        "distance": getattr(embedder_config, "distance", "cosine"),
        "batch_size": getattr(embedder_config, "batch_size", 32),
        # PR #81 routing — every provider accepts the allow-list (forwarded
        # as ``extensions`` to ``BaseEmbedder.__init__``).  Default ``[]``
        # marks the embedder as a catchall; non-empty makes it a specialist
        # over those file suffixes.
        "extensions": list(getattr(embedder_config, "extensions", []) or []),
    }
    provider = getattr(embedder_config, "provider", "")
    if provider == "sentence_transformers":
        extras["device"] = getattr(embedder_config, "device", "auto")
    elif provider == "openai":
        extras["api_key_env"] = getattr(embedder_config, "api_key_env", "OPENAI_API_KEY")
        base_url = getattr(embedder_config, "base_url", None)
        if base_url is not None:
            # RFC fleet-4 — ``base_url`` accepts a ``ts://name[:port]``
            # tailnet endpoint; resolve it to a connectable URL at this
            # consumption point. Non-``ts://`` values (the common case)
            # pass through ``resolve_endpoint`` unchanged with no
            # Tailscale import. ``rstrip('/')`` keeps the OpenAI SDK
            # happy. When ``tailscale`` is None (duck-typed callers /
            # tests) resolution is skipped — a plain ``str()`` cast, the
            # pre-RFC behaviour.
            url_str = str(base_url)
            if tailscale is not None:
                from corpus_forge.net import resolve_endpoint  # noqa: PLC0415

                url_str = resolve_endpoint(
                    url_str,
                    tailscale_enabled=tailscale.enabled,
                    prefer_magicdns=tailscale.prefer_magicdns,
                    default_scheme="http",
                )
            extras["base_url"] = url_str.rstrip("/")
    elif provider == "llama-cpp":
        # Forward llama.cpp-specific knobs to the constructor.
        # ``gguf_path`` is forwarded ONLY when truthy so the
        # LlamaCppEmbedder constructor default (``None``) fires when
        # the user wants Ollama auto-discover via ``model_id``.
        extras["n_ctx"] = getattr(embedder_config, "n_ctx", 512)
        extras["n_gpu_layers"] = getattr(embedder_config, "n_gpu_layers", -1)
        gguf_path = getattr(embedder_config, "gguf_path", None)
        if gguf_path:
            extras["gguf_path"] = gguf_path
        # Follow-up to PR #78: tune the n_seq_max / n_batch / n_ubatch
        # knobs that gate ``n_ctx_seq = n_ctx / n_seq_max`` inside
        # llama-cpp-python. ``n_seq_max`` always forwarded (default 1
        # = single-sequence-per-call, full n_ctx window). The two
        # batch knobs forward only when explicitly set; ``None`` lets
        # the embedder constructor resolve them to ``n_ctx`` so the
        # physical batch buffer stays >= n_ctx by default.
        extras["n_seq_max"] = getattr(embedder_config, "n_seq_max", 1)
        n_batch = getattr(embedder_config, "n_batch", None)
        if n_batch is not None:
            extras["n_batch"] = n_batch
        n_ubatch = getattr(embedder_config, "n_ubatch", None)
        if n_ubatch is not None:
            extras["n_ubatch"] = n_ubatch
    # ``model2vec`` and any future CPU-only / static providers fall
    # through with just the common kwargs.
    return extras


def register_from_config(
    registry: "EmbedderRegistry",
    embedder_config,
    tailscale: "TailscaleConfig | None" = None,
) -> Embedder:
    """Register an embedder using the per-provider kwarg policy.

    Every call site that builds an :class:`Embedder` from a config
    object should route through here so the provider-specific
    kwargs don't drift between ingest, search, and admin paths.

    RFC fleet-4: pass ``tailscale=config.tailscale`` so an OpenAI
    provider's ``ts://name[:port]`` ``base_url`` is resolved to a
    connectable URL at registration time. Omitting it (the default)
    skips resolution — preserves the pre-RFC behaviour for duck-typed
    callers and unit tests.
    """
    return registry.register(
        name=embedder_config.name,
        provider=embedder_config.provider,
        model_id=embedder_config.model_id,
        dimension=embedder_config.dimension,
        **_per_provider_extras(embedder_config, tailscale),
    )


class EmbedderRegistry:
    """Registry for managing embedder instances."""

    def __init__(self):
        self._embedder_classes = {
            "sentence_transformers": SentenceTransformersEmbedder,
            "openai": OpenAIEmbedder,
            # Phase N Wave 3 — static-tier fast embedder (model2vec /
            # potion-code-16M).  Optional ``[fast-tier]`` extra; the
            # provider module is importable without the extra (encode
            # raises ImportError lazily).
            "model2vec": Model2VecEmbedder,
            # In-process llama.cpp embedder for GGUF models — added to
            # unblock qwen3-embedding on Apple Silicon when Ollama's
            # OpenAI-compatible endpoint returns HTTP 500 with
            # ``failed to encode response: json: unsupported value: NaN``
            # for ~30 % of code chunks. Optional ``[llama-cpp]`` extra;
            # same lazy-import policy as ``model2vec`` above.
            "llama-cpp": LlamaCppEmbedder,
        }
        self._instances: dict[str, Embedder] = {}

    def register(
        self, name: str, provider: str, model_id: str, dimension: int, **kwargs
    ) -> Embedder:
        """Register and create an embedder instance.

        If an embedder with the same name already exists, its attributes are
        updated in-place and the same object reference is returned.
        """
        if provider not in self._embedder_classes:
            raise ValueError(f"Unknown embedder provider: {provider}")

        if name in self._instances:
            existing = self._instances[name]
            existing.provider = provider
            existing.model_id = model_id
            existing.dimension = dimension
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing

        embedder_class = self._embedder_classes[provider]
        embedder = embedder_class(name=name, model_id=model_id, dimension=dimension, **kwargs)

        self._instances[name] = embedder
        return embedder

    def get(self, name: str) -> Embedder | None:
        """Get an embedder instance by name."""
        return self._instances.get(name)

    def list_names(self) -> list[str]:
        """List all registered embedder names."""
        return list(self._instances.keys())

    def clear(self):
        """Clear all registered embedders."""
        self._instances.clear()


# Global registry instance
registry = EmbedderRegistry()
