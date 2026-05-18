"""Tests for :mod:`corpus_forge.admin._path` (Phase L Wave 7).

Three regions:

1. ``parse_dotted_key`` — every accepted shape + every error shape.
2. ``get_at_path`` / ``set_at_path`` — round-trips against both dict
   inputs and tomlkit documents.
3. ``coerce_for_field`` — Pydantic ``FieldInfo`` introspection.
"""

from __future__ import annotations

import json

import pytest
import tomlkit
from pydantic import BaseModel, Field

from corpus_forge.admin import _path
from corpus_forge.admin._path import (
    PathNotFound,
    Token,
    coerce_for_field,
    get_at_path,
    parse_dotted_key,
    set_at_path,
)

# ── parse_dotted_key ─────────────────────────────────────────────────────


def test_parse_dotted_key_single_token() -> None:
    assert parse_dotted_key("a") == [Token("key", "a")]


def test_parse_dotted_key_dot_chain() -> None:
    assert parse_dotted_key("a.b") == [Token("key", "a"), Token("key", "b")]


def test_parse_dotted_key_with_index() -> None:
    assert parse_dotted_key("a[0]") == [Token("key", "a"), Token("index", 0)]


def test_parse_dotted_key_index_then_dot() -> None:
    assert parse_dotted_key("a[0].b") == [
        Token("key", "a"),
        Token("index", 0),
        Token("key", "b"),
    ]


def test_parse_dotted_key_deep_chain() -> None:
    assert parse_dotted_key("a.b.c[2].d") == [
        Token("key", "a"),
        Token("key", "b"),
        Token("key", "c"),
        Token("index", 2),
        Token("key", "d"),
    ]


def test_parse_dotted_key_multiple_indices() -> None:
    assert parse_dotted_key("matrix[1][2]") == [
        Token("key", "matrix"),
        Token("index", 1),
        Token("index", 2),
    ]


def test_parse_dotted_key_realistic_config_path() -> None:
    assert parse_dotted_key("datasets[0].sources[1].plugin") == [
        Token("key", "datasets"),
        Token("index", 0),
        Token("key", "sources"),
        Token("index", 1),
        Token("key", "plugin"),
    ]


def test_parse_dotted_key_empty_string_raises() -> None:
    with pytest.raises(ValueError):
        parse_dotted_key("")


def test_parse_dotted_key_leading_dot_raises() -> None:
    with pytest.raises(ValueError):
        parse_dotted_key(".a")


def test_parse_dotted_key_bare_brackets_raises() -> None:
    with pytest.raises(ValueError):
        parse_dotted_key("[0]")


def test_parse_dotted_key_trailing_garbage_raises() -> None:
    with pytest.raises(ValueError):
        parse_dotted_key("a[0]junk")


# ── get_at_path ──────────────────────────────────────────────────────────


def test_get_at_path_dict_round_trip() -> None:
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert get_at_path(doc, "a.b[0].c") == 1
    assert get_at_path(doc, "a.b[1].c") == 2


def test_get_at_path_missing_key_raises() -> None:
    doc = {"a": {"b": 1}}
    with pytest.raises(PathNotFound):
        get_at_path(doc, "a.c")


def test_get_at_path_index_out_of_range_raises() -> None:
    doc = {"a": [1]}
    with pytest.raises(PathNotFound):
        get_at_path(doc, "a[2]")


def test_get_at_path_tomlkit_round_trip() -> None:
    toml = tomlkit.parse(
        """
[backend]
kind = "sqlite"
dsn = "/tmp/a"

[[embedders]]
name = "qwen3_8b"
provider = "sentence_transformers"
dimension = 4096
"""
    )
    assert get_at_path(toml, "backend.kind") == "sqlite"
    assert get_at_path(toml, "embedders[0].name") == "qwen3_8b"
    assert get_at_path(toml, "embedders[0].dimension") == 4096


def test_get_at_path_nested_array_of_tables() -> None:
    toml = tomlkit.parse(
        """
[[datasets]]
name = "default"
[[datasets.sources]]
plugin = "filesystem"
root = "~/notes"
"""
    )
    assert get_at_path(toml, "datasets[0].sources[0].plugin") == "filesystem"


# ── set_at_path ──────────────────────────────────────────────────────────


def test_set_at_path_dict_modifies_in_place() -> None:
    doc: dict = {"a": {"b": 1}}
    set_at_path(doc, "a.b", 99)
    assert doc["a"]["b"] == 99


def test_set_at_path_dict_auto_creates_intermediate_tables() -> None:
    doc: dict = {}
    set_at_path(doc, "a.b.c", "value")
    assert doc == {"a": {"b": {"c": "value"}}}


def test_set_at_path_dict_index_in_path() -> None:
    doc: dict = {"a": [{"b": 1}, {"b": 2}]}
    set_at_path(doc, "a[1].b", 999)
    assert doc["a"][1]["b"] == 999


def test_set_at_path_list_index_out_of_range_raises() -> None:
    doc: dict = {"a": [1]}
    with pytest.raises(PathNotFound):
        set_at_path(doc, "a[5]", 999)


def test_set_at_path_tomlkit_preserves_other_keys() -> None:
    text = """\
# top-level comment
[backend]
kind = "sqlite"
dsn = "/tmp/a"

[[embedders]]
name = "qwen3_8b"
dimension = 4096
"""
    toml = tomlkit.parse(text)
    set_at_path(toml, "backend.kind", "postgres")
    set_at_path(toml, "embedders[0].dimension", 1024)
    rendered = tomlkit.dumps(toml)
    # Comment survives.
    assert "top-level comment" in rendered
    # Values updated.
    assert 'kind = "postgres"' in rendered
    assert "dimension = 1024" in rendered


def test_set_at_path_round_trip_with_get() -> None:
    doc: dict = {"a": {"b": [{"c": 1}]}}
    set_at_path(doc, "a.b[0].c", 42)
    assert get_at_path(doc, "a.b[0].c") == 42


# ── coerce_for_field ────────────────────────────────────────────────────


class _Sample(BaseModel):
    flag: bool = False
    count: int = 0
    rate: float = 0.0
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    extras: dict = Field(default_factory=dict)
    maybe: int | None = None


def _field(name: str):
    return _Sample.model_fields[name]


def test_coerce_bool_truthy_falsy() -> None:
    f = _field("flag")
    assert coerce_for_field("true", f) is True
    assert coerce_for_field("Yes", f) is True
    assert coerce_for_field("1", f) is True
    assert coerce_for_field("ON", f) is True
    assert coerce_for_field("false", f) is False
    assert coerce_for_field("no", f) is False
    assert coerce_for_field("0", f) is False
    assert coerce_for_field("Off", f) is False


def test_coerce_bool_invalid_raises() -> None:
    with pytest.raises(ValueError):
        coerce_for_field("maybe", _field("flag"))


def test_coerce_int() -> None:
    assert coerce_for_field("42", _field("count")) == 42


def test_coerce_int_bad_raises() -> None:
    with pytest.raises(ValueError):
        coerce_for_field("not-a-number", _field("count"))


def test_coerce_float() -> None:
    assert coerce_for_field("0.75", _field("rate")) == 0.75


def test_coerce_str_passthrough() -> None:
    assert coerce_for_field("hello", _field("name")) == "hello"


def test_coerce_list_via_json() -> None:
    assert coerce_for_field('["a", "b"]', _field("tags")) == ["a", "b"]


def test_coerce_dict_via_json() -> None:
    assert coerce_for_field('{"k": 1}', _field("extras")) == {"k": 1}


def test_coerce_list_bad_json_raises() -> None:
    with pytest.raises(ValueError):
        coerce_for_field("[unclosed", _field("tags"))


def test_coerce_optional_unwraps_to_int() -> None:
    assert coerce_for_field("17", _field("maybe")) == 17


def test_coerce_no_field_info_falls_back_to_string() -> None:
    # No annotation hint — returns the raw string for a non-JSON value.
    assert coerce_for_field("plain-text", None) == "plain-text"


def test_coerce_no_field_info_parses_json_brackets() -> None:
    # JSON-looking input is decoded eagerly.
    out = coerce_for_field("[1, 2, 3]", None)
    assert out == [1, 2, 3]


# Defensive — make sure we don't drag a private helper into __all__.


def test_module_exports_public_only() -> None:
    public = set(_path.__all__)
    assert "PathNotFound" in public
    assert "parse_dotted_key" in public
    assert "get_at_path" in public
    assert "set_at_path" in public
    assert "coerce_for_field" in public
    assert "Token" in public


def test_coerce_does_not_double_decode_json_strings() -> None:
    # When a string field is given JSON-looking content, we still return
    # the raw string — Pydantic decides what it means.
    raw = json.dumps({"x": 1})
    assert coerce_for_field(raw, _field("name")) == raw
