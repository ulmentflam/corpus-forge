"""RFC fleet-3 — federation config scope: extract + merge.

corpus-forge config is machine-local, but parts of it are
*corpus-shaped*: dataset names/kinds, embedder definitions, retrieval
settings, model choices. This module is the scope layer those
federation verbs (``config publish`` / ``config pull``, later RFC
slices) are built on:

- :func:`shared_scope_dict` extracts the SHARED subset of a
  :class:`~corpus_forge.config.Config` as a plain nested dict that
  mirrors the TOML structure.
- :func:`merge_shared_scope` rewrites *only* shared-scope keys inside
  a local ``config.toml`` text via tomlkit, preserving comments and
  key ordering — the operator's annotations survive a pull.

Scope is declared on the pydantic models themselves:
``Field(json_schema_extra={"scope": "shared"})``. **Anything
unannotated is local** — new fields are private-by-default, so a
forgotten annotation can never leak a path or secret into the shared
corpus row. The structural deny-list test in
``tests/unit/test_config_scope.py`` enforces that invariant shape-wise
(no path-shaped or secret-shaped field name may carry the shared
mark).

Array-of-table sections (``[[datasets]]``, ``[[embedders]]``) merge by
the item's ``name`` key: matching local entries are updated in place
(shared keys only), unmatched shared entries are appended. An appended
dataset arrives with name/kind but no local ``sources`` — the merged
TOML is textually valid but needs the operator to add sources before
it loads (each machine ingests its own directories; that's a feature,
per the RFC).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import TYPE_CHECKING

import tomlkit
from pydantic import BaseModel
from pydantic.fields import FieldInfo

if TYPE_CHECKING:
    from corpus_forge.config import Config

#: Field-metadata key + value marking a field as fleet-shared.
SCOPE_KEY = "scope"
SCOPE_SHARED = "shared"

#: TOML array-of-table items are matched across hosts by this key.
_IDENTITY_KEY = "name"

# A nested dict mirroring the TOML structure: tables are dicts,
# arrays-of-tables are lists of dicts, leaves are TOML-compatible
# primitives (the output of ``model_dump(mode="json")``).
SharedScope = dict[str, object]


def field_is_shared(field: FieldInfo) -> bool:
    """True when ``field`` carries the ``scope: shared`` annotation."""
    extra = field.json_schema_extra
    return isinstance(extra, dict) and extra.get(SCOPE_KEY) == SCOPE_SHARED


def _extract(model: BaseModel) -> SharedScope:
    """Recursively collect the shared-scope subset of ``model``.

    Nested models recurse unconditionally — their own field
    annotations decide what surfaces. A nested model (or list of
    models) contributing nothing is omitted entirely, so purely-local
    blocks like ``[backend]`` and ``[daemon]`` never appear in the
    shared dict, not even as empty tables.
    """
    dumped = model.model_dump(mode="json")
    out: SharedScope = {}
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            sub = _extract(value)
            if sub:
                out[name] = sub
        elif isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            subs = [s for s in (_extract(v) for v in value) if s]
            if subs:
                out[name] = subs
        elif field_is_shared(field) and dumped[name] is not None:
            # ``None`` leaves are omitted: TOML has no null, and "the
            # fleet has no opinion" and "the key is absent" are the
            # same statement for an optional shared field.
            out[name] = dumped[name]
    return out


def shared_scope_dict(config: Config) -> SharedScope:
    """Extract the fleet-shared subset of ``config`` as a plain dict.

    The result mirrors the TOML structure (``datasets`` /
    ``embedders`` as lists of dicts, ``retrieval`` etc. as nested
    dicts) and contains only TOML-representable primitives — it is the
    body that ``config publish`` versions into ``corpus.shared_config``
    in the next RFC slice.
    """
    return _extract(config)


# Both ``tomlkit.TOMLDocument`` and ``tomlkit.items.Table`` are dict
# subclasses, so one MutableMapping bound covers tables at any depth.
_TomlTable = MutableMapping[str, object]


def _merge_table(container: _TomlTable, shared: SharedScope) -> None:
    for key, value in shared.items():
        if isinstance(value, dict):
            if not isinstance(container.get(key), MutableMapping):
                container[key] = tomlkit.table()
            # tomlkit returns the live node; mutate it in place.
            sub = container[key]
            assert isinstance(sub, MutableMapping)
            _merge_table(sub, value)
        elif isinstance(value, list) and value:
            dict_items = [v for v in value if isinstance(v, dict)]
            if len(dict_items) == len(value):
                _merge_aot(container, key, dict_items)
            else:
                container[key] = value
        else:
            container[key] = value


def _merge_aot(container: _TomlTable, key: str, shared_items: list[dict[str, object]]) -> None:
    """Merge an array-of-tables by ``name`` identity.

    Local items keep every local-scope key (and their comments);
    shared keys are overwritten. Shared items with no local
    counterpart are appended as fresh tables.
    """
    existing = container.get(key)
    if not isinstance(existing, list):  # tomlkit's AoT subclasses list
        existing = tomlkit.aot()
        container[key] = existing
    by_name: dict[object, _TomlTable] = {
        item[_IDENTITY_KEY]: item
        for item in existing
        if isinstance(item, MutableMapping) and item.get(_IDENTITY_KEY) is not None
    }
    for raw in shared_items:
        local_item = by_name.get(raw.get(_IDENTITY_KEY))
        if local_item is not None:
            _merge_table(local_item, raw)
        else:
            fresh = tomlkit.table()
            _merge_table(fresh, raw)
            existing.append(fresh)


def merge_shared_scope(local_toml_text: str, shared: SharedScope) -> str:
    """Rewrite only shared-scope keys inside ``local_toml_text``.

    Parses with tomlkit (comments and ordering survive), applies
    ``shared`` on top — shared keys are set, missing tables/items are
    added, **local-scope keys are never touched** — and returns the
    rewritten TOML text. Pure function: no file IO, no validation;
    callers (the ``config pull`` verb, next slice) decide where the
    text comes from and whether the result must load as a
    :class:`~corpus_forge.config.Config`.
    """
    doc = tomlkit.parse(local_toml_text)
    _merge_table(doc, shared)
    return tomlkit.dumps(doc)
