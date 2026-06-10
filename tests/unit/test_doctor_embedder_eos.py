"""Tests for the ``embedder_eos`` doctor check (RFC embedder-eos diagnostic).

The check flags embedders that resolve ``append_eos=True`` but are served
via a transport corpus-forge doesn't yet terminate client-side (the
``openai`` provider). The in-process ``llama-cpp`` provider appends the
terminator at the token layer, so it's reported as wired, not flagged.

What we pin
-----------
1. No embedders → ``OK`` (nothing requests the terminator).
2. A ``llama-cpp`` embedder that wants EOS → ``OK``, named as wired.
3. An ``openai`` embedder that wants EOS → ``WARN``, named as a gap.
4. Mixed → ``WARN`` naming only the un-terminated (``openai``) one.
5. Inactive embedders are ignored.
6. ``sentence_transformers`` (own HF tokenizer) is not flagged.
7. An explicit ``append_eos=False`` is not a request.
8. Registered in ``run_doctor`` (appears even when config can't load).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from corpus_forge.config import EmbedderConfig
from corpus_forge.doctor.checks import (
    CheckStatus,
    _check_embedder_eos,
    run_doctor,
)


def _cfg(*embedders) -> MagicMock:
    cfg = MagicMock()
    cfg.embedders = list(embedders)
    return cfg


def _emb(name, provider, model_id, *, append_eos=None, active=True, dimension=768):
    return EmbedderConfig(
        name=name,
        provider=provider,
        model_id=model_id,
        dimension=dimension,
        append_eos=append_eos,
        active=active,
    )


def test_no_embedders_is_ok():
    result = _check_embedder_eos(_cfg())
    assert result.status is CheckStatus.OK
    assert "no embedder requests" in result.detail


def test_llama_cpp_eos_embedder_is_ok_and_named():
    emb = _emb("nomic-code", "llama-cpp", "manutic/nomic-embed-code:latest", dimension=3584)
    result = _check_embedder_eos(_cfg(emb))
    assert result.status is CheckStatus.OK
    assert "nomic-code" in result.detail
    assert "llama-cpp" in result.detail


def test_openai_eos_embedder_warns_and_names_it():
    emb = _emb("nomic", "openai", "nomic-embed-text")
    result = _check_embedder_eos(_cfg(emb))
    assert result.status is CheckStatus.WARN
    assert "nomic" in result.detail
    assert "openai" in result.detail


def test_mixed_warns_only_about_openai():
    code = _emb("nomic-code", "llama-cpp", "manutic/nomic-embed-code:latest", dimension=3584)
    text = _emb("nomic", "openai", "nomic-embed-text")
    result = _check_embedder_eos(_cfg(code, text))
    assert result.status is CheckStatus.WARN
    assert "nomic" in result.detail
    # The wired llama-cpp embedder is not named as a gap.
    assert "nomic-code" not in result.detail


def test_inactive_embedder_ignored():
    emb = _emb("nomic", "openai", "nomic-embed-text", active=False)
    result = _check_embedder_eos(_cfg(emb))
    assert result.status is CheckStatus.OK


def test_sentence_transformers_not_flagged():
    # ST tokenizes via its own HF tokenizer (adds special tokens itself),
    # so even an EOS-wanting model isn't a corpus-forge transport gap.
    emb = _emb("nomic-st", "sentence_transformers", "nomic-ai/nomic-embed-text-v1.5")
    result = _check_embedder_eos(_cfg(emb))
    assert result.status is CheckStatus.OK


def test_explicit_append_eos_false_is_not_a_request():
    emb = _emb("nomic", "openai", "nomic-embed-text", append_eos=False)
    result = _check_embedder_eos(_cfg(emb))
    assert result.status is CheckStatus.OK


class TestRegisteredInRunDoctor:
    def test_embedder_eos_appears_in_report(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        names = {r.name for r in report.results}
        assert "embedder_eos" in names

    def test_embedder_eos_skipped_when_config_missing(self, tmp_path) -> None:
        report = run_doctor(config_path=tmp_path / "no-config.toml")
        rows = [r for r in report.results if r.name == "embedder_eos"]
        assert len(rows) == 1
        assert rows[0].status is CheckStatus.SKIP
