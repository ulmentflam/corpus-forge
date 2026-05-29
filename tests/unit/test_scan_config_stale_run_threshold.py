"""DR-T3 (RED) — ScanConfig.stale_run_threshold field + duration-string validator.

Contract (from .planning/tdd/tasks.md §DR-T3 / design contract §C3):

- ``ScanConfig.stale_run_threshold: float = 900.0`` (15 min default).
- Pydantic ``@field_validator("stale_run_threshold", mode="before")`` that
  lazy-imports ``parse_scan_age_spec`` from ``corpus_forge.scanner.age_spec``
  and decodes string forms. Numeric input passes through unchanged.
- Accepted string forms: ``"15m"`` → 900.0, ``"30s"`` → 30.0, ``"2h"`` → 7200.0,
  ``"1d"`` → 86400.0, ``"1.5m"`` → 90.0, ``"60"`` → 60.0.
- Zero is accepted (disables stale-takeover).
- Negative values rejected (ValidationError).
- Invalid strings (unknown suffix, empty, whitespace-only, alpha-only) raise
  ValidationError delegated from parse_scan_age_spec's ValueError.
- ``model_dump()`` returns the canonical float, never the original string.
- ``extra='forbid'`` preserved on ScanConfig.
- Lazy import: importing ``corpus_forge.config`` must NOT pull in
  ``corpus_forge.scanner`` at module level.

RED reason:
  ``stale_run_threshold`` is not yet a field.  ``ScanConfig(stale_run_threshold=900.0)``
  raises ``pydantic.ValidationError: Extra inputs are not permitted``.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Minimal TOML block reused across Config.load round-trip tests.
# Matches the floor used in test_analyze_config.py / test_config.py.
# ---------------------------------------------------------------------------

_BASE_TOML = """
[backend]
kind = "postgres"
dsn = "postgresql://localhost/forge"

[daemon]

[[datasets]]
name = "x"
kind = "text"
sources = [{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}]

[[embedders]]
name = "e"
provider = "sentence_transformers"
model_id = "m"
dimension = 1
"""


def _load_config(toml_body: str, tmp_path: Path):
    """Write TOML to a temp file and load via Config.load."""
    from corpus_forge.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent(toml_body), encoding="utf-8")
    return Config.load(config_path=cfg_path)


# ===========================================================================
# 1. Field existence + default
# ===========================================================================


def test_stale_run_threshold_field_exists() -> None:
    """stale_run_threshold must be an attribute after construction."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert hasattr(cfg, "stale_run_threshold"), (
        "ScanConfig is missing stale_run_threshold — DR-G2 must add it"
    )


def test_stale_run_threshold_default_is_900() -> None:
    """Default must be 900.0 (15 minutes expressed in seconds)."""
    from corpus_forge.config import ScanConfig

    assert ScanConfig().stale_run_threshold == 900.0


def test_stale_run_threshold_default_is_float() -> None:
    """Default type must be float, not int."""
    from corpus_forge.config import ScanConfig

    assert isinstance(ScanConfig().stale_run_threshold, float)


def test_stale_run_threshold_in_model_fields() -> None:
    """stale_run_threshold must appear in ScanConfig.model_fields."""
    from corpus_forge.config import ScanConfig

    assert "stale_run_threshold" in ScanConfig.model_fields, (
        "stale_run_threshold not in ScanConfig.model_fields"
    )


# ===========================================================================
# 2. Numeric pass-through (float and int)
# ===========================================================================


def test_stale_run_threshold_explicit_float_accepted() -> None:
    """Explicit float passes through unchanged."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold=300.0)
    assert cfg.stale_run_threshold == 300.0


def test_stale_run_threshold_int_coerced_to_float() -> None:
    """int input must be coerced to float."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold=900)  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 900.0
    assert isinstance(cfg.stale_run_threshold, float)


def test_stale_run_threshold_zero_accepted() -> None:
    """0.0 is accepted — disables stale-takeover entirely."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold=0.0)
    assert cfg.stale_run_threshold == 0.0


def test_stale_run_threshold_int_zero_coerced() -> None:
    """int 0 coerced to 0.0 float."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold=0)  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 0.0


def test_stale_run_threshold_large_value_accepted() -> None:
    """Very large float accepted (no upper bound)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold=86400.0 * 7)
    assert cfg.stale_run_threshold == 86400.0 * 7


# ===========================================================================
# 3. String form — accepted via parse_scan_age_spec
# ===========================================================================


def test_stale_run_threshold_string_15m() -> None:
    """'15m' must decode to 900.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="15m")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 900.0


def test_stale_run_threshold_string_30s() -> None:
    """'30s' must decode to 30.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="30s")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 30.0


def test_stale_run_threshold_string_2h() -> None:
    """'2h' must decode to 7200.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="2h")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 7200.0


def test_stale_run_threshold_string_1d() -> None:
    """'1d' must decode to 86400.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="1d")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 86400.0


def test_stale_run_threshold_string_fractional_minutes() -> None:
    """'1.5m' must decode to 90.0 (fractional coefficient)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="1.5m")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 90.0


def test_stale_run_threshold_string_bare_number() -> None:
    """'60' (bare numeric string) must decode to 60.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="60")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 60.0


def test_stale_run_threshold_string_zero() -> None:
    """'0' (string zero) must decode to 0.0 (disable)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="0")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 0.0


def test_stale_run_threshold_string_result_is_float() -> None:
    """String-decoded value must be a float, not the original string."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="15m")  # type: ignore[arg-type]
    assert isinstance(cfg.stale_run_threshold, float), (
        f"Expected float, got {type(cfg.stale_run_threshold)!r}: {cfg.stale_run_threshold!r}"
    )


# ===========================================================================
# 4. Rejection paths (ValidationError expected)
# ===========================================================================


def test_stale_run_threshold_negative_float_rejected() -> None:
    """Negative float must raise ValidationError."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold=-1.0)


def test_stale_run_threshold_negative_int_rejected() -> None:
    """Negative int must raise ValidationError."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold=-5)  # type: ignore[arg-type]


def test_stale_run_threshold_negative_string_rejected() -> None:
    """'-5m' must raise ValidationError (negative result from parse_scan_age_spec)."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="-5m")  # type: ignore[arg-type]


def test_stale_run_threshold_alpha_string_rejected() -> None:
    """'abc' must raise ValidationError (not a number, no valid suffix)."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="abc")  # type: ignore[arg-type]


def test_stale_run_threshold_unknown_suffix_rejected() -> None:
    """'5x' must raise ValidationError (unknown suffix)."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="5x")  # type: ignore[arg-type]


def test_stale_run_threshold_empty_string_rejected() -> None:
    """Empty string must raise ValidationError."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="")  # type: ignore[arg-type]


def test_stale_run_threshold_whitespace_only_rejected() -> None:
    """Whitespace-only string must raise ValidationError."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="   ")  # type: ignore[arg-type]


def test_stale_run_threshold_bare_suffix_rejected() -> None:
    """'m' (suffix with no coefficient) must raise ValidationError."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="m")  # type: ignore[arg-type]


# ===========================================================================
# 5. model_dump returns canonical float (TOML→object→TOML normalisation)
# ===========================================================================


def test_model_dump_returns_float_for_numeric_input() -> None:
    """model_dump() must return float 900.0, not any other type."""
    from corpus_forge.config import ScanConfig

    dumped = ScanConfig(stale_run_threshold=900.0).model_dump()
    assert dumped["stale_run_threshold"] == 900.0
    assert isinstance(dumped["stale_run_threshold"], float)


def test_model_dump_returns_float_for_string_input() -> None:
    """model_dump() after string input must return canonical float, not the string."""
    from corpus_forge.config import ScanConfig

    dumped = ScanConfig(stale_run_threshold="15m").model_dump()  # type: ignore[arg-type]
    assert dumped["stale_run_threshold"] == 900.0
    assert isinstance(dumped["stale_run_threshold"], float), (
        f"model_dump() returned {dumped['stale_run_threshold']!r} — "
        "validator must normalise strings to float before Pydantic stores the value"
    )


def test_model_dump_round_trip_float() -> None:
    """model_dump() + model_validate() round-trip preserves the float."""
    from corpus_forge.config import ScanConfig

    original = ScanConfig(stale_run_threshold=1800.0)
    restored = ScanConfig.model_validate(original.model_dump())
    assert restored.stale_run_threshold == 1800.0


def test_model_dump_round_trip_after_string_input() -> None:
    """String→float normalisation survives model_dump() + model_validate()."""
    from corpus_forge.config import ScanConfig

    original = ScanConfig(stale_run_threshold="30m")  # type: ignore[arg-type]
    restored = ScanConfig.model_validate(original.model_dump())
    assert restored.stale_run_threshold == 1800.0


# ===========================================================================
# 6. TOML round-trips via Config.load
# ===========================================================================


def test_toml_float_literal_round_trip(tmp_path: Path) -> None:
    """TOML float literal stale_run_threshold = 900.0 parses to 900.0."""
    cfg = _load_config(
        _BASE_TOML + "\n[scan]\nstale_run_threshold = 900.0\n",
        tmp_path,
    )
    assert cfg.scan.stale_run_threshold == 900.0


def test_toml_string_literal_round_trip(tmp_path: Path) -> None:
    """TOML string literal stale_run_threshold = \"15m\" parses to 900.0."""
    cfg = _load_config(
        _BASE_TOML + '\n[scan]\nstale_run_threshold = "15m"\n',
        tmp_path,
    )
    assert cfg.scan.stale_run_threshold == 900.0


def test_toml_float_and_string_produce_same_result(tmp_path: Path) -> None:
    """Float literal and equivalent string literal must produce identical floats."""
    float_dir = tmp_path / "float"
    str_dir = tmp_path / "str"
    float_dir.mkdir()
    str_dir.mkdir()
    cfg_float = _load_config(
        _BASE_TOML + "\n[scan]\nstale_run_threshold = 900.0\n",
        float_dir,
    )
    cfg_str = _load_config(
        _BASE_TOML + '\n[scan]\nstale_run_threshold = "15m"\n',
        str_dir,
    )
    assert cfg_float.scan.stale_run_threshold == cfg_str.scan.stale_run_threshold


def test_toml_zero_disables_via_float(tmp_path: Path) -> None:
    """stale_run_threshold = 0.0 in TOML disables stale takeover."""
    cfg = _load_config(
        _BASE_TOML + "\n[scan]\nstale_run_threshold = 0.0\n",
        tmp_path,
    )
    assert cfg.scan.stale_run_threshold == 0.0


def test_toml_absent_scan_block_uses_default(tmp_path: Path) -> None:
    """When [scan] block is absent, stale_run_threshold defaults to 900.0."""
    cfg = _load_config(_BASE_TOML, tmp_path)
    assert cfg.scan.stale_run_threshold == 900.0


def test_toml_empty_scan_block_uses_default(tmp_path: Path) -> None:
    """When [scan] block is present but empty, stale_run_threshold still defaults."""
    cfg = _load_config(_BASE_TOML + "\n[scan]\n", tmp_path)
    assert cfg.scan.stale_run_threshold == 900.0


# ===========================================================================
# 7. Coexistence with existing ScanConfig fields
# ===========================================================================


def test_stale_run_threshold_coexists_with_workers() -> None:
    """stale_run_threshold and workers can be set simultaneously."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=4, stale_run_threshold=300.0)
    assert cfg.workers == 4
    assert cfg.stale_run_threshold == 300.0


def test_stale_run_threshold_coexists_with_follow_symlinks() -> None:
    """stale_run_threshold and follow_symlinks can be set simultaneously."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(follow_symlinks=True, stale_run_threshold=60.0)
    assert cfg.follow_symlinks is True
    assert cfg.stale_run_threshold == 60.0


def test_stale_run_threshold_coexists_with_max_scan_age() -> None:
    """stale_run_threshold and max_scan_age coexist without collision."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(max_scan_age=3600.0, stale_run_threshold=900.0)
    assert cfg.max_scan_age == 3600.0
    assert cfg.stale_run_threshold == 900.0


def test_all_scan_config_fields_together() -> None:
    """All known ScanConfig fields can be set simultaneously."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(
        workers=2,
        follow_symlinks=False,
        extra_skip_dirs=["node_modules"],
        max_scan_age=7200.0,
        stale_run_threshold=1800.0,
    )
    assert cfg.workers == 2
    assert cfg.follow_symlinks is False
    assert cfg.extra_skip_dirs == ["node_modules"]
    assert cfg.max_scan_age == 7200.0
    assert cfg.stale_run_threshold == 1800.0


# ===========================================================================
# 8. extra='forbid' preservation
# ===========================================================================


def test_extra_fields_still_rejected() -> None:
    """extra='forbid' must remain after adding stale_run_threshold."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold=900.0, nonexistent_field=True)  # type: ignore[call-arg]


# ===========================================================================
# 9. Lazy import — importing corpus_forge.config must NOT load corpus_forge.scanner
# ===========================================================================


def test_importing_config_does_not_eagerly_import_scanner() -> None:
    """corpus_forge.scanner must NOT be in sys.modules after importing corpus_forge.config.

    The field_validator must lazy-import parse_scan_age_spec inside its body,
    not at module level.  This keeps corpus_forge.config import-time cheap.

    Implementation: use ``importlib.reload(corpus_forge.config)`` rather than
    ``sys.modules.pop`` + re-import.  ``reload`` re-executes the module's
    top-level code IN-PLACE — the module object identity is preserved, so
    other modules that already captured references to ``corpus_forge.config``
    or its classes (e.g. ``corpus_forge.cli`` doing
    ``from corpus_forge.config import Config``) do NOT see a stale namespace
    across the reload.  Pop+re-import breaks that invariant and pollutes
    parallel xdist workers — see the PR #72 cascade and DR-Q1 follow-up.
    """
    import importlib

    import corpus_forge.config as _cfg_mod

    # Evict only scanner.* — config's top-level reload must NOT pull them back in.
    scanner_keys = [k for k in list(sys.modules) if k.startswith("corpus_forge.scanner")]
    snapshot = {name: sys.modules.pop(name) for name in scanner_keys}
    try:
        importlib.reload(_cfg_mod)
        scanner_keys_after = [k for k in sys.modules if k.startswith("corpus_forge.scanner")]
        assert scanner_keys_after == [], (
            f"Reloading corpus_forge.config eagerly loaded corpus_forge.scanner: "
            f"{scanner_keys_after}. The field_validator must lazy-import "
            "parse_scan_age_spec inside its body."
        )
    finally:
        # Restore originals so other tests keep their references.
        # Use setdefault so a fresh import during this test doesn't get clobbered
        # (subsequent tests will use the version sys.modules already has).
        for name, mod in snapshot.items():
            sys.modules.setdefault(name, mod)


def test_validator_triggers_scanner_import_on_first_string_use() -> None:
    """Calling the validator with a string value SHOULD import corpus_forge.scanner.

    This is the positive side of the lazy-import contract: the import happens
    on first use, not at module load time.
    """
    # Evict scanner so we can confirm it gets imported when the validator fires.
    scanner_keys = [k for k in list(sys.modules) if k.startswith("corpus_forge.scanner")]
    snapshot = {name: sys.modules.pop(name) for name in scanner_keys}
    try:
        from corpus_forge.config import ScanConfig

        # Passing a string triggers the validator which must import the scanner module.
        cfg = ScanConfig(stale_run_threshold="15m")  # type: ignore[arg-type]
        assert cfg.stale_run_threshold == 900.0

        # After the validator ran, at least corpus_forge.scanner.age_spec should be loaded.
        loaded = [k for k in sys.modules if k.startswith("corpus_forge.scanner.age_spec")]
        assert loaded, (
            "corpus_forge.scanner.age_spec was not imported even after the string validator ran; "
            "check the lazy-import path inside the field_validator body."
        )
    finally:
        for name, mod in snapshot.items():
            sys.modules[name] = mod


# ===========================================================================
# 10. Boundary / type edge cases
# ===========================================================================


def test_stale_run_threshold_very_small_positive_accepted() -> None:
    """Tiny positive float like 0.001 is accepted (> 0 boundary)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold=0.001)
    assert cfg.stale_run_threshold == pytest.approx(0.001)


def test_stale_run_threshold_string_1s() -> None:
    """'1s' must decode to 1.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="1s")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 1.0


def test_stale_run_threshold_string_1h() -> None:
    """'1h' must decode to 3600.0."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(stale_run_threshold="1h")  # type: ignore[arg-type]
    assert cfg.stale_run_threshold == 3600.0


def test_stale_run_threshold_double_suffix_rejected() -> None:
    """'5mm' has an unrecognised prefix after removing 'm' — rejected."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="5mm")  # type: ignore[arg-type]


def test_stale_run_threshold_nan_string_rejected() -> None:
    """'nan' parses to NaN which is not finite — rejected."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="nan")  # type: ignore[arg-type]


def test_stale_run_threshold_inf_string_rejected() -> None:
    """'inf' parses to Infinity which is not finite — rejected."""
    from corpus_forge.config import ScanConfig

    with pytest.raises(ValidationError):
        ScanConfig(stale_run_threshold="inf")  # type: ignore[arg-type]
