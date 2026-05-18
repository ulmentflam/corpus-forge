"""Dotted-path resolver for the ``config`` CRUD verbs (Phase L Wave 7).

Three pieces:

- :func:`parse_dotted_key` — tokenize ``"embedders[0].model_id"`` into a
  list of :class:`Token` (kind + key) so the get/set walkers don't have
  to re-parse mid-traversal.
- :func:`get_at_path` / :func:`set_at_path` — walk a ``tomlkit``
  ``TOMLDocument`` (or a plain dict, for tests).  ``set_at_path`` auto-
  creates intermediate tables, which the ``config set`` verb needs when
  the user touches a field whose section hasn't been written yet.
- :func:`coerce_for_field` — convert a CLI-supplied string into the
  Python type the Pydantic field expects.  Handles ``int`` / ``float`` /
  ``bool`` / ``str``, JSON for ``list`` / ``dict``, and the small set of
  union shapes Pydantic typically produces (``X | None``).

The Pydantic shim is intentionally minimal — ``config set`` round-trips
the resulting value through ``Config.load`` so deep validation is the
real authority.  Coercion just needs to be type-correct enough that
``Config(**raw)`` doesn't immediately error on "str where int wanted".
"""

from __future__ import annotations

import json
import re
import types
from dataclasses import dataclass
from typing import Any, get_args, get_origin

from pydantic.fields import FieldInfo

# ── Tokens ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Token:
    """One segment of a dotted path.

    ``kind == "key"``: ``key`` is the table/attribute name (``str``).
    ``kind == "index"``: ``key`` is the integer list index.
    """

    kind: str  # "key" | "index"
    key: str | int


_INDEX_RE = re.compile(r"\[(-?\d+)\]")


def parse_dotted_key(dotted: str) -> list[Token]:
    """Tokenize ``dotted`` into a list of :class:`Token`.

    Accepted shapes::

        "a"                  → [key:a]
        "a.b"                → [key:a, key:b]
        "a[0]"               → [key:a, index:0]
        "a[0].b"             → [key:a, index:0, key:b]
        "a.b.c[2].d"         → [key:a, key:b, key:c, index:2, key:d]
        "datasets[0].sources[1].plugin"
            → [key:datasets, index:0, key:sources, index:1, key:plugin]

    A leading dot or ``[``-without-a-name is a syntax error.  Empty
    string raises.
    """

    if not dotted:
        raise ValueError("parse_dotted_key requires a non-empty key")

    tokens: list[Token] = []
    # Split on "." but keep the bracket sub-expression with its host.
    parts = dotted.split(".")
    for raw_part in parts:
        if not raw_part:
            raise ValueError(f"empty segment in dotted key: {dotted!r}")
        # Split off trailing ``[...]`` index groups.
        bracket_match = _INDEX_RE.search(raw_part)
        if bracket_match is None:
            tokens.append(Token(kind="key", key=raw_part))
            continue
        # Strip everything from the first bracket on; that's the key name.
        first_bracket = raw_part.index("[")
        key_name = raw_part[:first_bracket]
        if not key_name:
            raise ValueError(f"missing key name before brackets in: {raw_part!r}")
        tokens.append(Token(kind="key", key=key_name))
        # Append every ``[N]`` group; reject leftover content.
        cursor = first_bracket
        while cursor < len(raw_part):
            m = _INDEX_RE.match(raw_part, cursor)
            if m is None:
                raise ValueError(f"malformed bracket expression in {raw_part!r}")
            tokens.append(Token(kind="index", key=int(m.group(1))))
            cursor = m.end()
        if cursor != len(raw_part):
            raise ValueError(f"trailing characters in {raw_part!r}")

    return tokens


# ── Get / set walkers ────────────────────────────────────────────────────


class PathNotFound(KeyError):
    """Raised when ``get_at_path`` cannot resolve a token."""


def _get_one(container: Any, token: Token) -> Any:
    if token.kind == "index":
        if not isinstance(container, (list, tuple)):
            raise PathNotFound(
                f"expected list at index {token.key}, got {type(container).__name__}"
            )
        idx = int(token.key)
        try:
            return container[idx]
        except IndexError as exc:
            raise PathNotFound(f"index {idx} out of range") from exc
    # Key access — dict-like.
    if not hasattr(container, "__getitem__"):
        raise PathNotFound(f"cannot index {type(container).__name__} by {token.key!r}")
    try:
        return container[token.key]
    except (KeyError, TypeError) as exc:
        raise PathNotFound(str(token.key)) from exc


def get_at_path(doc: Any, dotted: str) -> Any:
    """Resolve ``dotted`` against ``doc`` and return the leaf value.

    Raises :class:`PathNotFound` (a ``KeyError`` subclass) when any
    segment can't be walked.
    """

    tokens = parse_dotted_key(dotted)
    node: Any = doc
    for token in tokens:
        node = _get_one(node, token)
    return node


def _ensure_table(container: Any, key: str) -> Any:
    """Return the sub-table at ``key``, creating it if missing.

    Works on both ``dict`` and ``tomlkit`` ``Table`` / ``TOMLDocument``.
    """

    if key not in container:
        # tomlkit tables and plain dicts both accept dict assignment;
        # tomlkit will lift a bare dict to an inline table.
        container[key] = {}
    return container[key]


def set_at_path(doc: Any, dotted: str, value: Any) -> None:
    """Assign ``value`` to ``doc`` at ``dotted``.

    Intermediate tables are auto-created when missing; intermediate
    list indices are NOT (out-of-range index → :class:`PathNotFound`,
    because list growth has too many semantics for "auto-create").
    """

    tokens = parse_dotted_key(dotted)
    if not tokens:
        raise ValueError("set_at_path requires at least one token")

    node: Any = doc
    for token in tokens[:-1]:
        # Auto-create intermediate tables for key tokens; list indices
        # MUST already exist (auto-growing a list has too many semantics).
        node = _ensure_table(node, str(token.key)) if token.kind == "key" else _get_one(node, token)

    last = tokens[-1]
    if last.kind == "index":
        if not isinstance(node, list):
            raise PathNotFound(f"cannot index {type(node).__name__} by [{last.key}]")
        idx = int(last.key)
        if idx < 0 or idx >= len(node):
            raise PathNotFound(f"index {idx} out of range for set_at_path")
        node[idx] = value
    else:
        node[last.key] = value


# ── Pydantic-aware coercion ──────────────────────────────────────────────


_TRUTHY = {"true", "yes", "y", "1", "on"}
_FALSY = {"false", "no", "n", "0", "off"}


def _coerce_bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ValueError(f"cannot coerce {raw!r} to bool")


def _unwrap_optional(annotation: Any) -> Any:
    """If ``annotation`` is ``X | None`` / ``Optional[X]``, return ``X``."""

    origin = get_origin(annotation)
    if origin is types.UnionType or str(origin) == "typing.Union":
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def coerce_for_field(value: str, field_info: FieldInfo | None) -> Any:
    """Convert ``value`` (str from the CLI) to the type ``field_info`` declares.

    Resolution:

    - When ``field_info`` is ``None`` we fall back to JSON parsing for
      ``[...]`` / ``{...}`` shapes and return the raw string otherwise.
    - The annotation is unwrapped from ``Optional`` (``X | None``) since
      "empty string → None" is the caller's concern, not coercion's.
    - ``bool`` accepts a small set of canonical truthy/falsy strings
      (``true``/``false``/``yes``/``no``/``y``/``n``/``1``/``0``/``on``/``off``).
    - ``int`` / ``float`` use the standard constructors.
    - ``list`` / ``dict`` parse as JSON.
    - Everything else returns the raw string and lets Pydantic validate.
    """

    annotation = _unwrap_optional(field_info.annotation if field_info else None)

    # No annotation hint — best effort.
    if annotation is None or annotation is Any:
        return _maybe_json(value)

    if annotation is bool:
        return _coerce_bool(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is str:
        return value

    origin = get_origin(annotation)
    if origin in (list, dict, tuple, set):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"expected JSON-encoded {origin.__name__} for {value!r}: {exc}"
            ) from exc
        return parsed

    # Pydantic submodels / unknown class → return raw string; the
    # Config.load round-trip validates it (typical case: AnyHttpUrl).
    return _maybe_json(value)


def _maybe_json(value: str) -> Any:
    """Parse JSON when the string clearly looks like a JSON literal."""

    stripped = value.strip()
    if not stripped:
        return value
    first = stripped[0]
    if first in '{["':
        with _swallow(json.JSONDecodeError):
            return json.loads(stripped)
    # bare ``true`` / ``false`` / ``null`` / numbers — also valid JSON
    # but more useful as strings unless the caller is explicit.
    return value


class _swallow:
    """``contextlib.suppress`` variant that returns from the with-block.

    Used inline by ``_maybe_json`` because the equivalent ``try/except``
    is six lines and obscures the happy path.
    """

    def __init__(self, *exc_types: type[BaseException]) -> None:
        self.exc_types = exc_types

    def __enter__(self) -> _swallow:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, self.exc_types)


__all__ = [
    "PathNotFound",
    "Token",
    "coerce_for_field",
    "get_at_path",
    "parse_dotted_key",
    "set_at_path",
]
