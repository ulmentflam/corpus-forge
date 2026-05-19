"""Phase M Wave 1 — default patterns for the managed ``.corpusignore``
block.

This is a *pure* module — no filesystem I/O at import time and no I/O
inside any of its functions. It is consumed by:

- :mod:`corpus_forge.ignore_lifecycle` (writes the managed block).
- :mod:`corpus_forge.setup.wizard` (renders on setup).
- :mod:`corpus_forge.doctor.checks` (drift detection).
- (Wave 3) the ``corpus-forge ignore`` admin CLI.

Design notes
------------

* The managed block is delimited by two **full-line** sentinel comments
  (``MANAGED_START`` / ``MANAGED_END``). The splicer in
  :mod:`corpus_forge.ignore_lifecycle` requires an exact full-line
  match; substring mentions in user comments are intentionally ignored.
* Pattern selection is **conservative** — we never auto-ignore PDFs,
  notebooks, or source code regardless of feature flags. Heavy media
  (audio/video, RAW images) are gated on whether the user opted into
  the relevant extractor (Whisper, image_extractor).
* ``feature_flags_from_config`` derives the four feature bools from a
  :class:`corpus_forge.config.Config`:

  =================  ====================================================
  flag               source
  =================  ====================================================
  ``whisper``        ``cfg.whisper.backend != "none"``
  ``vlm``            ``cfg.vlm.backend != "none"``
  ``code_enricher``  ``cfg.code_enricher.backend != "none"``
  ``image_extractor`` ``True`` when ``vlm`` is on (the OCR pipeline
                      implies image-aware extraction). Reserved as a
                      separate flag so a future image-only extractor
                      can flip it independently.
  =================  ====================================================
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corpus_forge.config import Config

__all__ = [
    "MANAGED_END",
    "MANAGED_START",
    "default_managed_lines",
    "feature_flags_from_config",
    "parse_managed_lines",
    "render_managed_block",
]


# ── sentinels ─────────────────────────────────────────────────────────

MANAGED_START: str = "# >>> corpus-forge managed (do not edit between sentinels) >>>"
MANAGED_END: str = "# <<< corpus-forge managed <<<"


# ── pattern tuples ────────────────────────────────────────────────────
#
# Each tuple is **sorted** so :func:`default_managed_lines` produces a
# deterministic output across releases. Tests in
# ``tests/unit/test_ignore_defaults.py`` pin this ordering.

# Conservative always-on set:
#   * Apple / macOS metadata (Spotlight, .DS_Store, ._* doubles).
#   * iCloud placeholder files.
#   * Archives (compressed; treat as binary opaque).
#   * Build / output directories (gitignore-style trailing slash).
#   * Lockfiles (no information density worth chunking).
#   * Minified bundles + sourcemaps (text but unstructured).
_ALWAYS_ON: tuple[str, ...] = tuple(
    sorted(
        (
            # Apple / macOS metadata
            ".DS_Store",
            ".Spotlight-V100/",
            "._*",
            "*.icloud",
            # Lockfiles
            "*.lock",
            "Cargo.lock",
            "package-lock.json",
            "pnpm-lock.yaml",
            "poetry.lock",
            "uv.lock",
            "yarn.lock",
            # Minified + sourcemaps
            "*.map",
            "*.min.css",
            "*.min.js",
            # Build / output
            ".next/",
            ".nuxt/",
            "build/",
            "coverage/",
            "dist/",
            "out/",
            "target/",
            # Archives (binary, opaque to text extractors)
            "*.7z",
            "*.dmg",
            "*.iso",
            "*.tar",
            "*.tar.bz2",
            "*.tar.gz",
            "*.tar.xz",
            "*.tgz",
            "*.zip",
        )
    )
)


# Audio + video — auto-ignored only when Whisper is OFF.
_AUDIO_VIDEO: tuple[str, ...] = tuple(
    sorted(
        (
            # Audio
            "*.flac",
            "*.m4a",
            "*.mp3",
            "*.ogg",
            "*.wav",
            # Video
            "*.avi",
            "*.mkv",
            "*.mov",
            "*.mp4",
            "*.webm",
        )
    )
)


# RAW / heavy still-image formats — auto-ignored only when there is no
# image-aware extractor (VLM off and no image_extractor feature).
_RAW_IMAGES: tuple[str, ...] = tuple(
    sorted(
        (
            "*.cr2",
            "*.dng",
            "*.heic",
            "*.heif",
            "*.nef",
            "*.psd",
            "*.raw",
            "*.tif",
            "*.tiff",
        )
    )
)


# ── feature flag derivation ───────────────────────────────────────────


def feature_flags_from_config(cfg: Config) -> dict[str, bool]:
    """Derive ``{whisper, image_extractor, code_enricher, vlm}`` bools.

    Each flag is True when the corresponding backend / extractor is
    *active*. The wizard maps its yes/no answers onto these flags via
    its own mapping table — this helper is the canonical source for the
    rendered-config case.
    """
    whisper_on = cfg.whisper.backend != "none"
    vlm_on = cfg.vlm.backend != "none"
    code_enricher_on = cfg.code_enricher.backend != "none"
    # The OCR pipeline implies image-aware extraction; reserve the flag
    # so future independent image extractors can flip it.
    image_extractor_on = vlm_on
    return {
        "whisper": whisper_on,
        "image_extractor": image_extractor_on,
        "code_enricher": code_enricher_on,
        "vlm": vlm_on,
    }


# ── managed-block composition ─────────────────────────────────────────


def default_managed_lines(features: dict[str, bool]) -> list[str]:
    """Return the deterministic list of patterns for the managed block.

    Composition: always-on + conditional groups based on ``features``.
    Order: ``_ALWAYS_ON`` first, then ``_AUDIO_VIDEO`` (if whisper off),
    then ``_RAW_IMAGES`` (if image_extractor off). Tuples are already
    individually sorted.
    """
    out: list[str] = list(_ALWAYS_ON)
    if not features.get("whisper", False):
        out.extend(_AUDIO_VIDEO)
    if not features.get("image_extractor", False):
        out.extend(_RAW_IMAGES)
    return out


def render_managed_block(features: dict[str, bool], *, include_timestamp: bool = True) -> str:
    """Render the full managed block (sentinels included) as text.

    The block is terminated with a trailing newline so ``splice`` can
    concatenate cleanly. When ``include_timestamp`` is True a single
    ``# Generated ...`` comment line is inserted immediately after the
    opening sentinel — useful for ``corpus-forge ignore validate`` to
    show "managed block generated 3 days ago".
    """
    lines: list[str] = [MANAGED_START]
    if include_timestamp:
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        lines.append(f"# Generated {ts} — do not edit; managed by corpus-forge.")
    lines.extend(default_managed_lines(features))
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def parse_managed_lines(text: str) -> list[str] | None:
    """Extract the lines *between* the two sentinels.

    Returns ``None`` when either sentinel is absent. Returns the body
    lines (excluding the sentinels themselves) as a list of strings in
    file order. Lines that look like comments are preserved as-is —
    callers compare against :func:`default_managed_lines` directly,
    using ``set(...) ⊇ default_managed_lines(...)`` semantics.
    """
    lines = text.splitlines()
    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if line == MANAGED_START and start_idx is None:
            start_idx = i
        elif line == MANAGED_END and start_idx is not None:
            end_idx = i
            break
    if start_idx is None or end_idx is None:
        return None
    return lines[start_idx + 1 : end_idx]
