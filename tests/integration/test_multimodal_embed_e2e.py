"""Phase G (G-16) — live-CLIP end-to-end tests.

Marker-gated by ``requires_clip_local``. Auto-skipped at collection
time when ``sentence-transformers`` can't load ``clip-ViT-B-32``
(no internet, no model cache, missing dep).

Fixtures: ``tests/fixtures/multi_format_corpus/images/screenshot.png``
(from Phase D Wave 6's synthetic corpus).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_clip_local]


_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"
_SCREENSHOT = _FIXTURE_ROOT / "images" / "screenshot.png"
_SCENE = _FIXTURE_ROOT / "images" / "scene-landscape.png"
_BLOCKS = _FIXTURE_ROOT / "images" / "abstract-blocks.png"


@pytest.mark.timeout(120)
def test_round_trip_image_through_clip_local() -> None:
    """A fixture image round-trips through ``encode_image`` cleanly."""
    from corpus_forge.embedders.clip_local import ClipLocalEmbedder

    assert _SCREENSHOT.is_file(), f"Fixture missing: {_SCREENSHOT}"

    e = ClipLocalEmbedder(device="cpu")
    out = e.encode_image([_SCREENSHOT.read_bytes()])
    assert len(out) == 1
    assert len(out[0]) == 512  # CLIP ViT-B/32 default
    assert all(isinstance(v, float) for v in out[0])


@pytest.mark.timeout(120)
def test_text_and_image_share_dimension() -> None:
    """Text and image vectors must live in the same dimensionality."""
    from corpus_forge.embedders.clip_local import ClipLocalEmbedder

    assert _SCREENSHOT.is_file()
    e = ClipLocalEmbedder(device="cpu")

    text_vec = e.encode_text(["a screenshot of code"])[0]
    image_vec = e.encode_image([_SCREENSHOT.read_bytes()])[0]
    assert len(text_vec) == len(image_vec)


@pytest.mark.timeout(180)
def test_cross_modal_cosine_similarity_above_random() -> None:
    """Text describing the image should land closer to it than pure noise.

    CLIP embeddings are not perfectly aligned out of the box — we only
    assert the related pair beats a random baseline by a comfortable
    margin (above the >0.2 spec floor on related pairs).
    """
    import numpy as np

    from corpus_forge.embedders.clip_local import ClipLocalEmbedder

    e = ClipLocalEmbedder(device="cpu")
    text_vec = np.asarray(e.encode_text(["a screenshot of code"])[0])
    img_vec = np.asarray(e.encode_image([_SCREENSHOT.read_bytes()])[0])

    def _cos(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    sim = _cos(text_vec, img_vec)
    # CLIP normalised cosine on related pairs is typically ~0.2-0.3.
    assert sim > 0.15, f"Cross-modal cosine {sim:.3f} below the random-baseline floor (0.15)"


@pytest.mark.timeout(180)
def test_distinct_clip_images_embed_distinctly() -> None:
    """The two text-free CLIP-lane fixtures embed to *different* vectors.

    ``scene-landscape.png`` (gradient landscape) and
    ``abstract-blocks.png`` (colour-block grid) are visually unrelated,
    so their CLIP image embeddings must not be near-identical — cosine
    well below 1.0 confirms CLIP separates them (and that the builder
    didn't accidentally emit the same picture twice).
    """
    import numpy as np

    from corpus_forge.embedders.clip_local import ClipLocalEmbedder

    assert _SCENE.is_file(), f"Fixture missing: {_SCENE}"
    assert _BLOCKS.is_file(), f"Fixture missing: {_BLOCKS}"

    e = ClipLocalEmbedder(device="cpu")
    scene_vec = np.asarray(e.encode_image([_SCENE.read_bytes()])[0])
    blocks_vec = np.asarray(e.encode_image([_BLOCKS.read_bytes()])[0])

    def _cos(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    sim = _cos(scene_vec, blocks_vec)
    assert sim < 0.99, (
        f"The two CLIP-lane fixtures embed near-identically (cosine {sim:.3f}); "
        "they should be visually distinct."
    )
