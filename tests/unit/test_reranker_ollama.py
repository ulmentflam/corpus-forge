"""R4-09 — `OllamaReranker` behaviour pins.

The Ollama reranker is a score-via-completion fallback: each ``(query,
passage)`` pair gets a chat completion request asking the model to
return a 0-10 relevance score; the score is parsed from the response.

This is a debug / parity path (N completions per rerank, slow), not a
production reranker.  Tests pin the prompt template + parser:

- No default ``model_id`` (caller MUST specify; embedding-only Ollama
  tags silently produce garbage otherwise).
- Lazy ``_get_client``: ``__init__`` does NOT construct the OpenAI client.
- The scoring prompt MUST include both query and passage text.
- The parser tolerates surrounding prose ("score: 7.5", "I'd say 8").
- Parse failures score 0 (do not crash the whole rerank pass).
- Output hits carry ``source="reranked"`` and the parsed score replaces
  the fused score.
- Sort: descending by parsed score, ties broken by fused score then
  chunk_id (mirror of CrossEncoderReranker).
- Empty input short-circuits BEFORE client construction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.retrieval.rerank.ollama import OllamaReranker, _parse_score
from corpus_forge.retrieval.types import Hit


def _hit(cid: int, *, score: float, text: str = "") -> Hit:
    return Hit(
        chunk_id=cid,
        score=score,
        text=text or f"chunk-{cid}",
        document_id=None,
        source_uri=f"test://{cid}",
        title=None,
        dataset_id=1,
        metadata={},
        source="fused",
    )


# ---------------------------------------------------------------------------
# Score parser
# ---------------------------------------------------------------------------


class TestParseScore:
    def test_integer_alone(self):
        assert _parse_score("7") == 7.0

    def test_decimal_alone(self):
        assert _parse_score("7.5") == pytest.approx(7.5)

    def test_score_with_prose(self):
        assert _parse_score("Score: 8.2") == pytest.approx(8.2)

    def test_score_in_middle_of_sentence(self):
        assert _parse_score("I'd say this is about a 6 out of 10") == pytest.approx(6.0)

    def test_garbage_returns_zero(self):
        assert _parse_score("not relevant at all") == 0.0

    def test_empty_returns_zero(self):
        assert _parse_score("") == 0.0
        assert _parse_score("   ") == 0.0

    def test_clips_to_zero_ten(self):
        assert _parse_score("100") == 10.0
        # Negatives — the regex captures positive numbers only.  A Unicode
        # minus sign would never bind; ASCII "-3" → 3 (regex captures
        # bare digits without consuming the sign).
        # We only need the clip to [0, 10] to hold for runaway high vals.
        assert _parse_score("9999") == 10.0


# ---------------------------------------------------------------------------
# Constructor + lazy client
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_no_default_model_id(self):
        """Caller MUST provide a `model_id`; there's no sensible default."""
        with pytest.raises(TypeError):
            OllamaReranker()  # type: ignore[call-arg]

    def test_stores_model_id(self):
        r = OllamaReranker(model_id="llama3.1:8b")
        assert r.model_id == "llama3.1:8b"
        # name has a sensible default but is overridable.
        assert r.name == "ollama-reranker"

    def test_name_overridable(self):
        r = OllamaReranker(model_id="any:tag", name="custom-name")
        assert r.name == "custom-name"

    def test_base_url_default_points_at_localhost_ollama(self):
        r = OllamaReranker(model_id="x:y")
        assert r.base_url == "http://localhost:11434/v1"

    def test_base_url_overridable(self):
        r = OllamaReranker(model_id="x:y", base_url="http://remote:11434/v1")
        assert r.base_url == "http://remote:11434/v1"

    def test_construction_does_not_instantiate_openai_client(self):
        """`__init__` must NOT construct the OpenAI client."""
        with patch.object(OllamaReranker, "_get_client") as mock:
            OllamaReranker(model_id="llama3.1:8b")
            OllamaReranker(model_id="x:y", base_url="http://r:11434/v1")
            assert mock.call_count == 0


# ---------------------------------------------------------------------------
# Empty input short-circuits before client construction
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_hits_returns_empty_without_client(self):
        with patch.object(OllamaReranker, "_get_client") as mock:
            r = OllamaReranker(model_id="x:y")
            assert r.rerank("q", []) == []
            assert mock.call_count == 0


# ---------------------------------------------------------------------------
# Scoring + ordering
# ---------------------------------------------------------------------------


def _build_mock_client(per_call_responses: list[str]) -> Any:
    """Build a MagicMock OpenAI client whose `chat.completions.create`
    returns the next response in `per_call_responses` per call."""
    client = MagicMock()
    iterator = iter(per_call_responses)

    def _create(*, model, messages, temperature):
        # Build a response shaped like an OpenAI completion.
        resp = MagicMock()
        try:
            content = next(iterator)
        except StopIteration:
            content = ""
        resp.choices = [MagicMock()]
        resp.choices[0].message = MagicMock()
        resp.choices[0].message.content = content
        return resp

    client.chat.completions.create.side_effect = _create
    return client


class TestScoringAndOrdering:
    def _patch_client(self, monkeypatch, mock_client):
        monkeypatch.setattr(
            "corpus_forge.retrieval.rerank.ollama.OllamaReranker._get_client",
            lambda self, _c=mock_client: _c,
        )

    def test_scoring_prompt_includes_query_and_passage(self, monkeypatch):
        client = _build_mock_client(["5"])
        self._patch_client(monkeypatch, client)
        r = OllamaReranker(model_id="llama3.1:8b")
        r.rerank("how are you", [_hit(1, score=0.0, text="passage body")])

        # The prompt sent in `messages[0].content` must mention both the
        # query and the passage.
        kwargs = client.chat.completions.create.call_args.kwargs
        msg = kwargs["messages"][0]["content"]
        assert "how are you" in msg
        assert "passage body" in msg

    def test_output_score_equals_parsed_score(self, monkeypatch):
        client = _build_mock_client(["9", "3"])
        self._patch_client(monkeypatch, client)
        r = OllamaReranker(model_id="llama3.1:8b")
        out = r.rerank("q", [_hit(1, score=0.5), _hit(2, score=0.5)])
        assert len(out) == 2
        # Chunk 1 scored 9 → wins.  Output ordering: [1, 2].
        assert [h.chunk_id for h in out] == [1, 2]
        assert out[0].score == pytest.approx(9.0)
        assert out[1].score == pytest.approx(3.0)

    def test_output_source_reranked(self, monkeypatch):
        client = _build_mock_client(["7", "4"])
        self._patch_client(monkeypatch, client)
        r = OllamaReranker(model_id="llama3.1:8b")
        out = r.rerank("q", [_hit(1, score=0.0), _hit(2, score=0.0)])
        for h in out:
            assert h.source == "reranked"

    def test_top_n_clips_input(self, monkeypatch):
        client = _build_mock_client(["8", "5"])
        self._patch_client(monkeypatch, client)
        r = OllamaReranker(model_id="llama3.1:8b")
        hits = [_hit(i, score=10.0 - i) for i in range(5)]
        out = r.rerank("q", hits, top_n=2)
        # Only first 2 hits (top-fused) are scored.
        assert client.chat.completions.create.call_count == 2
        assert len(out) == 2

    def test_parse_failure_scores_zero(self, monkeypatch):
        """Garbage response scores 0; does NOT crash the rerank pass."""
        client = _build_mock_client(["garbage no number"])
        self._patch_client(monkeypatch, client)
        r = OllamaReranker(model_id="llama3.1:8b")
        out = r.rerank("q", [_hit(1, score=0.5)])
        assert len(out) == 1
        assert out[0].score == 0.0

    def test_network_error_scores_zero(self, monkeypatch):
        """A raising client returns score 0 — the rerank pass does NOT crash."""
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("boom")
        monkeypatch.setattr(
            "corpus_forge.retrieval.rerank.ollama.OllamaReranker._get_client",
            lambda self, _c=client: _c,
        )
        r = OllamaReranker(model_id="llama3.1:8b")
        out = r.rerank("q", [_hit(1, score=0.5), _hit(2, score=0.5)])
        # Both score 0 → tie → fused score (also tied) → chunk_id asc.
        assert len(out) == 2
        for h in out:
            assert h.score == 0.0
        assert [h.chunk_id for h in out] == [1, 2]


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------


def test_protocol_runtime_check():
    """`OllamaReranker` satisfies the `Reranker` Protocol structurally."""
    from corpus_forge.retrieval.rerank import Reranker

    r = OllamaReranker(model_id="x:y")
    assert isinstance(r, Reranker)
