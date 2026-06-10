"""Unit tests for the in-process llama-cpp EOS/SEP terminator append
(RFC embedder-eos item 2 — the transport half).

Three layers:

- ``_EosAppendingTokenizer`` — the tokenizer wrapper that terminates every
  tokenization with the model's EOS token id (append, idempotent, empty,
  detokenize pass-through).
- ``LlamaCppEmbedder`` — the ``append_eos`` constructor field and
  ``_install_eos_tokenizer`` wiring (installed only when requested, no-op
  when the model has no EOS, swapped onto the live handle so every
  ``create_embedding`` call inherits it).
- ``_per_provider_extras`` — the registry resolves the three-state
  ``append_eos`` (explicit > known-model registry > False) to a bool for
  the llama-cpp provider.

Why the token layer and not a surface-string append: the Qwen2 EOS
(``<|im_end|>``) used by ``nomic-embed-code`` has no surface form and
``Llama.embed`` tokenizes with ``special=False``, so a string append can
never reproduce the special token. These tests pin the token-layer
contract directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.embedders.llama_cpp import (
    LlamaCppEmbedder,
    _EosAppendingTokenizer,
)


class _FakeBaseTokenizer:
    """Stand-in for ``llama_cpp.llama_tokenizer.LlamaTokenizer`` — records
    the args it was called with and returns a fixed token list so the
    wrapper's append behaviour is observable without the extra installed."""

    def __init__(self, tokens: list[int]):
        self._tokens = tokens
        self.tokenize_calls: list[tuple] = []
        self.detokenize_calls: list[tuple] = []

    def tokenize(self, text: bytes, add_bos: bool = True, special: bool = False) -> list[int]:
        self.tokenize_calls.append((text, add_bos, special))
        return list(self._tokens)

    def detokenize(self, tokens, prev_tokens=None, special=False) -> bytes:
        self.detokenize_calls.append((tokens, prev_tokens, special))
        return b"detok"


EOS = 151645


class TestEosAppendingTokenizer:
    """The append/idempotency/pass-through contract of the wrapper."""

    def test_appends_eos_when_absent(self) -> None:
        wrap = _EosAppendingTokenizer(_FakeBaseTokenizer([1, 2, 3]), EOS)
        assert wrap.tokenize(b"hello") == [1, 2, 3, EOS]

    def test_idempotent_when_already_terminated(self) -> None:
        # A GGUF that DOES set add_eos_token already ends with EOS; the
        # wrapper must not double it.
        wrap = _EosAppendingTokenizer(_FakeBaseTokenizer([1, 2, EOS]), EOS)
        assert wrap.tokenize(b"hello") == [1, 2, EOS]

    def test_empty_tokenization_becomes_just_eos(self) -> None:
        wrap = _EosAppendingTokenizer(_FakeBaseTokenizer([]), EOS)
        assert wrap.tokenize(b"") == [EOS]

    def test_forwards_add_bos_and_special(self) -> None:
        base = _FakeBaseTokenizer([7])
        wrap = _EosAppendingTokenizer(base, EOS)
        wrap.tokenize(b"x", add_bos=False, special=True)
        assert base.tokenize_calls == [(b"x", False, True)]

    def test_detokenize_passes_through(self) -> None:
        base = _FakeBaseTokenizer([1])
        wrap = _EosAppendingTokenizer(base, EOS)
        assert wrap.detokenize([1, 2], special=True) == b"detok"
        assert base.detokenize_calls == [([1, 2], None, True)]

    def test_returns_new_list_not_base_internal(self) -> None:
        # Mutating the wrapper's output must not corrupt the base's tokens.
        base = _FakeBaseTokenizer([1, 2])
        wrap = _EosAppendingTokenizer(base, EOS)
        out = wrap.tokenize(b"x")
        out.append(999)
        assert base.tokenize(b"x") == [1, 2]


class TestAppendEosField:
    """The ``append_eos`` constructor field."""

    def _make(self, **kw) -> LlamaCppEmbedder:
        defaults = {"name": "e", "model_id": "nomic-embed-code:latest", "dimension": 3584}
        defaults.update(kw)
        return LlamaCppEmbedder(**defaults)

    def test_defaults_false(self) -> None:
        assert self._make().append_eos is False

    def test_round_trips_true(self) -> None:
        assert self._make(append_eos=True).append_eos is True


class TestInstallEosTokenizer:
    """``_install_eos_tokenizer`` wiring onto a (faked) llama handle."""

    def _embedder(self, append_eos: bool) -> LlamaCppEmbedder:
        return LlamaCppEmbedder(
            name="nomic-code",
            model_id="manutic/nomic-embed-code:latest",
            dimension=3584,
            append_eos=append_eos,
        )

    def _fake_handle(self, eos: int = EOS, *, with_token_eos: bool = True) -> MagicMock:
        handle = MagicMock()
        handle.tokenizer_ = _FakeBaseTokenizer([10, 11, 12])
        if with_token_eos:
            handle.token_eos.return_value = eos
        else:
            del handle.token_eos
        return handle

    def test_load_model_installs_wrapper_when_enabled(self) -> None:
        emb = self._embedder(append_eos=True)
        handle = self._fake_handle()
        with patch("corpus_forge.embedders.llama_cpp._load_llama_handle", return_value=handle):
            emb._load_model()
        assert isinstance(handle.tokenizer_, _EosAppendingTokenizer)
        # Every embed input now carries the EOS as its final token.
        assert handle.tokenizer_.tokenize(b"def f(): ...")[-1] == EOS

    def test_load_model_skips_wrapper_when_disabled(self) -> None:
        emb = self._embedder(append_eos=False)
        handle = self._fake_handle()
        with patch("corpus_forge.embedders.llama_cpp._load_llama_handle", return_value=handle):
            emb._load_model()
        assert isinstance(handle.tokenizer_, _FakeBaseTokenizer)

    def test_noop_when_model_reports_no_eos(self) -> None:
        emb = self._embedder(append_eos=True)
        handle = self._fake_handle(eos=-1)
        with patch("corpus_forge.embedders.llama_cpp._load_llama_handle", return_value=handle):
            emb._load_model()
        # -1 EOS → nothing safe to append; leave the base tokenizer intact.
        assert isinstance(handle.tokenizer_, _FakeBaseTokenizer)

    def test_noop_when_handle_lacks_token_eos(self) -> None:
        emb = self._embedder(append_eos=True)
        handle = self._fake_handle(with_token_eos=False)
        with patch("corpus_forge.embedders.llama_cpp._load_llama_handle", return_value=handle):
            emb._load_model()
        assert isinstance(handle.tokenizer_, _FakeBaseTokenizer)

    def test_install_is_noop_when_handle_none(self) -> None:
        # Defensive: _install_eos_tokenizer called with no loaded handle.
        emb = self._embedder(append_eos=True)
        emb._llama = None
        emb._install_eos_tokenizer()  # must not raise


class TestRegistryResolvesAppendEos:
    """``_per_provider_extras`` resolves append_eos for the llama-cpp provider."""

    def _extras(self, model_id: str, append_eos=None):
        from corpus_forge.config import EmbedderConfig
        from corpus_forge.embedders.registry import _per_provider_extras

        cfg = EmbedderConfig(
            name="x",
            provider="llama-cpp",
            model_id=model_id,
            dimension=3584,
            append_eos=append_eos,
        )
        return _per_provider_extras(cfg)

    def test_nomic_code_resolves_true_from_registry(self) -> None:
        assert self._extras("manutic/nomic-embed-code:latest")["append_eos"] is True

    def test_unknown_model_resolves_false(self) -> None:
        assert self._extras("qwen3-embedding:8b")["append_eos"] is False

    def test_explicit_false_overrides_registry_default(self) -> None:
        extras = self._extras("manutic/nomic-embed-code:latest", append_eos=False)
        assert extras["append_eos"] is False

    def test_duck_typed_config_without_resolver_falls_back_to_raw_flag(self) -> None:
        from corpus_forge.embedders.registry import _per_provider_extras

        cfg = MagicMock(spec=["provider", "model_id", "n_ctx", "n_gpu_layers", "append_eos"])
        cfg.provider = "llama-cpp"
        cfg.model_id = "whatever:1"
        cfg.append_eos = True
        # No ``effective_append_eos`` attribute on the spec → raw-flag path.
        assert _per_provider_extras(cfg)["append_eos"] is True
