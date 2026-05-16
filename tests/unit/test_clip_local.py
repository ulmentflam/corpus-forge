"""Phase G (G-11) — :class:`ClipLocalEmbedder` unit tests.

The underlying ``SentenceTransformer`` is patched at the class level so
these tests don't actually load CLIP weights.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.embedders.clip_local import ClipLocalEmbedder
from corpus_forge.embedders.multimodal import MultiModalEmbedder, MultiModalUnavailableError


def _make_png_bytes(color: str = "white") -> bytes:
    from PIL import Image

    img = Image.new("RGB", (4, 4), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Protocol surface ────────────────────────────────────────────────────


def test_satisfies_protocol() -> None:
    assert isinstance(ClipLocalEmbedder(), MultiModalEmbedder)


def test_defaults() -> None:
    e = ClipLocalEmbedder()
    assert e.name == "clip_local"
    assert e.model_id == "clip-ViT-B-32"
    assert e.dimension == 512
    assert e.device == "auto"


def test_custom_constructor() -> None:
    e = ClipLocalEmbedder(name="my_clip", model="jina-clip-v2", dimension=1024, device="cpu")
    assert e.name == "my_clip"
    assert e.model_id == "jina-clip-v2"
    assert e.dimension == 1024
    assert e.device == "cpu"


# ── Lazy model loading ─────────────────────────────────────────────────


def test_warmup_loads_model() -> None:
    fake_model = MagicMock()
    fake_st = MagicMock(return_value=fake_model)
    with patch("sentence_transformers.SentenceTransformer", fake_st):
        e = ClipLocalEmbedder(device="cpu")
        assert e._model is None
        e.warmup()
        assert e._model is fake_model
        fake_st.assert_called_once_with("clip-ViT-B-32", device="cpu")


def test_warmup_load_failure_raises_unavailable() -> None:
    with patch(
        "sentence_transformers.SentenceTransformer",
        side_effect=RuntimeError("corrupt cache"),
    ):
        e = ClipLocalEmbedder(device="cpu")
        with pytest.raises(MultiModalUnavailableError, match=r"(?i)load|clip"):
            e.warmup()


# ── encode_text ─────────────────────────────────────────────────────────


def test_encode_text_returns_vectors_of_correct_shape() -> None:
    fake_model = MagicMock()
    fake_model.encode.return_value = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32)
    with patch("sentence_transformers.SentenceTransformer", return_value=fake_model):
        e = ClipLocalEmbedder(dimension=3, device="cpu")
        out = e.encode_text(["a", "b"])
    assert len(out) == 2
    assert out[0] == pytest.approx([0.1, 0.2, 0.3])


def test_encode_text_empty_returns_empty_list() -> None:
    e = ClipLocalEmbedder(device="cpu")
    assert e.encode_text([]) == []


# ── encode_image ────────────────────────────────────────────────────────


def test_encode_image_returns_vectors() -> None:
    fake_model = MagicMock()
    fake_model.encode.return_value = np.array([[0.1, 0.2, 0.3]], dtype=np.float32)
    img_bytes = _make_png_bytes()
    with patch("sentence_transformers.SentenceTransformer", return_value=fake_model):
        e = ClipLocalEmbedder(dimension=3, device="cpu")
        out = e.encode_image([img_bytes])
    assert len(out) == 1
    assert out[0] == pytest.approx([0.1, 0.2, 0.3])


def test_encode_image_empty_returns_empty_list() -> None:
    e = ClipLocalEmbedder(device="cpu")
    assert e.encode_image([]) == []


def test_encode_image_invalid_bytes_raises() -> None:
    fake_model = MagicMock()
    with patch("sentence_transformers.SentenceTransformer", return_value=fake_model):
        e = ClipLocalEmbedder(device="cpu")
        with pytest.raises(MultiModalUnavailableError, match=r"(?i)decode"):
            e.encode_image([b"not a real image"])


def test_encode_image_converts_to_rgb() -> None:
    """RGBA PNGs should be coerced to RGB before passing to the model."""
    from PIL import Image

    fake_model = MagicMock()
    fake_model.encode.return_value = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)

    # Build an RGBA image.
    rgba_img = Image.new("RGBA", (4, 4), (255, 0, 0, 128))
    buf = io.BytesIO()
    rgba_img.save(buf, format="PNG")
    raw = buf.getvalue()

    with patch("sentence_transformers.SentenceTransformer", return_value=fake_model):
        e = ClipLocalEmbedder(dimension=3, device="cpu")
        e.encode_image([raw])

    args, _kwargs = fake_model.encode.call_args
    pil_list = args[0]
    assert pil_list[0].mode == "RGB"
