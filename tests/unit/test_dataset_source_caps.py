"""Unit tests for ``DatasetSourceConfig.max_rows`` / ``max_bytes`` caps.

RFC ``rfc-corpus-growth-controls`` (P1). The fields are storage for
the per-source growth caps the eviction loop will enforce; this PR
adds the schema only — the runtime eviction lands in a follow-up.
Pin the defaults and the positivity validators so a future PR can't
silently regress the contract.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from corpus_forge.config import DatasetSourceConfig


def _base_kwargs() -> dict:
    """The minimum fields a ``DatasetSourceConfig`` needs to validate."""
    return {
        "plugin": "markdown_vault",
        "vault_root": "/tmp/vault",
        "chunker": "markdown",
    }


class TestDefaults:
    """Both caps default to ``None`` (uncapped)."""

    def test_max_rows_default_none(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs())
        assert cfg.max_rows is None

    def test_max_bytes_default_none(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs())
        assert cfg.max_bytes is None


class TestMaxRowsBounds:
    """`max_rows` must be a positive int when set."""

    def test_positive_accepted(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs(), max_rows=10_000)
        assert cfg.max_rows == 10_000

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_base_kwargs(), max_rows=0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_base_kwargs(), max_rows=-1)


class TestMaxBytesBounds:
    """`max_bytes` must be a positive int when set."""

    def test_positive_accepted(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs(), max_bytes=1_000_000_000)
        assert cfg.max_bytes == 1_000_000_000

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_base_kwargs(), max_bytes=0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatasetSourceConfig(**_base_kwargs(), max_bytes=-100)


class TestCoexistence:
    """`max_rows` and `max_bytes` can be set independently or together."""

    def test_only_rows(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs(), max_rows=500)
        assert cfg.max_rows == 500
        assert cfg.max_bytes is None

    def test_only_bytes(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs(), max_bytes=500_000_000)
        assert cfg.max_bytes == 500_000_000
        assert cfg.max_rows is None

    def test_both(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs(), max_rows=1_000, max_bytes=10_000_000_000)
        assert cfg.max_rows == 1_000
        assert cfg.max_bytes == 10_000_000_000


class TestBackwardsCompat:
    """Existing configs without `max_rows`/`max_bytes` continue to validate."""

    def test_minimum_kwargs_still_valid(self) -> None:
        cfg = DatasetSourceConfig(**_base_kwargs())
        # Spot-check that the pre-existing fields still resolve.
        assert cfg.plugin == "markdown_vault"
        assert cfg.chunker == "markdown"
        # New fields default to None.
        assert cfg.max_rows is None
        assert cfg.max_bytes is None
