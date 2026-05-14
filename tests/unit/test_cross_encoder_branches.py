"""Additional branch tests for CrossEncoderReranker.

Covers the lines not hit by the main test_reranker_cross_encoder.py:
- _resolve_device: non-auto path (line 75), MPS path (line 83), CUDA path (line 85)
- _get_model caching (lines 133-150): memoisation, CrossEncoder construction

Lines 75-86 in cross_encoder.py:
    75: if device != _AUTO_DEVICE: return device
    79-81: torch ImportError → "cpu"
    83-84: mps available → "mps"
    85-86: cuda available → "cuda"
    87: fallback → "cpu"

Lines 133-150: _get_model body (load CrossEncoder, cache, device resolution).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.retrieval.rerank.cross_encoder import (
    CrossEncoderReranker,
    _resolve_device,
)

# ---------------------------------------------------------------------------
# _resolve_device
# ---------------------------------------------------------------------------


class TestResolveDevice:
    def test_explicit_cpu_passthrough(self):
        """Non-auto device is returned as-is (line 75)."""
        assert _resolve_device("cpu") == "cpu"

    def test_explicit_cuda_passthrough(self):
        assert _resolve_device("cuda") == "cuda"

    def test_explicit_mps_passthrough(self):
        assert _resolve_device("mps") == "mps"

    def test_auto_resolves_to_mps_when_available(self):
        """auto → mps when torch.backends.mps.is_available() is True (line 83)."""
        mock_torch = MagicMock()
        mock_torch.backends.mps.is_available.return_value = True
        mock_torch.cuda.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = _resolve_device("auto")
        assert result == "mps"

    def test_auto_resolves_to_cuda_when_mps_unavailable(self):
        """auto → cuda when MPS is off but CUDA is on (line 85)."""
        mock_torch = MagicMock()
        mock_torch.backends.mps.is_available.return_value = False
        mock_torch.cuda.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = _resolve_device("auto")
        assert result == "cuda"

    def test_auto_resolves_to_cpu_when_neither_available(self):
        """auto → cpu when both MPS and CUDA are off (line 87)."""
        mock_torch = MagicMock()
        mock_torch.backends.mps.is_available.return_value = False
        mock_torch.cuda.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = _resolve_device("auto")
        assert result == "cpu"


# ---------------------------------------------------------------------------
# _get_model: caching and CrossEncoder construction (lines 133-150)
# ---------------------------------------------------------------------------


class TestGetModelBody:
    """Test _get_model body (lines 133-150).

    ``CrossEncoder`` is imported lazily inside ``_get_model`` via:
        from sentence_transformers import CrossEncoder
    We inject a fake ``sentence_transformers`` module into sys.modules.
    """

    def _make_st_module(self, model_instance):
        """Return a fake sentence_transformers module with a scripted CrossEncoder."""
        mock_ce_cls = MagicMock(return_value=model_instance)
        mock_st = MagicMock()
        mock_st.CrossEncoder = mock_ce_cls
        return mock_st, mock_ce_cls

    def test_get_model_caches_result(self):
        """_get_model returns the same object on second call without re-running body."""
        stub_model = MagicMock()
        mock_st, mock_ce_cls = self._make_st_module(stub_model)

        with patch.dict("sys.modules", {"sentence_transformers": mock_st, "torch": MagicMock()}):
            r = CrossEncoderReranker(device="cpu")
            m1 = r._get_model()
            m2 = r._get_model()

        assert m1 is m2
        assert m1 is stub_model
        # CrossEncoder constructor only called once
        assert mock_ce_cls.call_count == 1

    def test_get_model_constructs_cross_encoder_with_correct_args(self):
        """Verify model_id, max_length, and device are forwarded to CrossEncoder."""
        stub_model = MagicMock()
        mock_st, mock_ce_cls = self._make_st_module(stub_model)

        with patch.dict("sys.modules", {"sentence_transformers": mock_st, "torch": MagicMock()}):
            r = CrossEncoderReranker(
                model_id="cross-encoder/ms-marco-MiniLM-L-12-v2",
                device="cpu",
                max_length=256,
            )
            r._get_model()

        mock_ce_cls.assert_called_once()
        kwargs = mock_ce_cls.call_args.kwargs
        assert kwargs.get("max_length") == 256
        assert kwargs.get("device") == "cpu"
        all_args = list(mock_ce_cls.call_args.args) + list(mock_ce_cls.call_args.kwargs.values())
        assert "cross-encoder/ms-marco-MiniLM-L-12-v2" in all_args

    def test_get_model_resolves_auto_device(self):
        """auto device sentinel is resolved before CrossEncoder construction."""
        stub_model = MagicMock()
        mock_st, mock_ce_cls = self._make_st_module(stub_model)

        # Fake torch: MPS unavailable, CUDA unavailable → cpu
        mock_torch = MagicMock()
        mock_torch.backends.mps.is_available.return_value = False
        mock_torch.cuda.is_available.return_value = False

        with patch.dict(
            "sys.modules",
            {"sentence_transformers": mock_st, "torch": mock_torch},
        ):
            r = CrossEncoderReranker(device="auto")
            r._get_model()

        called_kwargs = mock_ce_cls.call_args.kwargs
        assert called_kwargs.get("device") == "cpu"


# ---------------------------------------------------------------------------
# warmup path (triggers _get_model + predict)
# ---------------------------------------------------------------------------


class TestWarmup:
    def test_warmup_calls_predict_with_warmup_pair(self):
        """warmup() triggers _get_model and calls predict([('warmup', 'warmup')])."""
        stub_model = MagicMock()
        with patch.object(CrossEncoderReranker, "_get_model", return_value=stub_model):
            r = CrossEncoderReranker()
            r.warmup()
        stub_model.predict.assert_called_once()
        call_args = stub_model.predict.call_args[0][0]
        assert ("warmup", "warmup") in call_args
