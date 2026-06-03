"""Hardware-accelerator detection + embedder preset recommendation.

Consulted at two times:

* **``corpus-forge setup`` wizard** — picks an embedder block default
  that matches the host's hardware so the first install on Linux /
  CPU-only / non-Mac boxes lands a working config without manual
  edits.
* **``corpus-forge doctor``** — surfaces the detected accelerator
  alongside the recommended preset so operators can spot a config
  that's leaving GPU on the table (e.g. an in-place CPU config on a
  freshly-CUDA-capable runner).

Detection is intentionally **subprocess-only** for the CUDA branch —
``nvidia-smi`` is the universal driver-installed marker.  Loading
``torch`` just to read ``torch.cuda.is_available()`` would pull
~1 GB of CUDA wheels on hosts that wouldn't even use them; it's
also unreliable on torch wheels built without CUDA support (returns
``False`` despite the driver being present).  ``torch`` is consulted
only for the MPS branch where the runtime API is the canonical
check and the wheel is already cheap.

Lives at module top-level (not under ``embedders/``) so doctor +
wizard can import without dragging the embedder loader stack along.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Accelerator",
    "AcceleratorInfo",
    "EmbedderPreset",
    "detect_accelerator",
    "recommend_embedder_preset",
]


class Accelerator(StrEnum):
    """Hardware lane recommended for embedder serving."""

    CUDA = "cuda"
    MPS = "mps"
    CPU = "cpu"


@dataclass(frozen=True)
class AcceleratorInfo:
    """Detection result.  ``device_name`` and ``vram_mb`` only populated
    for CUDA — MPS / CPU don't expose enumerable equivalents that are
    worth scraping at install time."""

    kind: Accelerator
    device_name: str | None = None
    vram_mb: int | None = None


@dataclass(frozen=True)
class EmbedderPreset:
    """Recommended ``[[embedders]]`` block for the detected hardware."""

    provider: str
    model_id: str
    dimension: int
    n_gpu_layers: int
    n_ctx: int = 8192
    n_seq_max: int = 1
    normalize: bool = True
    distance: str = "cosine"
    extras: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    def to_toml_block(self, *, name: str = "embedder", active: bool = True) -> str:
        """Render the preset as a copy-pasteable ``[[embedders]]`` block.

        The aligned-equals style matches the rest of ``config.toml``
        the wizard emits — purely aesthetic but the doctor's suggest
        path benefits from "looks like the file you're editing"
        signal-to-noise.
        """
        lines = [
            "[[embedders]]",
            f'name       = "{name}"',
            f'provider   = "{self.provider}"',
            f'model_id   = "{self.model_id}"',
            f"dimension  = {self.dimension}",
            f"normalize  = {str(self.normalize).lower()}",
            f'distance   = "{self.distance}"',
            f"active     = {str(active).lower()}",
            f"n_ctx        = {self.n_ctx}",
            f"n_seq_max    = {self.n_seq_max}",
            f"n_gpu_layers = {self.n_gpu_layers}",
        ]
        return "\n".join(lines) + "\n"


# ── Detection ────────────────────────────────────────────────────────

# nvidia-smi field list — comma-separated CSV with no header.  The
# fields are stable across the driver versions corpus-forge has been
# verified against (470+).
_NVIDIA_SMI_FIELDS = "name,memory.total"
_NVIDIA_SMI_TIMEOUT_S = 2.0


def _detect_cuda() -> AcceleratorInfo | None:
    """Probe for a working NVIDIA driver via ``nvidia-smi``.

    Returns ``None`` when ``nvidia-smi`` is absent, hangs, or exits
    non-zero.  The first GPU's name + total VRAM (MB) are captured
    when present so the wizard can pick a model size that fits.
    """
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={_NVIDIA_SMI_FIELDS}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    first_line = completed.stdout.strip().splitlines()[0]
    # Each row is ``"<name>, <memory_mb>"`` — split on the comma but
    # tolerate names that contain commas (rare but contractually legal
    # for OEM-branded boards).  Reversing the rsplit keeps the memory
    # field intact.
    try:
        name, memory_str = first_line.rsplit(",", 1)
    except ValueError:
        return AcceleratorInfo(kind=Accelerator.CUDA)
    try:
        vram_mb = int(memory_str.strip())
    except ValueError:
        vram_mb = None
    return AcceleratorInfo(
        kind=Accelerator.CUDA,
        device_name=name.strip(),
        vram_mb=vram_mb,
    )


def _mps_available() -> bool:
    """True iff PyTorch reports a working MPS (Apple Silicon Metal) device.

    Lazy-imported so this module is safe to call on minimal installs.
    Defined at module level (rather than inline) so tests can patch
    ``corpus_forge.acceleration._mps_available`` to flip the branch
    without touching torch.
    """
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return False
    return bool(torch.backends.mps.is_available())


def detect_accelerator() -> AcceleratorInfo:
    """Return the recommended accelerator lane for this host.

    Priority: CUDA → MPS → CPU.  CUDA wins over MPS even on a
    hypothetical Mac+CUDA box (eGPU; rare but real) because the
    NVIDIA stack has lower latency than the Metal path for embedder
    workloads.  CPU is the universal floor.
    """
    cuda = _detect_cuda()
    if cuda is not None:
        return cuda
    if _mps_available():
        return AcceleratorInfo(kind=Accelerator.MPS)
    return AcceleratorInfo(kind=Accelerator.CPU)


# ── Preset selection ─────────────────────────────────────────────────

# Threshold for picking qwen3-embedding:8b (4096d, ~5 GB at q4_k_m)
# vs nomic-embed-text (768d, ~140 MB).  qwen needs comfortable
# headroom; 8 GB VRAM keeps the model + activations + a typical
# multi-source ingest batch in memory without paging.
_QWEN3_MIN_VRAM_MB = 8 * 1024


def recommend_embedder_preset(info: AcceleratorInfo) -> EmbedderPreset:
    """Pick the embedder block that best matches the detected hardware.

    Three lanes — see module docstring for the rationale.  All three
    use ``provider = "llama-cpp"`` so the cross-host config diff is
    a single ``n_gpu_layers`` line and (for CPU) a smaller model id.
    """
    if info.kind is Accelerator.CUDA:
        vram_ok = info.vram_mb is None or info.vram_mb >= _QWEN3_MIN_VRAM_MB
        if vram_ok:
            return EmbedderPreset(
                provider="llama-cpp",
                model_id="qwen3-embedding:8b",
                dimension=4096,
                n_gpu_layers=-1,
                extras=("llama-cpp",),
                summary=_cuda_summary(info, "qwen3-embedding:8b (4096d)"),
            )
        return EmbedderPreset(
            provider="llama-cpp",
            model_id="nomic-embed-text",
            dimension=768,
            n_gpu_layers=-1,
            extras=("llama-cpp",),
            summary=_cuda_summary(info, "nomic-embed-text (768d, low-VRAM lane)"),
        )
    if info.kind is Accelerator.MPS:
        return EmbedderPreset(
            provider="llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
            n_gpu_layers=-1,
            extras=("llama-cpp",),
            summary="Apple Silicon (Metal) — qwen3-embedding:8b (4096d) via llama-cpp-python.",
        )
    # CPU
    return EmbedderPreset(
        provider="llama-cpp",
        model_id="nomic-embed-text",
        dimension=768,
        n_gpu_layers=0,
        extras=("llama-cpp",),
        summary="No GPU detected — CPU-only lane: nomic-embed-text (768d) via llama-cpp-python.",
    )


def _cuda_summary(info: AcceleratorInfo, model_blurb: str) -> str:
    """Human-readable CUDA summary used by doctor."""
    parts = ["CUDA"]
    if info.device_name:
        parts.append(f"({info.device_name})")
    if info.vram_mb:
        parts.append(f"— {info.vram_mb} MB VRAM")
    parts.append(f"— {model_blurb} via llama-cpp-python with full GPU offload.")
    return " ".join(parts)
