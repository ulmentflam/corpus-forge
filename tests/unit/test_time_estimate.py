"""Unit tests for :mod:`corpus_forge.time_estimate`.

Wall-clock prediction. Pure functions over a synthesised
``SyncEstimate`` + ``Config`` — no filesystem walk, no backend, no
model calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge import runtime_profile as rp
from corpus_forge.estimate import EmbedderSizing, ExtractorClassSummary, SyncEstimate
from corpus_forge.time_estimate import (
    SCHEMA_VERSION,
    estimate_time,
    format_duration,
)


@pytest.fixture
def profile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "runtime_profile.json"
    monkeypatch.setenv("CF_RUNTIME_PROFILE", str(path))
    return path


def _build_sync_estimate() -> SyncEstimate:
    """A canned ``SyncEstimate`` covering the common extractor classes."""
    return SyncEstimate(
        schema_version=1,
        scanned_path="/tmp/canned",
        file_count=300,
        dir_count=10,
        total_raw_bytes=10 * 1024 * 1024,
        by_extractor=[
            ExtractorClassSummary(
                extractor_class="markdown",
                file_count=100,
                raw_bytes=4 * 1024 * 1024,
                est_chunks=200,
            ),
            ExtractorClassSummary(
                extractor_class="pdf",
                file_count=20,
                raw_bytes=6 * 1024 * 1024,
                est_chunks=100,
            ),
            ExtractorClassSummary(
                extractor_class="code",
                file_count=180,
                raw_bytes=2 * 1024 * 1024,
                est_chunks=400,
            ),
        ],
        documents_bytes=0,
        chunks_bytes=0,
        embeddings=[
            EmbedderSizing(
                name="qwen3_8b",
                dim=4096,
                n_chunks=700,
                raw_vector_bytes=0,
                hnsw_overhead_bytes=0,
                row_overhead_bytes=0,
                total_bytes=0,
            ),
        ],
        btree_index_bytes=0,
        total_bytes=0,
        compression_ratio=1.0,
        embedders_active=["qwen3_8b"],
    )


class _StubEmbedderCfg:
    def __init__(self, name: str, dim: int) -> None:
        self.name = name
        self.dimension = dim


class _StubConfig:
    def __init__(self, embedders: list[_StubEmbedderCfg]) -> None:
        self.embedders = embedders


def _build_config() -> _StubConfig:
    return _StubConfig([_StubEmbedderCfg("qwen3_8b", 4096)])


def test_heuristic_only_estimate_is_deterministic(profile_path: Path) -> None:
    """Same inputs, no profile → byte-identical TimeEstimate."""
    sync = _build_sync_estimate()
    cfg = _build_config()
    a = estimate_time(sync, cfg)  # type: ignore[arg-type]
    b = estimate_time(sync, cfg)  # type: ignore[arg-type]
    assert a == b
    assert a.calibration == "heuristic"
    assert a.profile_samples == 0
    assert a.schema_version == SCHEMA_VERSION


def test_estimate_has_all_five_phases_in_pipeline_order(profile_path: Path) -> None:
    te = estimate_time(_build_sync_estimate(), _build_config())  # type: ignore[arg-type]
    assert [p.name for p in te.phases] == ["scan", "extract", "chunk", "embed", "db_write"]
    # Every phase contributes; we never want a zero-total estimate when
    # the corpus has chunks and bytes.
    assert te.total_seconds > 0
    for phase in te.phases:
        assert phase.seconds >= 0


def test_total_equals_phase_sum(profile_path: Path) -> None:
    te = estimate_time(_build_sync_estimate(), _build_config())  # type: ignore[arg-type]
    assert te.total_seconds == pytest.approx(sum(p.seconds for p in te.phases))


def test_calibration_label_is_calibrated_when_profile_covers_all_rates(
    profile_path: Path,
) -> None:
    """Every per-phase rate in the profile → calibration label flips."""
    # Seed every phase the estimator will consult, including all three
    # extractor classes in the canned sync estimate.
    rp.record("scan", units=100, seconds=0.001, alpha=1.0)
    rp.record("db_write", units=100, seconds=0.01, alpha=1.0)
    for cls in ("markdown", "pdf", "code"):
        rp.record("extract", units=1024, seconds=0.0001, key=cls, alpha=1.0)
        rp.record("chunk", units=100, seconds=0.01, key=cls, alpha=1.0)
    rp.record("embed", units=100, seconds=1.0, key="qwen3_8b", alpha=1.0)

    te = estimate_time(_build_sync_estimate(), _build_config())  # type: ignore[arg-type]
    assert te.calibration == "calibrated"
    assert te.profile_samples > 0


def test_calibration_label_is_hybrid_with_partial_profile(profile_path: Path) -> None:
    """Profile has some but not all rates → hybrid."""
    rp.record("scan", units=100, seconds=0.001, alpha=1.0)
    # Deliberately don't seed extract/chunk/embed/db_write — they'll
    # fall back to heuristic constants.
    te = estimate_time(_build_sync_estimate(), _build_config())  # type: ignore[arg-type]
    assert te.calibration == "hybrid"


def test_profile_rate_overrides_heuristic(profile_path: Path) -> None:
    """A very-slow embed profile produces a slower overall estimate."""
    sync = _build_sync_estimate()
    cfg = _build_config()
    baseline = estimate_time(sync, cfg)  # type: ignore[arg-type]

    # 10x slower than the default heuristic.
    rp.record("embed", units=1, seconds=0.25, key="qwen3_8b", alpha=1.0)

    calibrated = estimate_time(sync, cfg)  # type: ignore[arg-type]
    assert calibrated.total_seconds > baseline.total_seconds
    embed_phase = next(p for p in calibrated.phases if p.name == "embed")
    # PhaseTime.source is the collapsed label across all per-embedder
    # rates — here only one embedder exists and it came from the
    # profile, so the label collapses to "calibrated".
    assert embed_phase.source == "calibrated"


def test_format_duration_spans_seconds_to_days() -> None:
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"
    assert format_duration(60) == "1m 0s"
    assert format_duration(125) == "2m 5s"
    assert format_duration(3700) == "1h 1m"
    assert format_duration(90_061) == "1d 1h"
    # NaN / negative inputs degrade to em-dash rather than raising.
    assert format_duration(float("nan")) == "—"
    assert format_duration(-1) == "—"


def test_time_estimate_is_dataclass_serialisable(profile_path: Path) -> None:
    from dataclasses import asdict

    te = estimate_time(_build_sync_estimate(), _build_config())  # type: ignore[arg-type]
    payload = asdict(te)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "phases" in payload
    assert isinstance(payload["phases"], list)
    assert {p["name"] for p in payload["phases"]} == {
        "scan",
        "extract",
        "chunk",
        "embed",
        "db_write",
    }
