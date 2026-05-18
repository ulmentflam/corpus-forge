"""Secret redaction for the bug-report bundler (Phase L Wave 6).

Three surfaces:

- :func:`redact_string` — run every compiled pattern over a string,
  return ``(text, count)``.  Idempotent: a second pass replaces nothing.
- :func:`redact_toml_dict` — walk a ``tomlkit`` document, replace the
  *value* at any key whose name matches the secret-key shape with the
  literal ``«redacted»``.  Preserves comments and ordering.
- :func:`redact_file` — read / sweep / atomic-write back, returns the
  replacement count.

The literal replacement marker is the Unicode guillemets ``«redacted»``
so a single ``grep '«redacted»'`` over a bundle locates every site.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from tomlkit.items import AoT, InlineTable, Table
from tomlkit.toml_document import TOMLDocument

# Replacement marker — Unicode guillemets so it doesn't collide with
# innocuous ASCII strings in user data.
REDACTED: str = "«redacted»"


# ── String patterns (compiled once at module load) ─────────────────────


# 1. Connection strings — postgres / mysql / mongodb / redis, with or
#    without `+driver`, host required after `@`.  We deliberately
#    require the `@` so plain `redis://localhost` (no creds) is left
#    alone.
_DSN_RE = re.compile(
    r"(?:postgres(?:ql)?|mysql|mongodb|redis)(?:\+\w+)?://[^/\s@]+@\S+",
    re.IGNORECASE,
)

# 2. OpenAI-style ``sk-XXXX...``.
_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")

# 3. xAI-style ``xai-XXXX...``.
_XAI_KEY_RE = re.compile(r"xai-[A-Za-z0-9_\-]{16,}")

# 4. Anthropic-style ``claude-XXXX...``.  Token shape is opportunistic
#    — Anthropic's official keys use ``sk-ant-…`` (caught by
#    :data:`_OPENAI_KEY_RE`), but third-party libraries sometimes mint
#    `claude-` prefixed values.
_CLAUDE_KEY_RE = re.compile(r"claude-[A-Za-z0-9_\-]{16,}")

# 5. Generic ``api_key=``, ``password=``, ``secret=`` (and ``-`` /
#    space-separator variants) — case-insensitive.
_GENERIC_KV_RE = re.compile(
    r"(?i)(api[_-]?key|password|secret)\s*[=:]\s*[\"']?([^\s\"']+)[\"']?",
)

# 6. ``Bearer <token>`` — exact spec from RFC 6750.
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+")


# Order matters: more-specific patterns first so a single value isn't
# triple-counted.  The DSN sweep happens before the generic kv sweep
# because a URL with ``password=`` inside would otherwise be matched
# twice.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    _DSN_RE,
    _OPENAI_KEY_RE,
    _XAI_KEY_RE,
    _CLAUDE_KEY_RE,
    _BEARER_RE,
    _GENERIC_KV_RE,
)


# ── TOML key-name match ───────────────────────────────────────────────


_SECRET_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i).*dsn.*"),
    re.compile(r"(?i).*password.*"),
    re.compile(r"(?i).*api[_-]?key.*"),
    re.compile(r"(?i).*secret.*"),
    re.compile(r"(?i).*token.*"),
)


def _is_secret_key(name: str) -> bool:
    """Return True iff ``name`` (a TOML key) names a secret value."""
    return any(p.fullmatch(name) for p in _SECRET_KEY_PATTERNS)


# ── Public API ────────────────────────────────────────────────────────


def redact_string(s: str) -> tuple[str, int]:
    """Sweep ``s`` for secret patterns; return ``(redacted, count)``.

    Idempotent: a second pass over an already-redacted string returns
    the same string with ``count == 0``.  The replacement marker
    :data:`REDACTED` (Unicode ``«redacted»``) contains no characters
    matched by any pattern, so re-sweeping is a no-op.
    """

    if not s:
        return s, 0

    total = 0
    out = s
    for pattern in _PATTERNS:
        out, n = pattern.subn(REDACTED, out)
        total += n
    return out, total


def redact_toml_dict(doc: TOMLDocument | Table | InlineTable | AoT) -> tuple[Any, int]:
    """Walk ``doc`` and redact string values at secret-named keys.

    Preserves comments and ordering by editing the tomlkit AST in
    place.  Returns ``(doc, count)`` where ``count`` is the number of
    string values replaced.
    """

    count = _walk_toml(doc)
    return doc, count


def _walk_toml(node: Any) -> int:
    """Recursive helper for :func:`redact_toml_dict`."""

    total = 0

    if isinstance(node, AoT):
        for item in node:
            total += _walk_toml(item)
        return total

    # ``TOMLDocument`` IS-A ``Container``; ``Table`` exposes ``.items()``.
    # ``InlineTable`` likewise.
    if isinstance(node, (TOMLDocument, Table, InlineTable)):
        for key, value in list(node.items()):
            key_name = str(key)
            # Nested table / inline / array-of-tables: recurse.
            if isinstance(value, (Table, InlineTable, AoT)):
                total += _walk_toml(value)
                continue
            # Scalar string at a secret-named key → redact.
            if isinstance(value, str) and _is_secret_key(key_name):
                node[key_name] = REDACTED
                total += 1
                continue
            # Plain string at a non-secret key → leave it; the
            # bug-report code calls ``redact_string`` over rendered
            # blobs separately for value-based pattern matches.

    return total


def redact_file(path: Path) -> int:
    """Read ``path`` (utf-8), sweep, atomically write back; return count.

    Uses ``errors="replace"`` so binary fragments / mojibake don't
    break the sweep.  Write is atomic — tempfile + rename — so a
    crashed redaction never leaves a half-overwritten config on disk.
    """

    src_text = path.read_text(encoding="utf-8", errors="replace")
    out_text, count = redact_string(src_text)
    if count == 0:
        return 0

    # Atomic write — tempfile next to the target then rename.
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=path.name + ".", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(out_text)
        tmp_path.replace(path)
    except Exception:
        # Clean up the tempfile on failure.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    return count


__all__ = [
    "REDACTED",
    "redact_file",
    "redact_string",
    "redact_toml_dict",
]
