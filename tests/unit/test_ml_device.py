"""Unit tests for the shared device-detection helper.

Targets :mod:`corpus_forge._ml_device` — the one place the MPS → CUDA →
CPU heuristic lives, shared by sentence-transformers,
``faster-whisper`` (which disables MPS via ``prefer_mps=False``), CLIP
local, and the cross-encoder reranker.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from corpus_forge._ml_device import detect_device, resolve_device


class TestResolveDevicePassthrough:
    """Concrete strings pass straight through; only ``"auto"`` triggers detection."""

    def test_cpu_passthrough(self) -> None:
        assert resolve_device("cpu") == "cpu"

    def test_cuda_passthrough(self) -> None:
        assert resolve_device("cuda") == "cuda"

    def test_mps_passthrough(self) -> None:
        assert resolve_device("mps") == "mps"

    def test_auto_triggers_detection(self) -> None:
        with patch("corpus_forge._ml_device.detect_device", return_value="cpu"):
            assert resolve_device("auto") == "cpu"


class TestDetectDevice:
    def _mock_torch(self, *, mps: bool, cuda: bool) -> MagicMock:
        torch = MagicMock()
        torch.backends.mps.is_available.return_value = mps
        torch.cuda.is_available.return_value = cuda
        return torch

    def test_prefers_mps_when_available(self) -> None:
        with patch.dict(sys.modules, {"torch": self._mock_torch(mps=True, cuda=False)}):
            assert detect_device() == "mps"

    def test_falls_back_to_cuda(self) -> None:
        with patch.dict(sys.modules, {"torch": self._mock_torch(mps=False, cuda=True)}):
            assert detect_device() == "cuda"

    def test_falls_back_to_cpu(self) -> None:
        with patch.dict(sys.modules, {"torch": self._mock_torch(mps=False, cuda=False)}):
            assert detect_device() == "cpu"

    def test_prefer_mps_false_skips_mps_branch(self) -> None:
        """faster-whisper doesn't support MPS — pass ``prefer_mps=False``
        and the helper picks CUDA / CPU even when MPS is available."""
        with patch.dict(sys.modules, {"torch": self._mock_torch(mps=True, cuda=True)}):
            assert detect_device(prefer_mps=False) == "cuda"
        with patch.dict(sys.modules, {"torch": self._mock_torch(mps=True, cuda=False)}):
            assert detect_device(prefer_mps=False) == "cpu"

    def test_no_torch_falls_back_to_cpu(self) -> None:
        """The helper imports torch lazily so callers without the ML
        stack installed still resolve to ``"cpu"`` (rather than
        crashing on import)."""
        # Force the lazy import to raise.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def fake_import(name, *a, **kw):
            if name == "torch":
                raise ImportError("torch not installed")
            return real_import(name, *a, **kw)

        with patch("builtins.__import__", side_effect=fake_import):
            assert detect_device() == "cpu"
