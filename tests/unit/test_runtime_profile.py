"""Unit tests for :mod:`corpus_forge.runtime_profile`.

Calibration store for wall-clock estimation. Pure-function tests; the
profile file is redirected via ``CF_RUNTIME_PROFILE`` so the user's real
profile is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge import runtime_profile as rp


@pytest.fixture
def profile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the profile to a tmp file and return its path."""
    path = tmp_path / "runtime_profile.json"
    monkeypatch.setenv("CF_RUNTIME_PROFILE", str(path))
    return path


def test_load_missing_file_returns_empty_profile(profile_path: Path) -> None:
    profile = rp.load()
    assert profile.is_empty()
    assert profile.total_samples() == 0
    assert profile.scan is None
    assert profile.db_write is None


def test_record_then_load_roundtrips_a_scan_sample(profile_path: Path) -> None:
    assert rp.record("scan", units=1000, seconds=0.05) is True
    loaded = rp.load()
    assert loaded.scan is not None
    assert loaded.scan.samples == 1
    assert loaded.scan.sec_per_unit == pytest.approx(0.05 / 1000)


def test_record_keyed_phase_requires_key(profile_path: Path) -> None:
    # Missing key for a keyed phase is a soft no-op (returns False).
    assert rp.record("extract", units=1024, seconds=0.01) is False
    assert rp.load().is_empty()


def test_record_rejects_nonsensical_input(profile_path: Path) -> None:
    assert rp.record("scan", units=0, seconds=1.0) is False
    assert rp.record("scan", units=10, seconds=0.0) is False
    assert rp.record("scan", units=-1, seconds=1.0) is False
    assert rp.load().is_empty()


def test_record_rejects_non_finite_samples(profile_path: Path) -> None:
    """``inf`` / ``nan`` seconds can't be folded into the EWMA — they'd
    propagate forever once persisted."""
    assert rp.record("scan", units=10, seconds=float("inf")) is False
    assert rp.record("scan", units=10, seconds=float("nan")) is False
    assert rp.load().is_empty()


def test_record_rejects_alpha_outside_zero_to_one(profile_path: Path) -> None:
    """EWMA is undefined for alpha outside (0, 1]."""
    assert rp.record("scan", units=10, seconds=0.1, alpha=0.0) is False
    assert rp.record("scan", units=10, seconds=0.1, alpha=1.5) is False
    assert rp.record("scan", units=10, seconds=0.1, alpha=-0.1) is False
    assert rp.load().is_empty()
    # alpha=1.0 is the inclusive upper bound — must succeed.
    assert rp.record("scan", units=10, seconds=0.1, alpha=1.0) is True


def test_ewma_converges_over_multiple_samples(profile_path: Path) -> None:
    """An anomalous first sample is diluted by subsequent ones."""
    # Outlier first sample, then 10 normal ones.
    rp.record("embed", units=10, seconds=10.0, key="m1")  # 1.0 sec/chunk
    for _ in range(10):
        rp.record("embed", units=10, seconds=0.5, key="m1")  # 0.05 sec/chunk
    loaded = rp.load()
    rate = loaded.embed["m1"]
    assert rate.samples == 11
    # The outlier (1.0) is pulled toward 0.05 by EWMA; final rate sits
    # closer to the normal sample than to the anomaly.
    assert rate.sec_per_unit < 0.5
    assert rate.sec_per_unit > 0.05


def test_record_alpha_one_replaces_value(profile_path: Path) -> None:
    """alpha=1.0 makes the latest sample fully replace prior history —
    used by deterministic tests that want a single-shot update."""
    rp.record("embed", units=1, seconds=0.5, key="m1", alpha=1.0)
    rp.record("embed", units=1, seconds=0.1, key="m1", alpha=1.0)
    loaded = rp.load()
    assert loaded.embed["m1"].sec_per_unit == pytest.approx(0.1)
    assert loaded.embed["m1"].samples == 2


def test_get_rate_returns_none_for_unknown_key(profile_path: Path) -> None:
    rp.record("embed", units=10, seconds=0.5, key="m1")
    loaded = rp.load()
    assert loaded.get_rate("embed", "m1") is not None
    assert loaded.get_rate("embed", "unknown-name") is None
    assert loaded.get_rate("scan") is None  # never recorded


def test_get_rate_unknown_phase_raises(profile_path: Path) -> None:
    profile = rp.RuntimeProfile()
    with pytest.raises(ValueError):
        profile.get_rate("nope")  # type: ignore[arg-type]


def test_save_writes_valid_json(profile_path: Path) -> None:
    rp.record("scan", units=1000, seconds=0.05)
    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == rp.SCHEMA_VERSION
    assert raw["scan"]["sec_per_unit"] > 0
    assert raw.get("updated_at")


def test_record_tolerates_unwritable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only profile path: ``record`` returns False, never raises."""
    bogus = tmp_path / "nonexistent" / "child" / "profile.json"
    monkeypatch.setenv("CF_RUNTIME_PROFILE", str(bogus))
    # Make the parent unwritable by using a file as the parent path.
    blocker = tmp_path / "nonexistent"
    blocker.write_text("blocker", encoding="utf-8")
    # The write will fail in ``mkdir`` / ``NamedTemporaryFile``.
    result = rp.record("scan", units=10, seconds=0.001)
    assert result is False
