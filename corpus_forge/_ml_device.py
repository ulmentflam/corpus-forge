"""Shared device-detection helper for sentence-transformers-style backends.

Four call sites used to roll the same MPS → CUDA → CPU heuristic by
hand: :class:`SentenceTransformersEmbedder`, :class:`ClipLocalEmbedder`,
:class:`LocalWhisper`, and :class:`CrossEncoderReranker`. They now all
call :func:`detect_device`.

The single subtlety is :class:`LocalWhisper`: ``faster-whisper`` does
not yet support the MPS backend, so it disables the MPS branch via
``prefer_mps=False``.

``torch`` is imported lazily so this module is safe to import in
environments where the ML stack isn't installed (it returns ``"cpu"``
unconditionally in that case).
"""

from __future__ import annotations

__all__ = ["detect_device", "resolve_device"]

_AUTO = "auto"


def detect_device(*, prefer_mps: bool = True) -> str:
    """Pick the best available concrete device.

    Args:
        prefer_mps: When True (default), Apple Silicon's Metal backend
            is preferred when available. Set False for libraries that
            don't yet support MPS (faster-whisper).

    Returns:
        ``"mps"`` (when ``prefer_mps`` and available), ``"cuda"``, or
        ``"cpu"``. Falls back to ``"cpu"`` when ``torch`` isn't
        importable so callers can still run on hosts without the ML
        stack installed.
    """
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return "cpu"
    if prefer_mps and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def resolve_device(device: str, *, prefer_mps: bool = True) -> str:
    """Translate the ``"auto"`` sentinel into a concrete device.

    Any other value is returned unchanged so callers can pass through
    user-specified ``"cpu"`` / ``"cuda"`` / ``"mps"`` strings.
    """
    if device == _AUTO:
        return detect_device(prefer_mps=prefer_mps)
    return device
