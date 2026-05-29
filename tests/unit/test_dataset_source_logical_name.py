"""DR-T1 (RED) — DatasetSourceConfig.logical_name Pydantic field + validation.

Contract source: .planning/tdd/tasks.md §DR-T1 and design clause §C2.

Field spec (C2):
  - Type: str | None = None
  - Default: None (current path-based behavior unchanged)
  - When non-None, must match ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$
    (POSIX-safe identifier, 1-64 chars total)
  - Empty string must be rejected (use None to disable)
  - Sits at the bottom of DatasetSourceConfig, after max_bytes

RED state: logical_name is not yet a field on DatasetSourceConfig.
Accept tests will fail with:
  pydantic_core._pydantic_core.ValidationError: Extra inputs are not permitted
Read tests (attribute access) will fail with AttributeError.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_SOURCE_KWARGS: dict = {
    "plugin": "filesystem",
    "root": "/tmp/foo",
    "chunker": "markdown",
}

_BASE_TOML_TMPL = """\
[backend]
kind = "postgres"
dsn  = "postgresql://user:pass@localhost/forge"

[daemon]

[[datasets]]
name = "test-ds"
kind = "text"
  [[datasets.sources]]
  plugin  = "filesystem"
  root    = "/tmp/x"
  chunker = "markdown"
{extra_source}

[[embedders]]
name      = "e"
provider  = "sentence_transformers"
model_id  = "m"
dimension = 1
"""


def _load_config(toml_text: str, tmp_path: Path):
    """Write TOML to a temp file and load it via Config.load."""
    from corpus_forge.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent(toml_text), encoding="utf-8")
    return Config.load(config_path=cfg_path)


# ---------------------------------------------------------------------------
# 1. Import smoke
# ---------------------------------------------------------------------------


class TestDatasetSourceConfigImport:
    """DatasetSourceConfig must remain importable (no regression)."""

    def test_dataset_source_config_importable(self) -> None:
        from corpus_forge.config import DatasetSourceConfig  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Default value — logical_name is None
# ---------------------------------------------------------------------------


class TestLogicalNameDefault:
    """logical_name defaults to None when not supplied."""

    def test_logical_name_default_is_none(self) -> None:
        """DatasetSourceConfig constructed without logical_name has None."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS)
        assert src.logical_name is None, (
            f"Expected logical_name default None, got {src.logical_name!r}"
        )

    def test_logical_name_none_explicit(self) -> None:
        """Passing logical_name=None explicitly is accepted and stays None."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name=None)
        assert src.logical_name is None

    def test_logical_name_in_model_fields(self) -> None:
        """logical_name must appear in DatasetSourceConfig.model_fields."""
        from corpus_forge.config import DatasetSourceConfig

        assert "logical_name" in DatasetSourceConfig.model_fields, (
            "logical_name not in DatasetSourceConfig.model_fields — "
            "DR-G1 must add it as a proper Field()"
        )

    def test_logical_name_field_annotation_is_optional_str(self) -> None:
        """model_fields['logical_name'] annotation must be str | None."""
        from corpus_forge.config import DatasetSourceConfig

        field_info = DatasetSourceConfig.model_fields["logical_name"]
        # Pydantic v2 stores annotation on the FieldInfo
        annotation = field_info.annotation
        # Accept both 'str | None' and 'Optional[str]' representations
        import typing

        args = typing.get_args(annotation)
        assert type(None) in args, (
            f"logical_name annotation {annotation!r} must include NoneType; got args={args!r}"
        )
        assert str in args, (
            f"logical_name annotation {annotation!r} must include str; got args={args!r}"
        )


# ---------------------------------------------------------------------------
# 3. Valid logical names accepted
# ---------------------------------------------------------------------------


class TestLogicalNameValidAccepted:
    """Valid POSIX-safe identifiers are accepted."""

    @pytest.mark.parametrize(
        "name",
        [
            "notes",
            "work-notes",
            "team_data",
            "personal-vault",
            "a.b.c",
            "x_y",
            "a",  # single char (minimum length 1)
            "a" * 64,  # exactly 64 chars (maximum)
            "Notes2",  # mixed case + digit
            "A",  # uppercase single char
            "Z9",  # uppercase + digit
            "a1b2c3",  # alnum mix
            "my.corpus-data_v2",  # all separator types
        ],
    )
    def test_valid_logical_name_accepted(self, name: str) -> None:
        """logical_name={name!r} should be accepted."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name=name)
        assert src.logical_name == name, (
            f"Expected logical_name={name!r} to be preserved, got {src.logical_name!r}"
        )


# ---------------------------------------------------------------------------
# 4. Invalid logical names rejected (ValidationError)
# ---------------------------------------------------------------------------


class TestLogicalNameInvalidRejected:
    """Invalid names must raise pydantic.ValidationError once the field exists.

    RED strategy: each test first asserts that logical_name is a recognised
    field (which fails with AssertionError until DR-G1 adds it).  Once the
    field exists, the inner pytest.raises block will catch the pattern-
    validation error.  This gives a clear, intentional RED failure that maps
    directly to the missing implementation — not a spurious "DID NOT RAISE"
    that would occur if we relied on Pydantic's extra-field policy.
    """

    def _require_field_exists(self) -> None:
        """Pre-condition: logical_name must be a real field, not silently ignored."""
        from corpus_forge.config import DatasetSourceConfig

        assert "logical_name" in DatasetSourceConfig.model_fields, (
            "DR-G1 has not yet added the logical_name field to DatasetSourceConfig. "
            "This test will be RED until the field is declared."
        )

    @pytest.mark.parametrize(
        "bad_name",
        [
            "",  # empty string — must reject (use None to disable)
            " ",  # whitespace-only (single space)
            "  ",  # multiple spaces
            "\t",  # tab
            "a b",  # internal space
            "a/b",  # forward slash
            "a:b",  # colon
            "a@b",  # at-sign
            "-leading-dash",  # starts with dash (must start alnum)
            ".",  # only-dot — fails ^[a-zA-Z0-9] first-char
            "-",  # dash only — fails first-char
            "_leading",  # starts with underscore — fails ^[a-zA-Z0-9]
            "a" * 65,  # 65 chars — one over the 64-char max
            "a\\b",  # backslash
            "a!b",  # exclamation
            "a#b",  # hash
        ],
    )
    def test_invalid_logical_name_rejected(self, bad_name: str) -> None:
        """logical_name={bad_name!r} should raise ValidationError once field exists."""
        from corpus_forge.config import DatasetSourceConfig

        # Pre-condition: field must exist before we can test pattern validation
        self._require_field_exists()

        with pytest.raises(ValidationError, match="logical_name"):
            DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name=bad_name)

    def test_empty_string_is_rejected_not_coerced_to_none(self) -> None:
        """Empty string '' must be rejected with ValidationError, NOT silently coerced to None.

        Decision lock (tasks.md C2): the field uses min_length=1 so empty string
        raises ValidationError. Callers that want 'no logical name' must use None,
        not ''. This test encodes that product decision explicitly so it can't drift.
        """
        from corpus_forge.config import DatasetSourceConfig

        # Pre-condition: field must exist before we can test rejection
        self._require_field_exists()

        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name="")
        # If we reach here without raising, the field was silently coerced.
        # The `pytest.raises` above guarantees this assertion never runs in
        # the success path, but it documents the contract for readers.

    def test_whitespace_only_is_rejected(self) -> None:
        """A whitespace-only string like ' ' must be rejected (not pass-through)."""
        from corpus_forge.config import DatasetSourceConfig

        self._require_field_exists()

        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name="   ")

    def test_65_char_name_rejected(self) -> None:
        """A 65-character name exceeds max_length=64 and must be rejected."""
        from corpus_forge.config import DatasetSourceConfig

        self._require_field_exists()

        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name="a" * 65)

    def test_64_char_name_accepted(self) -> None:
        """A 64-character name is at max_length and must be accepted.

        Pre-condition guard: field must exist (will fail here until DR-G1).
        """
        from corpus_forge.config import DatasetSourceConfig

        self._require_field_exists()

        name = "a" * 64
        src = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name=name)
        assert src.logical_name == name


# ---------------------------------------------------------------------------
# 5. TOML round-trip
# ---------------------------------------------------------------------------


class TestLogicalNameTomlRoundTrip:
    """logical_name survives TOML serialization / Config.load round-trip."""

    def test_toml_with_logical_name_notes_parses(self, tmp_path: Path) -> None:
        """A [[datasets.sources]] block with logical_name = 'notes' parses cleanly."""
        toml = _BASE_TOML_TMPL.format(extra_source="")
        # Inject logical_name into the first (and only) source block
        toml = toml.replace(
            '  chunker = "markdown"',
            '  chunker = "markdown"\n  logical_name = "notes"',
        )
        cfg = _load_config(toml, tmp_path)
        src = cfg.datasets[0].sources[0]
        assert src.logical_name == "notes", (
            f"TOML round-trip failed: expected 'notes', got {src.logical_name!r}"
        )

    def test_toml_without_logical_name_defaults_to_none(self, tmp_path: Path) -> None:
        """A [[datasets.sources]] block WITHOUT logical_name keeps logical_name=None."""
        toml = _BASE_TOML_TMPL.format(extra_source="")
        cfg = _load_config(toml, tmp_path)
        src = cfg.datasets[0].sources[0]
        assert src.logical_name is None

    def test_model_dump_round_trip_with_logical_name(self) -> None:
        """model_dump() + model_validate() preserves logical_name."""
        from corpus_forge.config import DatasetSourceConfig

        original = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name="my-vault")
        dumped = original.model_dump()
        restored = DatasetSourceConfig.model_validate(dumped)
        assert restored.logical_name == "my-vault"

    def test_model_dump_round_trip_with_none(self) -> None:
        """model_dump() + model_validate() preserves logical_name=None."""
        from corpus_forge.config import DatasetSourceConfig

        original = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name=None)
        dumped = original.model_dump()
        restored = DatasetSourceConfig.model_validate(dumped)
        assert restored.logical_name is None

    def test_model_dump_json_includes_logical_name(self) -> None:
        """model_dump_json() serialises logical_name to a JSON string."""
        import json

        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS, logical_name="team_data")
        payload = json.loads(src.model_dump_json())
        assert payload.get("logical_name") == "team_data", (
            f"model_dump_json missing or wrong logical_name: {payload!r}"
        )

    def test_model_dump_json_none_is_null(self) -> None:
        """model_dump_json() serialises logical_name=None to JSON null."""
        import json

        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(**_MINIMAL_SOURCE_KWARGS)
        payload = json.loads(src.model_dump_json())
        # Key must be present and have value null
        assert "logical_name" in payload, "logical_name key missing from model_dump_json"
        assert payload["logical_name"] is None


# ---------------------------------------------------------------------------
# 6. Per-source independence
# ---------------------------------------------------------------------------


class TestLogicalNamePerSourceIndependence:
    """Two sources in the same dataset can have independent logical_name values."""

    def test_two_sources_one_with_one_without(self, tmp_path: Path) -> None:
        """One source has logical_name='a', the other has none; both validate."""
        toml = """\
[backend]
kind = "postgres"
dsn  = "postgresql://user:pass@localhost/forge"

[daemon]

[[datasets]]
name = "test-ds"
kind = "text"

  [[datasets.sources]]
  plugin       = "filesystem"
  root         = "/tmp/x"
  chunker      = "markdown"
  logical_name = "a"

  [[datasets.sources]]
  plugin  = "filesystem"
  root    = "/tmp/y"
  chunker = "markdown"

[[embedders]]
name      = "e"
provider  = "sentence_transformers"
model_id  = "m"
dimension = 1
"""
        cfg = _load_config(toml, tmp_path)
        sources = cfg.datasets[0].sources
        assert len(sources) == 2
        assert sources[0].logical_name == "a", (
            f"First source: expected logical_name='a', got {sources[0].logical_name!r}"
        )
        assert sources[1].logical_name is None, (
            f"Second source: expected logical_name=None, got {sources[1].logical_name!r}"
        )

    def test_two_sources_both_with_distinct_names(self, tmp_path: Path) -> None:
        """Two sources each with a different logical_name both validate."""
        toml = """\
[backend]
kind = "postgres"
dsn  = "postgresql://user:pass@localhost/forge"

[daemon]

[[datasets]]
name = "test-ds"
kind = "text"

  [[datasets.sources]]
  plugin       = "filesystem"
  root         = "/tmp/x"
  chunker      = "markdown"
  logical_name = "laptop-a"

  [[datasets.sources]]
  plugin       = "filesystem"
  root         = "/Users/bob/Notes"
  chunker      = "markdown"
  logical_name = "laptop-b"

[[embedders]]
name      = "e"
provider  = "sentence_transformers"
model_id  = "m"
dimension = 1
"""
        cfg = _load_config(toml, tmp_path)
        sources = cfg.datasets[0].sources
        assert sources[0].logical_name == "laptop-a"
        assert sources[1].logical_name == "laptop-b"


# ---------------------------------------------------------------------------
# 7. Coexistence with all existing DatasetSourceConfig fields
# ---------------------------------------------------------------------------


class TestLogicalNameCoexistsWithExistingFields:
    """logical_name must not break any existing field behavior."""

    def test_coexists_with_exclude_globs(self) -> None:
        """logical_name and exclude_globs can both be set."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(
            plugin="filesystem",
            root="/tmp/foo",
            chunker="markdown",
            exclude_globs=["*.tmp"],
            logical_name="work",
        )
        assert src.logical_name == "work"
        assert src.exclude_globs == ["*.tmp"]

    def test_coexists_with_extraction(self) -> None:
        """logical_name and extraction config can both be set."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(
            plugin="filesystem",
            root="/tmp/foo",
            chunker="markdown",
            extraction={"enable_pdf": False},  # any real ExtractionConfig field
            logical_name="docs",
        )
        assert src.logical_name == "docs"
        assert src.extraction is not None

    def test_coexists_with_chunker_config(self) -> None:
        """logical_name and chunker_config can both be set."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(
            plugin="filesystem",
            root="/tmp/foo",
            chunker="markdown",
            chunker_config={"max_chars": 1500, "overlap": 200},
            logical_name="vault",
        )
        assert src.logical_name == "vault"
        assert src.chunker_config == {"max_chars": 1500, "overlap": 200}

    def test_coexists_with_max_bytes(self) -> None:
        """logical_name and max_bytes can both be set; max_bytes is the last pre-existing field."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(
            plugin="filesystem",
            root="/tmp/foo",
            chunker="markdown",
            max_bytes=10_000_000,
            logical_name="small-vault",
        )
        assert src.logical_name == "small-vault"
        assert src.max_bytes == 10_000_000

    def test_coexists_with_max_rows(self) -> None:
        """logical_name and max_rows can both be set."""
        from corpus_forge.config import DatasetSourceConfig

        src = DatasetSourceConfig(
            plugin="filesystem",
            root="/tmp/foo",
            chunker="markdown",
            max_rows=5000,
            logical_name="capped-source",
        )
        assert src.logical_name == "capped-source"
        assert src.max_rows == 5000


# ---------------------------------------------------------------------------
# 8. Backwards compat — config.example.toml still validates without logical_name
# ---------------------------------------------------------------------------


class TestBackwardsCompatWithExampleConfig:
    """config.example.toml (no logical_name) must still validate cleanly.

    This is the regression sentinel: adding logical_name must NOT break
    existing configs that don't mention the field.
    """

    def test_config_example_toml_validates_after_field_added(self, tmp_path: Path) -> None:
        """config.example.toml must load cleanly — logical_name is optional."""
        import tomllib
        from pathlib import Path as _Path

        from corpus_forge.config import Config

        repo_root = _Path(__file__).parent.parent.parent
        example_path = repo_root / "config.example.toml"

        raw = example_path.read_text(encoding="utf-8")
        # Stub the env-interpolated DSN so we don't depend on PG_* env vars
        raw = raw.replace(
            "postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}",
            "postgresql://user:pass@localhost:5432/corpus",
        )
        data = tomllib.loads(raw)
        # Must not raise ValidationError
        cfg = Config(**data)
        # Spot-check: all sources have logical_name=None (not in the example file)
        for dataset in cfg.datasets:
            for source in dataset.sources:
                assert source.logical_name is None, (
                    f"source {source.plugin!r} in dataset {dataset.name!r} has "
                    f"logical_name={source.logical_name!r}; expected None — "
                    "config.example.toml should not yet include logical_name"
                )
