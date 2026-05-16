"""Phase G — :class:`ClipLocalEmbedder` via ``sentence-transformers``.

In-process multi-modal embedder using ``sentence-transformers`` with
``clip-ViT-B-32`` (default, 512 d) or any CLIP-compatible HF model id.

Both encode methods load the model lazily and run on the device picked
by :func:`_detect_device` (MPS on Apple Silicon, CUDA on NVIDIA, CPU
otherwise). The same model handle serves text and image inputs — that's
what makes them "multi-modal" in the same vector space.
"""

from __future__ import annotations

import io
import logging

from .multimodal import MultiModalUnavailableError

logger = logging.getLogger(__name__)


def _detect_device() -> str:
    """MPS → CUDA → CPU resolution, lazy-importing torch."""
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class ClipLocalEmbedder:
    """Local CLIP-family multi-modal embedder.

    Args:
        name: Stable identifier (default ``"clip_local"``). Used as
            the suffix of the dynamic ``image_embeddings_<name>``
            table.
        model: HF model id. Default ``"clip-ViT-B-32"`` (512 d,
            permissive license, ~150 MB). Other options:
            ``"jina-clip-v2"`` (1024 d, multilingual), or any other
            CLIP variant ``sentence-transformers`` supports.
        dimension: Vector dimensionality. Default 512 matches
            ``clip-ViT-B-32``. Override to 1024 for ``jina-clip-v2``.
        device: ``"auto"``, ``"cpu"``, ``"cuda"``, or ``"mps"``.
    """

    def __init__(
        self,
        *,
        name: str = "clip_local",
        model: str = "clip-ViT-B-32",
        dimension: int = 512,
        device: str = "auto",
    ) -> None:
        self.name = name
        self.model_id = model
        self.dimension = dimension
        self.device = device
        # Lazy-loaded SentenceTransformer handle.
        self._model: object | None = None

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Force-load the model."""
        self._load_model()

    def encode_text(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of text strings."""
        if not texts:
            return []
        self._load_model()
        out = self._model.encode(list(texts), convert_to_numpy=True)  # type: ignore[attr-defined]
        return [v.tolist() for v in out]

    def encode_image(self, images: list[bytes]) -> list[list[float]]:
        """Encode a batch of image bytes.

        sentence-transformers's CLIP image encoder takes PIL ``Image``
        objects; we decode the byte payloads via ``PIL.Image.open`` here.
        """
        if not images:
            return []
        # pyrefly: ignore[missing-import]  # part of [multi-format] / [ocr] extras
        try:
            from PIL import Image  # noqa: PLC0415
        except ImportError as exc:
            raise MultiModalUnavailableError(
                "Pillow is required for image encoding — install the [ocr] extra."
            ) from exc

        self._load_model()
        pil_images = []
        for raw in images:
            try:
                img = Image.open(io.BytesIO(raw))
                img.load()
                # Convert to RGB so CLIP's preprocess accepts every input.
                if img.mode != "RGB":
                    img = img.convert("RGB")
                pil_images.append(img)
            except Exception as exc:
                raise MultiModalUnavailableError(
                    f"Failed to decode image bytes ({len(raw)} bytes): {exc!s}"
                ) from exc

        out = self._model.encode(pil_images, convert_to_numpy=True)  # type: ignore[attr-defined]
        return [v.tolist() for v in out]

    # ── internals ─────────────────────────────────────────────────────

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise MultiModalUnavailableError(
                "sentence-transformers is not installed — it's a hard dep, did the "
                "wheel install correctly?"
            ) from exc

        device = self.device if self.device != "auto" else _detect_device()
        try:
            self._model = SentenceTransformer(self.model_id, device=device)
        except Exception as exc:
            raise MultiModalUnavailableError(
                f"Failed to load CLIP model {self.model_id!r} (device={device}): {exc!s}"
            ) from exc
