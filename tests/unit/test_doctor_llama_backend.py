"""Unit tests for ``_check_llama_cpp_backend`` (RFC fleet-7 item 4).

The check reconciles the installed ``llama-cpp-python`` build's GPU-offload
support against the detected accelerator, so it can WARN on the silent
"draining on CPU" trap (a CUDA / Apple-Silicon box that fetched the CPU-only
wheel). Both inputs are patched at the import path where the check uses them:

- ``corpus_forge.doctor.checks._llama_cpp_supports_gpu_offload`` →
  ``True`` / ``False`` / ``None`` (``None`` = library not installed).
- ``corpus_forge.doctor.checks.detect_accelerator`` → an ``AcceleratorInfo``.
"""

from __future__ import annotations

from unittest.mock import patch

from corpus_forge.acceleration import Accelerator, AcceleratorInfo
from corpus_forge.doctor.checks import (
    CheckStatus,
    _check_llama_cpp_backend,
    run_doctor,
)


def _accel(info: AcceleratorInfo):
    return patch("corpus_forge.doctor.checks.detect_accelerator", return_value=info)


def _offload(value: bool | None):
    return patch(
        "corpus_forge.doctor.checks._llama_cpp_supports_gpu_offload",
        return_value=value,
    )


class TestCheckLlamaCppBackend:
    def test_skip_when_llama_cpp_not_installed(self):
        # Probe returns None → the in-process embedder isn't in use; SKIP
        # keeps the overall report healthy (SKIP is not a failure).
        with _offload(None):
            result = _check_llama_cpp_backend()
        assert result.name == "llama_cpp_backend"
        assert result.status is CheckStatus.SKIP
        assert "not installed" in result.detail

    def test_cuda_with_cpu_only_wheel_warns_with_reinstall_hint(self):
        info = AcceleratorInfo(kind=Accelerator.CUDA, device_name="NVIDIA RTX 4090", vram_mb=24576)
        with _offload(False), _accel(info):
            result = _check_llama_cpp_backend()
        assert result.status is CheckStatus.WARN
        # The detail names the device, the CPU-only trap, and the exact fix.
        assert "RTX 4090" in result.detail
        assert "CPU-only" in result.detail
        assert "--llama-backend cuda" in result.detail

    def test_cuda_with_gpu_wheel_is_ok(self):
        info = AcceleratorInfo(kind=Accelerator.CUDA, device_name="NVIDIA L40S")
        with _offload(True), _accel(info):
            result = _check_llama_cpp_backend()
        assert result.status is CheckStatus.OK
        assert "supports GPU offload" in result.detail

    def test_mps_with_cpu_only_wheel_warns_with_metal_hint(self):
        info = AcceleratorInfo(kind=Accelerator.MPS)
        with _offload(False), _accel(info):
            result = _check_llama_cpp_backend()
        assert result.status is CheckStatus.WARN
        assert "--llama-backend metal" in result.detail

    def test_cpu_box_with_cpu_wheel_is_ok(self):
        info = AcceleratorInfo(kind=Accelerator.CPU)
        with _offload(False), _accel(info):
            result = _check_llama_cpp_backend()
        assert result.status is CheckStatus.OK
        assert "expected build" in result.detail

    def test_detection_failure_never_crashes(self):
        # A wedged detector must not crash doctor — falls back to OK.
        with (
            _offload(True),
            patch(
                "corpus_forge.doctor.checks.detect_accelerator",
                side_effect=RuntimeError("nvidia-smi wedged"),
            ),
        ):
            result = _check_llama_cpp_backend()
        assert result.status is CheckStatus.OK
        assert "detection unavailable" in result.detail


class TestProbeHelper:
    def test_probe_returns_none_when_llama_cpp_absent(self, monkeypatch):
        # Force the lazy ``import llama_cpp`` to raise so the helper's
        # not-installed branch is covered regardless of the test env.
        import builtins

        from corpus_forge.doctor.checks import _llama_cpp_supports_gpu_offload

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "llama_cpp":
                raise ImportError("simulated: llama-cpp-python not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert _llama_cpp_supports_gpu_offload() is None


class TestRegistration:
    def test_check_appears_in_doctor_report(self, tmp_path):
        # The new check must be wired into the registry so it shows up in
        # the human + --json output. Use a missing config path so the run
        # is cheap; the registered checks still run.
        report = run_doctor(config_path=tmp_path / "nonexistent.toml")
        names = {r.name for r in report.results}
        assert "llama_cpp_backend" in names
        # And it serializes into the JSON shape.
        payload = report.to_json()
        json_names = {c["name"] for c in payload["checks"]}
        assert "llama_cpp_backend" in json_names
