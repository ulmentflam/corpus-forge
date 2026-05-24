"""Structured-data extractor.

Phase D / Wave 0 — D-04 (half 1). Pure stdlib.

Handles ``.json .yaml .yml .toml`` files. Pretty-prints when possible
(round-trips through the relevant stdlib parser) and wraps the result in
a fenced code block tagged with the format. Falls back to the raw file
contents when parsing fails so malformed data still flows through the
pipeline.

YAML strategy: ``yaml`` (PyYAML) is *not* a hard dependency. We try
to import it lazily and only use it when present; otherwise we
pretty-print by reading the file verbatim. The fence is always
``yaml`` regardless of code path so downstream chunkers see a stable
format hint.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .base import ExtractedDocument

# Map file extension → (fence label, parser/round-trip strategy).
_FORMAT_BY_EXT: dict[str, str] = {
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}


def _pretty_print_json(raw: str) -> str:
    try:
        data = json.loads(raw)
        return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)
    except (ValueError, TypeError):
        return raw


def _pretty_print_toml(raw: str) -> str:
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return raw

    # tomllib has no dumper. Emit a stable, readable representation by
    # walking the parsed dict — good enough for "wrap in a fence so a
    # human-and-embedder can read it".
    return _stable_repr(data)


def _pretty_print_yaml(raw: str) -> str:
    # Lazy import — PyYAML is not a hard dep. Use it when present for
    # cleaner output; otherwise return raw bytes (already YAML-shaped).
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return raw
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    if data is None:
        return raw
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False).rstrip("\n")


def _stable_repr(value: object, indent: int = 0) -> str:
    """Tiny TOML-ish pretty-printer used when tomllib parses cleanly but
    we have no dumper. Produces ``key = value`` lines and ``[section]``
    headers. Good enough for retrieval; not a TOML round-trip."""
    pad = "  " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        scalars: dict[str, Any] = {}
        tables: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, dict):
                tables[k] = v
            else:
                scalars[k] = v
        for k, v in scalars.items():
            lines.append(f"{pad}{k} = {json.dumps(v, ensure_ascii=False)}")
        for k, v in tables.items():
            lines.append("")
            lines.append(f"{pad}[{k}]")
            lines.append(_stable_repr(v, indent=indent))
        return "\n".join(lines).strip("\n")
    return json.dumps(value, ensure_ascii=False)


class StructuredDataExtractor:
    """Wraps JSON / YAML / TOML in a fenced code block."""

    supported_extensions: tuple[str, ...] = (".json", ".yaml", ".yml", ".toml")

    def extract(self, path: Path) -> ExtractedDocument:
        ext = path.suffix.lower()
        fmt = _FORMAT_BY_EXT.get(ext, "text")
        raw = path.read_text(encoding="utf-8")

        if fmt == "json":
            body = _pretty_print_json(raw)
        elif fmt == "toml":
            body = _pretty_print_toml(raw)
        elif fmt == "yaml":
            body = _pretty_print_yaml(raw)
        else:  # pragma: no cover — guarded by supported_extensions
            body = raw

        fenced = f"```{fmt}\n{body}\n```"
        return ExtractedDocument(
            text=fenced,
            chunker_hint="passthrough",
            language=None,
            metadata={"title": path.stem, "format": fmt},
            labels=[],
        )
