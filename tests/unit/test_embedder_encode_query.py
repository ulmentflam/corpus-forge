"""R2-01 — `Embedder.encode_query` pins (asymmetric embedding).

The plan adds `encode_query(texts, *, batch_size=32) -> np.ndarray` to the
`Embedder` protocol with a default implementation on `BaseEmbedder` that
delegates to `self.encode(...)`.  Subclasses MAY override for asymmetric
families.

`SentenceTransformersEmbedder` overrides for the Qwen3-Embedding family:
the documented Qwen3 query-side format is the instruction prompt:

    "Instruct: Given a web search query, retrieve relevant passages that
    answer the query\nQuery: " + <user query>

The detection is by `model_id` prefix — either `Qwen/Qwen3-Embedding` (HF
hub canonical name) or the lowercase `qwen3-embedding` (Ollama-style alias).

For non-Qwen models, `encode_query` delegates to `encode` unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── Protocol surface ──────────────────────────────────────────────────────


def test_embedder_protocol_has_encode_query():
    """The Embedder Protocol must declare `encode_query`."""
    from corpus_forge.embedders.base import Embedder

    # Protocol attribute presence — check via __annotations__ or attribute on the class.
    assert hasattr(Embedder, "encode_query"), (
        "Embedder protocol must declare encode_query(texts, *, batch_size=32)"
    )


def test_base_embedder_has_encode_query():
    """BaseEmbedder must provide a default implementation."""
    from corpus_forge.embedders.base import BaseEmbedder

    assert hasattr(BaseEmbedder, "encode_query")


# ── BaseEmbedder default delegates to encode ──────────────────────────────


class _RecordingEmbedder:
    """Tiny subclass of BaseEmbedder used to verify the delegation path.

    Defined as a recorder around the real BaseEmbedder so we can stub `encode`.
    """

    name = "rec"
    provider = "test"
    model_id = "test/rec"
    dimension = 4
    normalized = True
    distance = "cosine"

    def __init__(self) -> None:
        self.encode_calls: list[tuple[tuple[str, ...], dict]] = []

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_calls.append((tuple(texts), {"batch_size": batch_size}))
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


def test_base_embedder_encode_query_delegates_to_encode():
    """The default `encode_query` calls `encode` with the same arguments."""
    from corpus_forge.embedders.base import BaseEmbedder

    rec = _RecordingEmbedder()
    # Bind the default impl onto our recorder
    out = BaseEmbedder.encode_query(rec, ["hello", "world"], batch_size=16)
    assert isinstance(out, np.ndarray)
    assert out.shape == (2, 4)
    assert rec.encode_calls == [(("hello", "world"), {"batch_size": 16})]


def test_base_embedder_encode_query_default_batch_size():
    """Default batch_size is 32 per the plan."""
    from corpus_forge.embedders.base import BaseEmbedder

    rec = _RecordingEmbedder()
    BaseEmbedder.encode_query(rec, ["hello"])
    assert rec.encode_calls == [(("hello",), {"batch_size": 32})]


# ── SentenceTransformersEmbedder: Qwen3 override path ─────────────────────


_QWEN_INSTRUCT_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)


class TestSentenceTransformersEncodeQuery:
    """Qwen3 family gets the instruction prompt prepended; others pass through."""

    def _patched_st_class(self, captured_texts: list[list[str]]):
        """Build a SentenceTransformer stand-in that records inputs and returns zeros."""

        class _FakeST:
            def __init__(self, model_id: str, device: str = "cpu") -> None:
                self.model_id = model_id
                self.device = device

            def encode(
                self,
                texts,
                *,
                batch_size: int = 32,
                convert_to_numpy: bool = True,
                normalize_embeddings: bool = True,
            ) -> np.ndarray:
                captured_texts.append(list(texts))
                return np.zeros((len(texts), 8), dtype=np.float32)

        return _FakeST

    def _make_embedder(self, model_id: str, dim: int = 8):
        from corpus_forge.embedders.sentence_transformers import (
            SentenceTransformersEmbedder,
        )

        return SentenceTransformersEmbedder(
            name="t",
            model_id=model_id,
            dimension=dim,
            normalized=True,
            distance="cosine",
            device="cpu",
        )

    @patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    def test_qwen3_prefix_canonical(self):
        """Qwen/Qwen3-Embedding-* models get the instruct prefix prepended."""
        captured: list[list[str]] = []
        fake_cls = self._patched_st_class(captured)
        emb = self._make_embedder("Qwen/Qwen3-Embedding-8B")
        with patch(
            "corpus_forge.embedders.sentence_transformers.SentenceTransformer",
            fake_cls,
        ):
            out = emb.encode_query(["how does lock_source work"], batch_size=4)
        assert isinstance(out, np.ndarray)
        assert out.shape == (1, 8)
        # The fake's encode() should have seen the instruct-prefixed string.
        assert len(captured) == 1
        sent = captured[0]
        assert len(sent) == 1
        assert sent[0].startswith(_QWEN_INSTRUCT_PREFIX)
        assert sent[0].endswith("how does lock_source work")

    @patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    def test_qwen3_prefix_lowercase_alias(self):
        """The lowercase `qwen3-embedding` alias also triggers the override."""
        captured: list[list[str]] = []
        fake_cls = self._patched_st_class(captured)
        emb = self._make_embedder("qwen3-embedding-8b")
        with patch(
            "corpus_forge.embedders.sentence_transformers.SentenceTransformer",
            fake_cls,
        ):
            emb.encode_query(["another query"])
        assert captured[0][0].startswith(_QWEN_INSTRUCT_PREFIX)

    @patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    def test_non_qwen_no_prefix(self):
        """Non-Qwen3 models pass the query through unchanged."""
        captured: list[list[str]] = []
        fake_cls = self._patched_st_class(captured)
        emb = self._make_embedder("BAAI/bge-large-en-v1.5")
        with patch(
            "corpus_forge.embedders.sentence_transformers.SentenceTransformer",
            fake_cls,
        ):
            emb.encode_query(["plain query"])
        assert captured[0][0] == "plain query"
        assert not captured[0][0].startswith("Instruct:")

    @patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    def test_qwen3_multiple_queries_all_prefixed(self):
        captured: list[list[str]] = []
        fake_cls = self._patched_st_class(captured)
        emb = self._make_embedder("Qwen/Qwen3-Embedding-4B")
        with patch(
            "corpus_forge.embedders.sentence_transformers.SentenceTransformer",
            fake_cls,
        ):
            emb.encode_query(["q1", "q2", "q3"])
        assert len(captured[0]) == 3
        for q in captured[0]:
            assert q.startswith(_QWEN_INSTRUCT_PREFIX)

    @patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    def test_encode_unchanged_no_prefix_for_qwen3(self):
        """`encode` (document-side) does NOT prepend the Qwen3 prefix; only `encode_query` does."""
        captured: list[list[str]] = []
        fake_cls = self._patched_st_class(captured)
        emb = self._make_embedder("Qwen/Qwen3-Embedding-8B")
        with patch(
            "corpus_forge.embedders.sentence_transformers.SentenceTransformer",
            fake_cls,
        ):
            emb.encode(["a passage to embed"])
        # The captured text must be the raw passage, no instruct prefix.
        assert captured[0][0] == "a passage to embed"

    def test_encode_query_empty_returns_empty_ndarray(self):
        """Empty input → zero-row ndarray, no model load."""
        emb = self._make_embedder("Qwen/Qwen3-Embedding-8B")
        out = emb.encode_query([])
        assert isinstance(out, np.ndarray)
        assert out.shape == (0, 8)


# ── Mirror of the FakeEmbedder used in dual-backend tests ─────────────────


def test_recording_embedder_via_default_path():
    """Sanity: BaseEmbedder.encode_query bound to a fresh BaseEmbedder delegates to encode."""
    from corpus_forge.embedders.base import BaseEmbedder

    be = BaseEmbedder(
        name="b",
        provider="test",
        model_id="test/b",
        dimension=4,
        normalized=True,
        distance="cosine",
    )
    # BaseEmbedder.encode is undefined by default; provide one via attribute injection.
    encode_log: list[list[str]] = []

    def fake_encode(texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        encode_log.append(list(texts))
        return np.zeros((len(texts), 4), dtype=np.float32)

    be.encode = fake_encode  # type: ignore[attr-defined]
    out = be.encode_query(["x", "y"])
    assert out.shape == (2, 4)
    assert encode_log == [["x", "y"]]


def test_protocol_runtime_check_against_recording_embedder():
    """A class that exposes name/provider/model_id/dimension/encode/encode_query/warmup
    must satisfy the Embedder protocol structurally (duck-typed)."""
    from corpus_forge.embedders.base import BaseEmbedder

    be = BaseEmbedder(
        name="b",
        provider="test",
        model_id="test/b",
        dimension=4,
    )

    # Provide the missing pieces.
    encoded = MagicMock(return_value=np.zeros((1, 4), dtype=np.float32))
    be.encode = encoded  # type: ignore[attr-defined]
    out = be.encode_query(["query"])
    assert out.shape == (1, 4)
    encoded.assert_called_once()


# ── Protocol method signature pin (smoke; reflective) ─────────────────────


def test_encode_query_signature_has_batch_size_kwarg():
    """`encode_query` must accept a keyword-only `batch_size` per the plan."""
    import inspect

    from corpus_forge.embedders.base import BaseEmbedder

    sig = inspect.signature(BaseEmbedder.encode_query)
    params = sig.parameters
    assert "texts" in params
    assert "batch_size" in params
    bs = params["batch_size"]
    # keyword-only or kwarg with default 32
    assert bs.default == 32

    # texts may be POSITIONAL_OR_KEYWORD; batch_size must NOT be positional-only
    assert bs.kind is not inspect.Parameter.POSITIONAL_ONLY


@pytest.mark.parametrize(
    "model_id",
    [
        "Qwen/Qwen3-Embedding-0.6B",
        "Qwen/Qwen3-Embedding-4B",
        "Qwen/Qwen3-Embedding-8B",
        "qwen3-embedding-0.6b",
        "qwen3-embedding-4b",
        "qwen3-embedding-8b",
    ],
)
@patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", True)
def test_all_qwen3_variants_prefixed(model_id):
    from corpus_forge.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )

    captured: list[list[str]] = []

    class _FakeST:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, texts, **_kwargs) -> np.ndarray:
            captured.append(list(texts))
            return np.zeros((len(texts), 8), dtype=np.float32)

    emb = SentenceTransformersEmbedder(
        name="t",
        model_id=model_id,
        dimension=8,
        normalized=True,
        distance="cosine",
        device="cpu",
    )
    with patch(
        "corpus_forge.embedders.sentence_transformers.SentenceTransformer",
        _FakeST,
    ):
        emb.encode_query(["q"])
    assert captured[0][0].startswith(_QWEN_INSTRUCT_PREFIX)
