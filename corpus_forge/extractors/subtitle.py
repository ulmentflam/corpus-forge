"""Subtitle extractor.

Phase D / Wave 0 — D-04 (half 2). Pure stdlib.

Handles ``.srt`` (SubRip) and ``.vtt`` (WebVTT) files. Strips cue
indices, ``-->`` timestamp lines, and the ``WEBVTT`` magic header,
leaving only dialogue text. ``chunker_hint = "passthrough"``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import ExtractedDocument

# Matches a cue timestamp line:
#   00:00:01,000 --> 00:00:04,000              (SRT, comma)
#   00:00:01.000 --> 00:00:04.000              (VTT, dot)
#   00:00:01.000 --> 00:00:04.000 line:0%      (VTT, with cue settings)
_TIMESTAMP_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}.*$"
)

# SRT cue indices: a line that is just an integer (optionally surrounded
# by whitespace).
_CUE_INDEX_RE = re.compile(r"^\s*\d+\s*$")

# VTT magic header.
_VTT_HEADER_RE = re.compile(r"^WEBVTT\b.*$")


def _strip_subtitle_metadata(raw: str) -> str:
    """Return only the dialogue portion of an SRT/VTT file."""
    out_lines: list[str] = []
    for line in raw.splitlines():
        if _TIMESTAMP_RE.match(line):
            continue
        if _CUE_INDEX_RE.match(line):
            continue
        if _VTT_HEADER_RE.match(line):
            continue
        out_lines.append(line)

    # Collapse 3+ blank lines into a single blank line and trim leading/
    # trailing whitespace so the output reads as flat prose.
    collapsed: list[str] = []
    blank_run = 0
    for line in out_lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(line)

    text = "\n".join(collapsed).strip()
    return text


class SubtitleExtractor:
    """Strips cue indices + timestamps from ``.srt`` / ``.vtt`` files."""

    supported_extensions: tuple[str, ...] = (".srt", ".vtt")

    def extract(self, path: Path) -> ExtractedDocument:
        raw = path.read_text(encoding="utf-8")
        text = _strip_subtitle_metadata(raw)
        return ExtractedDocument(
            text=text,
            chunker_hint="passthrough",
            language=None,
            metadata={"title": path.stem},
            labels=[],
        )
