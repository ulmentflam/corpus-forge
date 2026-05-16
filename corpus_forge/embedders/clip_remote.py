"""Phase G — :class:`ClipRemoteEmbedder` (OpenAI-compatible HTTP).

Talks to any ``POST {base_url}/embeddings`` endpoint that accepts
multi-modal input (e.g. Voyage AI ``voyage-multimodal-3``, Cohere
``embed-v3-multimodal``, or a self-hosted CLIP service speaking the
same JSON shape).

Request body for text:
    {"model": "<id>", "input": ["t1", "t2", ...]}

Request body for images (base64 data URLs):
    {"model": "<id>", "input": ["data:image/png;base64,...", ...]}

Response:
    {"data": [{"embedding": [...]}, ...]}

Same lazy-``requests`` discipline as :class:`RemoteWhisper` /
:class:`OllamaVLM`.
"""

from __future__ import annotations

import base64
import logging

from .multimodal import (
    MultiModalResponseError,
    MultiModalTimeoutError,
    MultiModalUnavailableError,
)

logger = logging.getLogger(__name__)


def _to_data_url(image_bytes: bytes) -> str:
    """Wrap raw bytes in a ``data:image/<mime>;base64,...`` URL.

    The mime sniff is intentionally minimal — PNG / JPEG / WebP /
    GIF cover the common cases. Anything else falls back to
    ``application/octet-stream``; the remote service decides what to
    do with it.
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
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


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
        import requests  # noqa: PLC0415

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout_s)
        except requests.Timeout as exc:
            raise MultiModalTimeoutError(
                f"Remote embedder exceeded {self.timeout_s}s budget at {url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise MultiModalUnavailableError(
                f"Cannot connect to embedder endpoint at {self.base_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise MultiModalUnavailableError(f"Remote embedder request failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise MultiModalUnavailableError(
                f"Embedder API key rejected (HTTP {resp.status_code}): {(resp.text or '')[:200]}"
            )

        if not resp.ok:
            body_str = (resp.text or "")[:200]
            raise MultiModalResponseError(f"HTTP {resp.status_code}: {body_str}")

        try:
            payload = resp.json()
        except ValueError as exc:
            body_str = (resp.text or "")[:200]
            raise MultiModalResponseError(f"Malformed JSON from embedder: {body_str}") from exc

        if not isinstance(payload, dict) or "data" not in payload:
            raise MultiModalResponseError(
                f"Embedder response missing 'data' key: {str(payload)[:200]}"
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
