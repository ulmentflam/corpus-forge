"""Unit tests for ``corpus_forge.acceleration``.

The detection helper is consulted at install time (setup wizard) and
runtime (``corpus-forge doctor``) to pick an embedder backend that
matches the host's hardware:

  * CUDA → in-process llama-cpp-python with ``n_gpu_layers=-1`` (the
    same shape the Metal-on-macOS path uses, so cross-host configs
    diverge by one line rather than three blocks).
  * MPS  → llama-cpp-python with Metal (macOS, existing path).
  * CPU  → llama-cpp-python with a smaller model (``nomic-embed-text``
    768d) — keeps the provider consistent across all three lanes.

The detection MUST work without ``torch`` installed (``corpus-forge
doctor`` ships on minimal installs), so the primary signal is a
``subprocess.run(["nvidia-smi", ...])`` shellout.  ``torch`` is only
consulted for the MPS branch (where the runtime API is the canonical
check anyway).
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from corpus_forge.acceleration import (
    Accelerator,
    AcceleratorInfo,
    detect_accelerator,
    recommend_embedder_preset,
)


class TestDetectAccelerator:
    """``detect_accelerator`` resolves to one of CUDA / MPS / CPU."""

    def test_cuda_detected_via_nvidia_smi(self):
        """``nvidia-smi`` returns OK + GPU info → CUDA accelerator."""
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24576\n",
            stderr="",
        )
        with patch("corpus_forge.acceleration.subprocess.run", return_value=completed):
            info = detect_accelerator()
        assert info.kind is Accelerator.CUDA
        assert info.device_name == "NVIDIA GeForce RTX 4090"
        assert info.vram_mb == 24576

    def test_cuda_with_multiple_gpus_picks_first(self):
        """Multi-GPU nvidia-smi output → first GPU is reported."""
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="NVIDIA A100 80GB PCIe, 81920\nNVIDIA A100 80GB PCIe, 81920\n",
            stderr="",
        )
        with patch("corpus_forge.acceleration.subprocess.run", return_value=completed):
            info = detect_accelerator()
        assert info.kind is Accelerator.CUDA
        assert info.device_name == "NVIDIA A100 80GB PCIe"
        assert info.vram_mb == 81920

    def test_nvidia_smi_missing_falls_through_to_mps_or_cpu(self):
        """``nvidia-smi`` not on PATH → FileNotFoundError → fall through."""
        with (
            patch(
                "corpus_forge.acceleration.subprocess.run",
                side_effect=FileNotFoundError("nvidia-smi not found"),
            ),
            patch("corpus_forge.acceleration._mps_available", return_value=False),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.CPU
        assert info.device_name is None
        assert info.vram_mb is None

    def test_nvidia_smi_present_but_returns_nonzero(self):
        """Driver mismatch / no GPU → exit code != 0 → not CUDA."""
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=9,
            stdout="",
            stderr="No devices were found",
        )
        with (
            patch("corpus_forge.acceleration.subprocess.run", return_value=completed),
            patch("corpus_forge.acceleration._mps_available", return_value=False),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.CPU

    def test_nvidia_smi_timeout_falls_through(self):
        """A hung nvidia-smi must not block the wizard / doctor."""
        with (
            patch(
                "corpus_forge.acceleration.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=2.0),
            ),
            patch("corpus_forge.acceleration._mps_available", return_value=False),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.CPU

    def test_mps_detected_when_no_cuda(self):
        """No NVIDIA driver + Apple Silicon → MPS."""
        with (
            patch(
                "corpus_forge.acceleration.subprocess.run",
                side_effect=FileNotFoundError("nvidia-smi not found"),
            ),
            patch("corpus_forge.acceleration._mps_available", return_value=True),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.MPS

    def test_cpu_fallback_when_neither_cuda_nor_mps(self):
        """Linux box with no NVIDIA GPU and no MPS → CPU."""
        with (
            patch(
                "corpus_forge.acceleration.subprocess.run",
                side_effect=FileNotFoundError("nvidia-smi not found"),
            ),
            patch("corpus_forge.acceleration._mps_available", return_value=False),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.CPU

    def test_nvidia_smi_empty_stdout(self):
        """returncode 0 but no GPU rows → not CUDA.

        Happens on hosts that have ``nvidia-smi`` installed (perhaps as
        part of a container image baseline) but no actual driver
        binding to GPUs at runtime.
        """
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"], returncode=0, stdout="\n", stderr=""
        )
        with (
            patch("corpus_forge.acceleration.subprocess.run", return_value=completed),
            patch("corpus_forge.acceleration._mps_available", return_value=False),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.CPU

    def test_nvidia_smi_unparseable_vram_keeps_cuda_lane(self):
        """Memory field is non-numeric → still CUDA, just no VRAM info.

        Robust to obscure driver-version output variations: we'd rather
        downgrade VRAM to None and recommend the small-model lane than
        misclassify as CPU.
        """
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="NVIDIA T4, [N/A]\n",
            stderr="",
        )
        with patch("corpus_forge.acceleration.subprocess.run", return_value=completed):
            info = detect_accelerator()
        assert info.kind is Accelerator.CUDA
        assert info.device_name == "NVIDIA T4"
        assert info.vram_mb is None

    def test_nvidia_smi_oserror_treated_as_missing(self):
        """``PermissionError`` / ``OSError`` from exec → fall through.

        Locked-down CI runners sometimes block ``nvidia-smi`` even when
        the binary is on PATH; an exception MUST NOT crash the wizard.
        """
        with (
            patch(
                "corpus_forge.acceleration.subprocess.run",
                side_effect=PermissionError("nvidia-smi: Operation not permitted"),
            ),
            patch("corpus_forge.acceleration._mps_available", return_value=False),
        ):
            info = detect_accelerator()
        assert info.kind is Accelerator.CPU


class TestMpsAvailable:
    """``_mps_available`` is the only torch-coupled branch — pin its
    fallbacks so import failures don't blow up doctor on minimal
    installs (the universal failure mode on a Linux box without ML
    extras)."""

    def test_torch_missing_returns_false(self):
        """``ImportError`` on torch → False, no crash."""
        import builtins

        from corpus_forge.acceleration import _mps_available

        real_import = builtins.__import__

        def _fake_import(name, *a, **k):
            if name == "torch":
                raise ImportError("No module named 'torch'")
            return real_import(name, *a, **k)

        with patch("builtins.__import__", side_effect=_fake_import):
            assert _mps_available() is False


class TestRecommendEmbedderPreset:
    """``recommend_embedder_preset`` maps detected hardware to a preset."""

    def test_cuda_recommends_llama_cpp_with_full_offload(self):
        """CUDA → llama-cpp-python with ``n_gpu_layers=-1``.

        Matches the Metal path's shape so configs are portable across
        Mac / Linux-GPU boxes with a single field diff.
        """
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA RTX 4090",
            vram_mb=24576,
        )
        preset = recommend_embedder_preset(info)
        assert preset.provider == "llama-cpp"
        assert preset.n_gpu_layers == -1
        # qwen3-embedding:8b is ~5 GB at q4_k_m — fits comfortably in
        # 24 GB; the preset assumes the user has at least 8 GB VRAM.
        assert preset.model_id == "qwen3-embedding:8b"
        assert preset.dimension == 4096

    def test_cuda_with_low_vram_recommends_smaller_model(self):
        """VRAM < 8 GB → smaller 768d model instead of qwen3-8b."""
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA GTX 1060 6GB",
            vram_mb=6144,
        )
        preset = recommend_embedder_preset(info)
        assert preset.provider == "llama-cpp"
        assert preset.n_gpu_layers == -1
        assert preset.model_id == "nomic-embed-text"
        assert preset.dimension == 768

    def test_cuda_with_unknown_vram_defaults_to_high_vram_model(self):
        """``vram_mb is None`` → optimistic ``qwen3-embedding:8b`` lane.

        Pins the optimistic-default branch documented in
        ``recommend_embedder_preset``: when ``nvidia-smi`` parsed the
        device name but the memory field didn't decode to an int
        (rare driver-version output quirk), we'd rather pick the
        high-capacity model and risk an OOM-on-first-encode than
        downgrade to a 768d lane whose dimension mismatch would
        force a full re-embed of an already-populated corpus.
        """
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA T4",
            vram_mb=None,
        )
        preset = recommend_embedder_preset(info)
        assert preset.provider == "llama-cpp"
        assert preset.n_gpu_layers == -1
        assert preset.model_id == "qwen3-embedding:8b"
        assert preset.dimension == 4096

    def test_mps_recommends_metal_offload(self):
        """MPS (Mac) → llama-cpp-python with Metal (n_gpu_layers=-1)."""
        info = AcceleratorInfo(kind=Accelerator.MPS)
        preset = recommend_embedder_preset(info)
        assert preset.provider == "llama-cpp"
        assert preset.n_gpu_layers == -1
        assert preset.model_id == "qwen3-embedding:8b"

    def test_cpu_recommends_cpu_friendly_small_model(self):
        """CPU → llama-cpp + smaller 768d model + n_gpu_layers=0."""
        info = AcceleratorInfo(kind=Accelerator.CPU)
        preset = recommend_embedder_preset(info)
        assert preset.provider == "llama-cpp"
        assert preset.n_gpu_layers == 0
        # nomic-embed-text is 137 M params — comfortable on a 4-core CPU.
        assert preset.model_id == "nomic-embed-text"
        assert preset.dimension == 768

    def test_preset_renders_toml_block(self):
        """Preset exposes a TOML snippet for the wizard / doctor."""
        info = AcceleratorInfo(kind=Accelerator.CPU)
        preset = recommend_embedder_preset(info)
        toml = preset.to_toml_block(name="nomic")
        assert 'name       = "nomic"' in toml
        assert 'provider   = "llama-cpp"' in toml
        assert "dimension  = 768" in toml
        assert "n_gpu_layers = 0" in toml

    def test_preset_carries_human_summary(self):
        """``summary`` text used by doctor's status detail line."""
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA RTX 4090",
            vram_mb=24576,
        )
        preset = recommend_embedder_preset(info)
        assert "RTX 4090" in preset.summary
        assert "24576" in preset.summary or "24 GB" in preset.summary
