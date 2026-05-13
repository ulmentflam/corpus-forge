"""Phase BR-03 banner / logo asset pins.

Validates the SVG assets shipped under ``assets/``:

* ``assets/banner.svg`` — light banner (1280x320-ish viewBox).
* ``assets/banner-dark.svg`` — dark variant.
* ``assets/logo.svg`` — square mark (256x256 or 512x512).

Each is asserted to:

* exist
* parse as XML
* have an ``<svg>`` root with a ``viewBox`` attribute

The two banner SVGs additionally must contain the wordmark "corpus-forge"
and a tagline referencing the training-corpus mission.

``assets/banner.png`` is best-effort — if a renderer is available locally
or in CI, BR-03 produces it; this suite only validates the magic bytes
when the file is present.

Reference: master plan §Phase BR (banner direction = anvil/forge + dataflow).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS = REPO_ROOT / "assets"

SVG_NS = "{http://www.w3.org/2000/svg}"


def _load_svg(path: Path) -> ET.Element:
    assert path.exists(), f"Missing {path.relative_to(REPO_ROOT)}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"Empty SVG: {path.relative_to(REPO_ROOT)}"
    root = ET.fromstring(text)
    # ElementTree strips the namespace from the tag; accept either form.
    tag = root.tag.lower()
    assert tag in {"svg", f"{SVG_NS}svg"}, f"Root tag must be <svg>, got {tag!r}"
    return root


def _all_text(root: ET.Element) -> str:
    out: list[str] = []
    for el in root.iter():
        if el.text:
            out.append(el.text)
        if el.tail:
            out.append(el.tail)
    return " ".join(out)


@pytest.fixture(scope="module")
def banner_light() -> ET.Element:
    return _load_svg(ASSETS / "banner.svg")


@pytest.fixture(scope="module")
def banner_dark() -> ET.Element:
    return _load_svg(ASSETS / "banner-dark.svg")


@pytest.fixture(scope="module")
def logo() -> ET.Element:
    return _load_svg(ASSETS / "logo.svg")


# ── light banner ──────────────────────────────────────────────────────────


def test_banner_light_has_viewbox(banner_light: ET.Element) -> None:
    assert "viewBox" in banner_light.attrib, "banner.svg must declare a viewBox"


def test_banner_light_contains_wordmark(banner_light: ET.Element) -> None:
    text = _all_text(banner_light)
    assert "corpus-forge" in text, "banner.svg must include the wordmark"


def test_banner_light_contains_tagline(banner_light: ET.Element) -> None:
    text = _all_text(banner_light).lower()
    assert "forge" in text and "training corpus" in text, (
        "banner.svg must include the training-corpus tagline"
    )


# ── dark banner ───────────────────────────────────────────────────────────


def test_banner_dark_has_viewbox(banner_dark: ET.Element) -> None:
    assert "viewBox" in banner_dark.attrib, "banner-dark.svg must declare a viewBox"


def test_banner_dark_contains_wordmark(banner_dark: ET.Element) -> None:
    text = _all_text(banner_dark)
    assert "corpus-forge" in text, "banner-dark.svg must include the wordmark"


def test_banner_dark_contains_tagline(banner_dark: ET.Element) -> None:
    text = _all_text(banner_dark).lower()
    assert "forge" in text and "training corpus" in text, (
        "banner-dark.svg must include the training-corpus tagline"
    )


# ── logo ──────────────────────────────────────────────────────────────────


def test_logo_has_viewbox(logo: ET.Element) -> None:
    assert "viewBox" in logo.attrib, "logo.svg must declare a viewBox"


def test_logo_is_square_aspect(logo: ET.Element) -> None:
    """logo.svg viewBox should be square (1:1)."""
    vb = logo.attrib["viewBox"].split()
    assert len(vb) == 4, "viewBox must be 'minx miny w h'"
    _, _, w, h = map(float, vb)
    assert w == h, f"logo.svg viewBox should be square; got {w}x{h}"


# ── optional PNG fallback ─────────────────────────────────────────────────


def test_banner_png_optional_but_valid_when_present() -> None:
    """If banner.png is rendered, the first 8 bytes must be a PNG signature."""
    png = ASSETS / "banner.png"
    if not png.exists():
        pytest.skip(
            "assets/banner.png not rendered (no svg renderer available); SVG fallback is acceptable"
        )
    with png.open("rb") as fh:
        signature = fh.read(8)
    assert signature == b"\x89PNG\r\n\x1a\n", (
        f"assets/banner.png must begin with the PNG magic; got {signature!r}"
    )
