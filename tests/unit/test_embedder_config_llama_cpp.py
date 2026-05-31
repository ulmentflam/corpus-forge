"""Pin ``EmbedderConfig``'s acceptance of ``provider="llama-cpp"`` + new fields.

The Pydantic schema in ``corpus_forge.config.EmbedderConfig`` gates
the provider via a regex.  Adding ``llama-cpp`` (with the dash) needs
its own pin so a regex regression surfaces immediately — and so the
three new optional fields (``gguf_path`` / ``n_ctx`` / ``n_gpu_layers``)
round-trip through the model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_provider_llama_cpp_accepted() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.provider == "llama-cpp"


def test_provider_bogus_still_rejected() -> None:
    """Other unknown providers must still be rejected by the regex."""
    from corpus_forge.config import EmbedderConfig

    with pytest.raises(ValidationError):
        EmbedderConfig(
            name="bad",
            provider="bogus-provider",
            model_id="x",
            dimension=128,
        )


def test_gguf_path_field_accepted() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        gguf_path="/tmp/x.gguf",
    )
    assert cfg.gguf_path == "/tmp/x.gguf"


def test_gguf_path_default_none() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.gguf_path is None


def test_n_ctx_field_accepted() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        n_ctx=2048,
    )
    assert cfg.n_ctx == 2048


def test_n_ctx_default_512() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.n_ctx == 512


def test_n_gpu_layers_field_accepted() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        n_gpu_layers=0,
    )
    assert cfg.n_gpu_layers == 0


def test_n_gpu_layers_default_minus_one() -> None:
    """-1 = all layers offloaded (Metal on Apple Silicon, CUDA on Linux)."""
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.n_gpu_layers == -1
