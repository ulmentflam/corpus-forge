"""Unit tests for ``GrowthConfig`` + the ``_parse_bytes`` helper.

Backs RFC ``rfc-corpus-growth-controls`` (P1). The config block is the
foundation that the prune / cap-eviction / estimate-gate features
build on, so the contract — defaults, validation, suffix parsing — is
pinned tight here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from corpus_forge.config import GrowthConfig, _parse_bytes


class TestGrowthConfigDefaults:
    """Default-constructed ``GrowthConfig`` is no-enforcement."""

    def test_construction_with_no_arguments(self) -> None:
        cfg = GrowthConfig()
        assert cfg.prune_percentile_default == 10
        assert cfg.sync_cap_bytes is None
        assert cfg.per_source_cap_default_rows == 0


class TestPercentileBounds:
    """`prune_percentile_default` is bounded [0, 100]."""

    def test_zero_accepted(self) -> None:
        assert GrowthConfig(prune_percentile_default=0).prune_percentile_default == 0

    def test_hundred_accepted(self) -> None:
        assert GrowthConfig(prune_percentile_default=100).prune_percentile_default == 100

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrowthConfig(prune_percentile_default=-1)

    def test_above_hundred_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrowthConfig(prune_percentile_default=101)


class TestSyncCapBytesParse:
    """Human-readable cap strings parse to ints; `None` stays `None`."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1024", 1024),
            ("1024b", 1024),
            ("1024B", 1024),
            ("1K", 1024),
            ("1KB", 1024),
            ("10G", 10 * 1024**3),
            ("10g", 10 * 1024**3),
            ("10GB", 10 * 1024**3),
            ("500M", 500 * 1024**2),
            ("1T", 1024**4),
            # Decimal magnitudes round to int.
            ("1.5G", int(1.5 * 1024**3)),
            # Underscore separator for readability.
            ("10_000", 10_000),
        ],
    )
    def test_string_forms_parse(self, value: str, expected: int) -> None:
        cfg = GrowthConfig(sync_cap_bytes=value)
        assert cfg.sync_cap_bytes == expected

    def test_int_passes_through(self) -> None:
        cfg = GrowthConfig(sync_cap_bytes=2048)
        assert cfg.sync_cap_bytes == 2048

    def test_none_stays_none(self) -> None:
        cfg = GrowthConfig(sync_cap_bytes=None)
        assert cfg.sync_cap_bytes is None

    @pytest.mark.parametrize(
        "bad",
        [
            "10X",  # unknown suffix
            "G",  # no numeric prefix
            "-5G",  # negative
            "0",  # non-positive
            "abc",  # non-numeric
            "",  # empty
            "1.5.0G",  # malformed numeric
        ],
    )
    def test_invalid_strings_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            GrowthConfig(sync_cap_bytes=bad)

    def test_non_positive_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrowthConfig(sync_cap_bytes=0)
        with pytest.raises(ValidationError):
            GrowthConfig(sync_cap_bytes=-1)


class TestPerSourceCapDefaultRows:
    """`per_source_cap_default_rows` must be a non-negative int."""

    def test_zero_means_disabled(self) -> None:
        assert GrowthConfig(per_source_cap_default_rows=0).per_source_cap_default_rows == 0

    def test_large_value_accepted(self) -> None:
        cfg = GrowthConfig(per_source_cap_default_rows=1_000_000)
        assert cfg.per_source_cap_default_rows == 1_000_000

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrowthConfig(per_source_cap_default_rows=-1)


class TestExtraForbid:
    """`extra='forbid'` catches typos in `[growth]` blocks."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GrowthConfig(prune_percentile_default=10, futuristic_knob=42)


class TestParseBytesHelper:
    """Direct unit tests on `_parse_bytes` for paths the model doesn't reach."""

    def test_int_input_returns_input(self) -> None:
        assert _parse_bytes(1024) == 1024

    def test_string_int_returns_parsed(self) -> None:
        assert _parse_bytes("1024") == 1024

    def test_lowercase_suffix(self) -> None:
        assert _parse_bytes("10g") == 10 * 1024**3

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError):
            _parse_bytes(1.5)  # type: ignore[arg-type]
