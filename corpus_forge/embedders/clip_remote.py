"""Phase G — :class:`ClipRemoteEmbedder` (OpenAI-compatible HTTP).

Talks to any ``POST {base_url}/embeddings`` endpoint that accepts
multi-modal input (e.g. Voyage AI ``voyage-multimodal-3``, Cohere
``embed-v3-multimodal``, or a self-hosted CLIP service speaking the
same JSON shape). Transport-level error mapping is delegated to
:mod:`corpus_forge._http` and shared with every other remote backend
in the repo.

Request body for text:
    {"model": "<id>", "input": ["t1", "t2", ...]}

Request body for images (base64 data URLs):
    {"model": "<id>", "input": ["data:image/png;base64,...", ...]}

Response:
    {"data": [{"embedding": [...]}, ...]}
"""

from __future__ import annotations

import base64
import logging

from corpus_forge._http import HttpErrors, request_json

from .multimodal import (
    MultiModalResponseError,
    MultiModalTimeoutError,
    MultiModalUnavailableError,
)

logger = logging.getLogger(__name__)

_ERR = HttpErrors(MultiModalUnavailableError, MultiModalTimeoutError, MultiModalResponseError)


def _to_data_url(image_bytes: bytes) -> str:
    """Wrap raw bytes in a ``data:image/<mime>;base64,...`` URL.

    The mime sniff is intentionally minimal — PNG / JPEG / WebP / GIF
    cover the common cases. Anything else falls back to
    ``application/octet-stream``.
    """
    if image_bytes.startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        mime = "image/webp"
    elif image_bytes.startswith(b"GIF8"):
        mime = "image/gif"
    else:
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


class ClipRemoteEmbedder:
    """OpenAI-compatible multi-modal embedder backend.

    Args:
        name: Stable identifier; used as the suffix of the dynamic
            ``image_embeddings_<name>`` table.
        base_url: API base. ``"/embeddings"`` is appended.
        model: Provider-specific model id (e.g. ``voyage-multimodal-3``).
        dimension: Expected vector dimensionality.
        api_key: Bearer token.
        timeout_s: Per-request HTTP budget.
    """

    def __init__(
        self,
        *,
        name: str = "clip_remote",
        base_url: str,
        model: str,
        dimension: int,
        api_key: str,
        timeout_s: float = 120.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model_id = model
        self.dimension = dimension
        self.api_key = api_key
        self.timeout_s = timeout_s

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """No-op — remote endpoints don't need preloading."""
        return None

    def encode_text(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._post({"model": self.model_id, "input": list(texts)})

    def encode_image(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        encoded = [_to_data_url(img) for img in images]
        return self._post({"model": self.model_id, "input": encoded})

    # ── internals ─────────────────────────────────────────────────────

    def _post(self, body: dict) -> list[list[float]]:
        payload = request_json(
            "POST",
            f"{self.base_url}/embeddings",
            timeout_s=self.timeout_s,
            errors=_ERR,
            label="Remote embedder",
            base_url=self.base_url,
            api_key=self.api_key,
            json_body=body,
            required_keys=("data",),
        )
        data = payload["data"]
        if not isinstance(data, list):
            raise MultiModalResponseError("Embedder 'data' field is not a list")

        results: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict) or "embedding" not in item:
                raise MultiModalResponseError(
                    f"Embedder data entry missing 'embedding': {str(item)[:100]}"
                )
            emb = item["embedding"]
            if not isinstance(emb, list):
                raise MultiModalResponseError("Embedder 'embedding' is not a list")
            results.append([float(x) for x in emb])
        return results
