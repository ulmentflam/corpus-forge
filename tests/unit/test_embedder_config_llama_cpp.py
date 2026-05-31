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


# ── n_seq_max (follow-up: tune for memory-slot crashes) ──────────────


def test_n_seq_max_default_is_one() -> None:
    """``n_seq_max`` defaults to 1 so each chunk gets the full ``n_ctx`` window.

    Root cause for the default choice: llama-cpp-python clamps the
    per-sequence context as ``n_ctx_seq = n_ctx / n_seq_max``. The
    embedding-mode initialiser silently sets
    ``n_seq_max = min(n_batch, llama_max_parallel_sequences())`` which
    can be 256 on a stock install — squeezing ``n_ctx_seq`` down to ~256
    even when the user configured ``n_ctx = 8192``. Default of 1 means
    we explicitly tell the binding "single-sequence per call" so the
    full ``n_ctx`` window is available for every chunk.
    """
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.n_seq_max == 1


def test_n_seq_max_round_trips() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        n_seq_max=4,
    )
    assert cfg.n_seq_max == 4


def test_n_seq_max_must_be_positive() -> None:
    """``n_seq_max <= 0`` is rejected — divide-by-zero on ``n_ctx_seq``."""
    from corpus_forge.config import EmbedderConfig

    with pytest.raises(ValidationError):
        EmbedderConfig(
            name="qwen3-llama-cpp",
            provider="llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
            n_seq_max=0,
        )


# ── n_batch + n_ubatch (forward to llama_cpp.Llama via context_params) ─


def test_n_batch_default_is_none() -> None:
    """``n_batch=None`` means "default to ``n_ctx`` at construction time".

    Sentinel pattern so the embedder constructor can compute the
    effective default from the configured ``n_ctx``. Forwarding a
    hard-coded number would break the relationship (e.g. the user
    bumps ``n_ctx`` to 8192 but ``n_batch`` still rounds down to 512).
    """
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.n_batch is None


def test_n_batch_round_trips() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        n_batch=4096,
    )
    assert cfg.n_batch == 4096


def test_n_batch_must_be_positive_when_set() -> None:
    from corpus_forge.config import EmbedderConfig

    with pytest.raises(ValidationError):
        EmbedderConfig(
            name="qwen3-llama-cpp",
            provider="llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
            n_batch=0,
        )


def test_n_ubatch_default_is_none() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
    )
    assert cfg.n_ubatch is None


def test_n_ubatch_round_trips() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="qwen3-llama-cpp",
        provider="llama-cpp",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        n_ubatch=4096,
    )
    assert cfg.n_ubatch == 4096


def test_n_ubatch_must_be_positive_when_set() -> None:
    from corpus_forge.config import EmbedderConfig

    with pytest.raises(ValidationError):
        EmbedderConfig(
            name="qwen3-llama-cpp",
            provider="llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
            n_ubatch=0,
        )
