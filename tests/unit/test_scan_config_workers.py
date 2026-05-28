"""CW2-T1 — ScanConfig.workers default/validation + CF_SCAN_WORKERS env override (RED).

Contract (from .planning/tdd/tasks.md § Concurrent Scanner Walk):
  - `ScanConfig.workers` exists, default=1, ge=1 (already exists — regression guard).
  - `CF_SCAN_WORKERS` env variable wins over the config field value.
  - Effective-default resolver: a public function (named
    `resolve_effective_workers`) returns `min(32, (os.cpu_count() or 1) * 4)`
    when the caller hasn't explicitly configured workers.
  - The resolver must be importable from `corpus_forge.scanner.walker`
    (same module as `walk()`).

Note to coder: the resolver function name is pinned here as
`resolve_effective_workers`. If you choose a different name, update
both this test file and the tasks.md note column.
"""

from __future__ import annotations

import os

import pytest

# ─────────────────────────────────────────────────────────────────────────
# ScanConfig.workers — field existence + validation
# ─────────────────────────────────────────────────────────────────────────


def test_scan_config_workers_default_is_1() -> None:
    """ScanConfig.workers defaults to 1 (API back-compat sentinel)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig()
    assert cfg.workers == 1


def test_scan_config_workers_explicit_1_accepted() -> None:
    """workers=1 is valid (serial path)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=1)
    assert cfg.workers == 1


def test_scan_config_workers_gt_1_accepted() -> None:
    """workers > 1 must now be accepted (NEW contract: not NotImplementedError)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=4)
    assert cfg.workers == 4


def test_scan_config_workers_0_rejected() -> None:
    """workers=0 violates ge=1 — must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(workers=0)


def test_scan_config_workers_negative_rejected() -> None:
    """workers=-1 violates ge=1 — must raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(workers=-1)


def test_scan_config_workers_large_value_accepted() -> None:
    """workers=64 is structurally valid (ge=1 only)."""
    from corpus_forge.config import ScanConfig

    cfg = ScanConfig(workers=64)
    assert cfg.workers == 64


def test_scan_config_extra_fields_rejected() -> None:
    """extra='forbid' is preserved — unknown fields raise ValidationError."""
    import pydantic

    from corpus_forge.config import ScanConfig

    with pytest.raises(pydantic.ValidationError):
        ScanConfig(unknown_field=True)  # type: ignore[call-arg]


# ─────────────────────────────────────────────────────────────────────────
# CF_SCAN_WORKERS env override
# ─────────────────────────────────────────────────────────────────────────


def test_cf_scan_workers_env_wins_over_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF_SCAN_WORKERS env variable overrides config workers value.

    The resolver must read CF_SCAN_WORKERS and return its int value
    regardless of what ScanConfig.workers says.
    """
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.setenv("CF_SCAN_WORKERS", "8")
    result = resolve_effective_workers(config_workers=1)
    assert result == 8, f"Expected env override 8, got {result}"


def test_cf_scan_workers_env_string_4(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF_SCAN_WORKERS=4 is parsed and returned."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.setenv("CF_SCAN_WORKERS", "4")
    result = resolve_effective_workers(config_workers=16)
    assert result == 4


def test_cf_scan_workers_env_wins_even_when_config_is_larger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env wins unconditionally — even when env value < config_workers."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.setenv("CF_SCAN_WORKERS", "2")
    result = resolve_effective_workers(config_workers=32)
    assert result == 2


def test_cf_scan_workers_env_absent_uses_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """When CF_SCAN_WORKERS is unset, config_workers is respected."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.delenv("CF_SCAN_WORKERS", raising=False)
    result = resolve_effective_workers(config_workers=7)
    assert result == 7


def test_cf_scan_workers_env_1_is_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF_SCAN_WORKERS=1 forces serial path."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.setenv("CF_SCAN_WORKERS", "1")
    result = resolve_effective_workers(config_workers=8)
    assert result == 1


# ─────────────────────────────────────────────────────────────────────────
# Effective-default resolver — auto/unset behaviour
# ─────────────────────────────────────────────────────────────────────────


def test_effective_default_resolver_is_importable() -> None:
    """resolve_effective_workers must be importable from corpus_forge.scanner.walker."""
    from corpus_forge.scanner.walker import resolve_effective_workers  # noqa: F401


def test_effective_default_no_env_no_config_returns_cpu_formula(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no env and config_workers is None (auto), resolver returns the formula.

    Formula: min(32, (os.cpu_count() or 1) * 4).
    The resolver accepts `config_workers=None` as the auto/unset sentinel.
    """
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.delenv("CF_SCAN_WORKERS", raising=False)
    expected = min(32, (os.cpu_count() or 1) * 4)
    result = resolve_effective_workers(config_workers=None)
    assert result == expected, f"Expected {expected}, got {result}"


def test_effective_default_formula_capped_at_32(monkeypatch: pytest.MonkeyPatch) -> None:
    """Formula result is capped at 32 even with many CPUs."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.delenv("CF_SCAN_WORKERS", raising=False)
    # Simulate 100 CPUs: min(32, 100 * 4) = 32.
    monkeypatch.setattr("os.cpu_count", lambda: 100)
    result = resolve_effective_workers(config_workers=None)
    assert result == 32, f"Expected cap at 32, got {result}"


def test_effective_default_formula_minimum_1_when_cpu_count_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When os.cpu_count() returns None, formula uses 1 as the fallback CPU count.

    Formula: min(32, (None or 1) * 4) = min(32, 4) = 4.
    """
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.delenv("CF_SCAN_WORKERS", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: None)
    result = resolve_effective_workers(config_workers=None)
    assert result == 4, f"Expected 4 (1 CPU fallback x 4), got {result}"


def test_effective_default_env_wins_over_auto_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even in auto mode (config=None), CF_SCAN_WORKERS env wins."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.setenv("CF_SCAN_WORKERS", "16")
    result = resolve_effective_workers(config_workers=None)
    assert result == 16


def test_effective_default_resolver_returns_int(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_effective_workers always returns a plain int, not a string."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.delenv("CF_SCAN_WORKERS", raising=False)
    result = resolve_effective_workers(config_workers=4)
    assert isinstance(result, int), f"Expected int, got {type(result)}"


def test_effective_default_resolver_env_returns_int(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override is parsed to int, not returned as a string."""
    from corpus_forge.scanner.walker import resolve_effective_workers

    monkeypatch.setenv("CF_SCAN_WORKERS", "12")
    result = resolve_effective_workers(config_workers=1)
    assert isinstance(result, int)
    assert result == 12
