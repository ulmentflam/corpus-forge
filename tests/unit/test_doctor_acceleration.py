"""Unit tests for ``_check_embedder_acceleration``.

The check is informational — it surfaces the detected GPU / CPU lane
plus the recommended embedder preset.  Status is always ``OK`` (no
WARN even on CPU; "you don't have a GPU" isn't a failure) so the
overall doctor report stays healthy.
"""

from __future__ import annotations

from unittest.mock import patch

from corpus_forge.acceleration import Accelerator, AcceleratorInfo
from corpus_forge.doctor.checks import (
    CheckStatus,
    _check_embedder_acceleration,
)


def _stub(info: AcceleratorInfo):
    """Patch ``detect_accelerator`` for the duration of a single check."""
    return patch("corpus_forge.doctor.checks.detect_accelerator", return_value=info)


class TestCheckEmbedderAcceleration:
    def test_cuda_reports_ok_with_gpu_name_and_model(self):
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA RTX 4090",
            vram_mb=24576,
        )
        with _stub(info):
            result = _check_embedder_acceleration()
        assert result.status is CheckStatus.OK
        assert result.name == "embedder_acceleration"
        # Detail surfaces both the device + the recommended model so
        # the user can spot a config that's leaving GPU on the table.
        assert "RTX 4090" in result.detail
        assert "qwen3-embedding:8b" in result.detail

    def test_low_vram_cuda_recommends_smaller_model(self):
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA GTX 1060 6GB",
            vram_mb=6144,
        )
        with _stub(info):
            result = _check_embedder_acceleration()
        assert result.status is CheckStatus.OK
        # The 6 GB card should land on the nomic-embed-text lane.
        assert "nomic-embed-text" in result.detail

    def test_mps_reports_ok_with_metal_blurb(self):
        with _stub(AcceleratorInfo(kind=Accelerator.MPS)):
            result = _check_embedder_acceleration()
        assert result.status is CheckStatus.OK
        # MPS detail mentions Apple Silicon / Metal so it's
        # unambiguous in the report alongside cuda / cpu rows.
        assert "Apple Silicon" in result.detail or "Metal" in result.detail
        assert "qwen3-embedding:8b" in result.detail

    def test_cpu_reports_ok_with_cpu_lane(self):
        with _stub(AcceleratorInfo(kind=Accelerator.CPU)):
            result = _check_embedder_acceleration()
        # CPU is not a failure — informational only.
        assert result.status is CheckStatus.OK
        assert "No GPU" in result.detail or "CPU" in result.detail
        assert "nomic-embed-text" in result.detail

    def test_check_is_registered_in_run_doctor(self):
        """``run_doctor`` includes the acceleration check.

        Without this pin, a future refactor that drops the check from
        ``_CHECKS`` would silently lose the recommendation surface.
        """
        from corpus_forge.doctor.checks import _CHECKS

        assert _check_embedder_acceleration in _CHECKS
