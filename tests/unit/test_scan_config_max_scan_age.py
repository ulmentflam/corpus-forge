"""SR-T7 unit slice — ScanConfig.max_scan_age field contract (RED).

Contract (from tasks.md SR-T7 / SR-G7):
  - ``ScanConfig.max_scan_age: float = Field(default=0.0, ge=0.0)`` exists.
  - Default is ``0.0`` (always rescan — backwards-compat invariant).
  - ``ge=0.0`` is enforced: negative values must raise ``ValidationError``.
  - The field is ``float`` (not ``int``, not ``str``): accepts both ``0``
    (coerced to ``0.0``) and ``3600.0`` etc.
  - ``extra='forbid'`` is preserved on ``ScanConfig`` (existing contract from
    CW2-T1 / test_scan_config_workers.py).

RED condition:
  ``ScanConfig`` does not yet have a ``max_scan_age`` field (SR-G7 adds it).
  All tests here will fail with ``ValidationError`` (extra-field forbidden) or
  ``AttributeError`` until SR-G7 is implemented.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# max_scan_age field — existence + default
# ---------------------------------------------------------------------------


def test_scan_config_max_scan_age_field_exists() -> None:
    """ScanConfig must have a max_scan_age attribute after construction."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert hasattr(cfg, "max_scan_age"), (
        "ScanConfig is missing the max_scan_age field — SR-G7 must add it"
    )


def test_scan_config_max_scan_age_default_is_zero() -> None:
    """ScanConfig().max_scan_age must default to 0.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert cfg.max_scan_age == 0.0, f"Expected max_scan_age default 0.0, got {cfg.max_scan_age!r}"


def test_scan_config_max_scan_age_default_is_float() -> None:
    """Default value must be a Python float, not int."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert isinstance(cfg.max_scan_age, float), (
        f"max_scan_age must be float, got {type(cfg.max_scan_age)!r}"
    )


# ---------------------------------------------------------------------------
# max_scan_age field — valid values
# ---------------------------------------------------------------------------


def test_scan_config_max_scan_age_explicit_zero_accepted() -> None:
    """max_scan_age=0.0 (always rescan) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=0.0)
    assert cfg.max_scan_age == 0.0


def test_scan_config_max_scan_age_integer_zero_coerced() -> None:
    """max_scan_age=0 (int) must be coerced to 0.0 (float)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=0)  # type: ignore[arg-type]
    assert cfg.max_scan_age == 0.0


def test_scan_config_max_scan_age_3600_accepted() -> None:
    """max_scan_age=3600.0 (1 hour in seconds) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=3600.0)
    assert cfg.max_scan_age == 3600.0


def test_scan_config_max_scan_age_86400_accepted() -> None:
    """max_scan_age=86400.0 (24 hours) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=86400.0)
    assert cfg.max_scan_age == 86400.0


def test_scan_config_max_scan_age_fractional_accepted() -> None:
    """max_scan_age=0.5 (sub-second) must be accepted."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=0.5)
    assert cfg.max_scan_age == 0.5


def test_scan_config_max_scan_age_large_value_accepted() -> None:
    """max_scan_age=1e9 must be accepted (no upper bound)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=1e9)
    assert cfg.max_scan_age == 1e9


# ---------------------------------------------------------------------------
# max_scan_age field — validation enforcement
# ---------------------------------------------------------------------------


def test_scan_config_max_scan_age_negative_rejected() -> None:
    """max_scan_age < 0 must raise ValidationError (ge=0.0 constraint)."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(max_scan_age=-1.0)


def test_scan_config_max_scan_age_minus_epsilon_rejected() -> None:
    """max_scan_age=-0.001 must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(max_scan_age=-0.001)


def test_scan_config_max_scan_age_large_negative_rejected() -> None:
    """max_scan_age=-3600.0 must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(max_scan_age=-3600.0)


# ---------------------------------------------------------------------------
# max_scan_age coexists with existing fields
# ---------------------------------------------------------------------------


def test_scan_config_max_scan_age_plus_workers() -> None:
    """max_scan_age and workers can both be set simultaneously."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=4, max_scan_age=1800.0)
    assert cfg.workers == 4
    assert cfg.max_scan_age == 1800.0


def test_scan_config_max_scan_age_plus_follow_symlinks() -> None:
    """max_scan_age and follow_symlinks can both be set."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(follow_symlinks=True, max_scan_age=60.0)
    assert cfg.follow_symlinks is True
    assert cfg.max_scan_age == 60.0


def test_scan_config_all_fields_together() -> None:
    """All three known ScanConfig fields can be set together."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=2, follow_symlinks=False, max_scan_age=7200.0)
    assert cfg.workers == 2
    assert cfg.follow_symlinks is False
    assert cfg.max_scan_age == 7200.0


# ---------------------------------------------------------------------------
# extra='forbid' preservation
# ---------------------------------------------------------------------------


def test_scan_config_extra_fields_still_rejected_after_max_scan_age_added() -> None:
    """extra='forbid' must remain on ScanConfig after adding max_scan_age."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(max_scan_age=0.0, unknown_new_field=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Config-level TOML round-trip — max_scan_age is recognised by the parser
# ---------------------------------------------------------------------------


def test_scan_config_max_scan_age_survives_model_dump_round_trip() -> None:
    """model_dump() + model_validate() round-trip preserves max_scan_age."""
    from corpus_forge.config import ScanConfig

    original = ScanConfig(max_scan_age=3600.0)
    dumped = original.model_dump()
    restored = ScanConfig.model_validate(dumped)
    assert restored.max_scan_age == 3600.0


def test_scan_config_max_scan_age_in_model_fields() -> None:
    """max_scan_age must appear in ScanConfig.model_fields (Pydantic v2 introspection)."""
    from corpus_forge.config import ScanConfig

    assert "max_scan_age" in ScanConfig.model_fields, (
        "max_scan_age not in ScanConfig.model_fields — SR-G7 must add it as a proper Field()"
    )
