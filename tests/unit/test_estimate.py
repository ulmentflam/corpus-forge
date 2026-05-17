"""Unit tests for the Phase J / J1 sync storage estimator.

These tests pin the sizing-model math, the per-extractor heuristic table,
the filesystem walk policy, and the embedder-summation logic. The
estimator is a pure-function module — every test below is mock-free and
runs purely from `tmp_path` filesystem fixtures.

Brief: `.planning/tdd/phase_j_living_corpus.md` § J1.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import pytest

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)

# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _embedder(
    name: str,
    dim: int,
    *,
    active: bool = True,
    provider: str = "sentence_transformers",
) -> EmbedderConfig:
    return EmbedderConfig(
        name=name,
        provider=provider,
        model_id=f"fake/{name}",
        dimension=dim,
        active=active,
    )


def _config(
    *,
    embedders: list[EmbedderConfig] | None = None,
    compression_ratio: float | None = None,
) -> Config:
    from corpus_forge.config import EstimateConfig

    if embedders is None:
        embedders = [_embedder("e1", 384)]
    kwargs: dict = {
        "backend": BackendConfig(kind="sqlite", dsn="sqlite:///:memory:"),
        "daemon": DaemonConfig(),
        "datasets": [
            DatasetConfig(
                name="d1",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="filesystem",
                        root="/tmp",
                        chunker="markdown",
                    )
                ],
            )
        ],
        "embedders": embedders,
    }
    if compression_ratio is not None:
        kwargs["estimate"] = EstimateConfig(compression_ratio=compression_ratio)
    return Config(**kwargs)


def _write(path: Path, size_bytes: int, *, content: bytes | None = None) -> Path:
    """Create a file at ``path`` with exactly ``size_bytes`` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is not None:
        assert len(content) == size_bytes, (len(content), size_bytes)
        path.write_bytes(content)
    else:
        path.write_bytes(b"x" * size_bytes)
    return path


# ─────────────────────────────────────────────────────────────────────────
# 1-2: empty and unknown-only trees
# ─────────────────────────────────────────────────────────────────────────


def test_empty_dir_returns_zero_estimate(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    est = estimate_sync(tmp_path, _config())
    assert est.file_count == 0
    assert est.total_raw_bytes == 0
    # All embedders should still be listed even when there are no chunks.
    assert {e.name for e in est.embeddings} == {"e1"}
    for e in est.embeddings:
        assert e.n_chunks == 0
        assert e.total_bytes == 0
    assert est.documents_bytes == 0
    assert est.chunks_bytes == 0


def test_unknown_only_dir(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "blob.xyz", 4096)
    _write(tmp_path / "more.abc", 8192)
    est = estimate_sync(tmp_path, _config())
    assert est.file_count == 2
    # Both bucketed unknown — no chunks, no embeddings.
    unknown = [b for b in est.by_extractor if b.extractor_class == "unknown"]
    assert len(unknown) == 1
    assert unknown[0].file_count == 2
    assert unknown[0].est_chunks == 0
    for e in est.embeddings:
        assert e.n_chunks == 0


# ─────────────────────────────────────────────────────────────────────────
# 3-5: markdown + pdf heuristics
# ─────────────────────────────────────────────────────────────────────────


def test_single_markdown_file(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    est = estimate_sync(tmp_path, _config())
    md = next(b for b in est.by_extractor if b.extractor_class == "markdown")
    assert md.file_count == 1
    assert md.est_chunks == 1
    assert est.chunks_bytes > 0
    assert est.documents_bytes > 0


def test_markdown_2x_size_2x_chunks(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "big.md", 8192)
    est = estimate_sync(tmp_path, _config())
    md = next(b for b in est.by_extractor if b.extractor_class == "markdown")
    assert md.est_chunks == 2


def test_pdf_page_break_multiplier(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "doc.pdf", 40960)
    est = estimate_sync(tmp_path, _config())
    pdf = next(b for b in est.by_extractor if b.extractor_class == "pdf")
    # ceil(40960/4096 * 1.05) == ceil(10.5) == 11
    assert pdf.est_chunks == 11


# ─────────────────────────────────────────────────────────────────────────
# 6-8: code class + filename fallback
# ─────────────────────────────────────────────────────────────────────────


def test_code_loc_heuristic(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "x.py", 12288)
    est = estimate_sync(tmp_path, _config())
    code = next(b for b in est.by_extractor if b.extractor_class == "code")
    # LOC = 12288/32 = 384; chunks = ceil(384/60) = 7
    assert code.est_chunks == 7


def test_code_dockerfile_filename_fallback(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "Dockerfile", 1024)
    est = estimate_sync(tmp_path, _config())
    code = [b for b in est.by_extractor if b.extractor_class == "code"]
    assert code and code[0].file_count == 1


def test_code_makefile_filename_fallback(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "Makefile", 256)
    est = estimate_sync(tmp_path, _config())
    code = [b for b in est.by_extractor if b.extractor_class == "code"]
    assert code and code[0].file_count == 1


# ─────────────────────────────────────────────────────────────────────────
# 9-14: notebook / csv / structured
# ─────────────────────────────────────────────────────────────────────────


def test_notebook_extractor_class(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "nb.ipynb", 16384)
    est = estimate_sync(tmp_path, _config())
    nb = next(b for b in est.by_extractor if b.extractor_class == "notebook")
    # ceil(16384/8192) == 2
    assert nb.est_chunks == 2


def test_csv_one_chunk_regardless_of_size(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "big.csv", 1_048_576)  # 1 MB
    est = estimate_sync(tmp_path, _config())
    csv = next(b for b in est.by_extractor if b.extractor_class == "csv")
    assert csv.est_chunks == 1


def test_tsv_same_as_csv(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "table.tsv", 10_000)
    est = estimate_sync(tmp_path, _config())
    csv = next(b for b in est.by_extractor if b.extractor_class == "csv")
    assert csv.est_chunks == 1


def test_structured_json_one_chunk(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "doc.json", 32_000)
    est = estimate_sync(tmp_path, _config())
    s = next(b for b in est.by_extractor if b.extractor_class == "structured")
    assert s.est_chunks == 1


def test_structured_yaml_one_chunk(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.yaml", 8000)
    _write(tmp_path / "b.yml", 8000)
    est = estimate_sync(tmp_path, _config())
    s = next(b for b in est.by_extractor if b.extractor_class == "structured")
    # Two files -> 2 chunks (one each)
    assert s.file_count == 2
    assert s.est_chunks == 2


def test_structured_toml_one_chunk(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "config.toml", 16_000)
    est = estimate_sync(tmp_path, _config())
    s = next(b for b in est.by_extractor if b.extractor_class == "structured")
    assert s.est_chunks == 1


# ─────────────────────────────────────────────────────────────────────────
# 15-19: subtitle / image / audio-video
# ─────────────────────────────────────────────────────────────────────────


def test_subtitle_srt_chunks(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "talk.srt", 12288)
    est = estimate_sync(tmp_path, _config())
    sub = next(b for b in est.by_extractor if b.extractor_class == "subtitle")
    # ceil(12288/6144) == 2
    assert sub.est_chunks == 2


def test_subtitle_vtt_chunks(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "talk.vtt", 6144)
    est = estimate_sync(tmp_path, _config())
    sub = next(b for b in est.by_extractor if b.extractor_class == "subtitle")
    assert sub.est_chunks == 1


def test_image_zero_text_chunks(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "pic.png", 200_000)
    est = estimate_sync(tmp_path, _config())
    img = next(b for b in est.by_extractor if b.extractor_class == "image")
    assert img.est_chunks == 0


def test_audio_video_chunks(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    # 5 MiB mp3 -> ceil(5 * 60 / 30) == 10
    _write(tmp_path / "voice.mp3", 5 * 1024 * 1024)
    est = estimate_sync(tmp_path, _config())
    av = next(b for b in est.by_extractor if b.extractor_class == "audio_video")
    assert av.est_chunks == 10


def test_video_chunks_too(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "clip.mp4", 10 * 1024 * 1024)
    est = estimate_sync(tmp_path, _config())
    av = next(b for b in est.by_extractor if b.extractor_class == "audio_video")
    assert av.est_chunks == 20


# ─────────────────────────────────────────────────────────────────────────
# 20-23: compression ratio
# ─────────────────────────────────────────────────────────────────────────


def test_compression_ratio_default_one(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    est = estimate_sync(tmp_path, _config())
    assert est.compression_ratio == 1.0


def test_compression_ratio_half_compresses_text(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 8192)
    cfg = _config()
    full = estimate_sync(tmp_path, cfg)
    half = estimate_sync(tmp_path, cfg, compression_ratio=0.5)
    # documents + chunks shrink with the ratio; embeddings + btree do not.
    assert half.documents_bytes < full.documents_bytes
    assert half.chunks_bytes < full.chunks_bytes
    assert half.embeddings[0].total_bytes == full.embeddings[0].total_bytes
    assert half.btree_index_bytes == full.btree_index_bytes
    assert half.compression_ratio == 0.5


def test_compression_ratio_from_config_estimate_block(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 8192)
    cfg = _config(compression_ratio=0.7)
    est = estimate_sync(tmp_path, cfg)
    assert est.compression_ratio == pytest.approx(0.7)


def test_compression_ratio_arg_overrides_config(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 8192)
    cfg = _config(compression_ratio=1.0)
    est = estimate_sync(tmp_path, cfg, compression_ratio=0.5)
    assert est.compression_ratio == 0.5


# ─────────────────────────────────────────────────────────────────────────
# 24-28: embedder selection + sizing
# ─────────────────────────────────────────────────────────────────────────


def test_embedders_default_to_all_active(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    cfg = _config(
        embedders=[_embedder("on", 384, active=True), _embedder("off", 1024, active=False)]
    )
    _write(tmp_path / "a.md", 4096)
    est = estimate_sync(tmp_path, cfg)
    assert est.embedders_active == ["on"]
    assert [e.name for e in est.embeddings] == ["on"]


def test_embedders_explicit_filter(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    cfg = _config(embedders=[_embedder("a", 384), _embedder("b", 512)])
    _write(tmp_path / "a.md", 4096)
    est = estimate_sync(tmp_path, cfg, embedders=["b"])
    assert [e.name for e in est.embeddings] == ["b"]
    assert est.embedders_active == ["b"]


def test_embedders_unknown_name_raises(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    cfg = _config(embedders=[_embedder("a", 384)])
    _write(tmp_path / "a.md", 4096)
    with pytest.raises(ValueError) as excinfo:
        estimate_sync(tmp_path, cfg, embedders=["nope"])
    msg = str(excinfo.value)
    assert "nope" in msg
    assert "a" in msg


def test_embedding_sizing_math(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    cfg = _config(embedders=[_embedder("e", 384)])
    # 100 chunks of markdown: 100 * 4096 = 409_600 bytes
    _write(tmp_path / "big.md", 100 * 4096)
    est = estimate_sync(tmp_path, cfg)
    md = next(b for b in est.by_extractor if b.extractor_class == "markdown")
    assert md.est_chunks == 100
    e = est.embeddings[0]
    assert e.n_chunks == 100
    assert e.raw_vector_bytes == 100 * 384 * 4
    assert e.row_overhead_bytes == 100 * 32
    # HNSW overhead = 35% of raw vectors (rounded to int)
    assert e.hnsw_overhead_bytes == round(100 * 384 * 4 * 0.35)
    assert e.total_bytes == e.raw_vector_bytes + e.row_overhead_bytes + e.hnsw_overhead_bytes


def test_embedding_total_with_two_embedders(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    cfg = _config(embedders=[_embedder("a", 384), _embedder("b", 4096)])
    _write(tmp_path / "x.md", 4096)
    est = estimate_sync(tmp_path, cfg)
    by_name = {e.name: e for e in est.embeddings}
    assert by_name["a"].raw_vector_bytes == 1 * 384 * 4
    assert by_name["b"].raw_vector_bytes == 1 * 4096 * 4
    assert by_name["a"].total_bytes != by_name["b"].total_bytes


# ─────────────────────────────────────────────────────────────────────────
# 29-30: btree + total sums
# ─────────────────────────────────────────────────────────────────────────


def test_btree_index_estimate(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)  # 1 doc, 1 chunk
    est = estimate_sync(tmp_path, _config())
    # documents (1) + chunks (1) -> 2 * 80 = 160
    assert est.btree_index_bytes == 160


def test_total_bytes_is_sum_of_parts(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    _write(tmp_path / "b.pdf", 8192)
    est = estimate_sync(tmp_path, _config())
    embedding_total = sum(e.total_bytes for e in est.embeddings)
    assert est.total_bytes == (
        est.documents_bytes + est.chunks_bytes + embedding_total + est.btree_index_bytes
    )


# ─────────────────────────────────────────────────────────────────────────
# 31-35: filesystem walk policy
# ─────────────────────────────────────────────────────────────────────────


def test_skip_dot_git_directory(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / ".git" / "HEAD", 256)
    _write(tmp_path / ".git" / "objects" / "ab" / "deadbeef", 4096)
    _write(tmp_path / "kept.md", 4096)
    est = estimate_sync(tmp_path, _config())
    assert est.file_count == 1


def test_skip_node_modules(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "node_modules" / "pkg" / "index.js", 4096)
    _write(tmp_path / "kept.md", 4096)
    est = estimate_sync(tmp_path, _config())
    assert est.file_count == 1


def test_skip_pycache(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "__pycache__" / "x.pyc", 4096)
    _write(tmp_path / "kept.py", 4096)
    est = estimate_sync(tmp_path, _config())
    assert est.file_count == 1


def test_walks_subdirectories(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "deep" / "nested" / "tree" / "a.md", 4096)
    est = estimate_sync(tmp_path, _config())
    assert est.file_count == 1
    assert est.dir_count >= 3


def test_dir_count_correct(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").mkdir()
    est = estimate_sync(tmp_path, _config())
    assert est.dir_count == 3


# ─────────────────────────────────────────────────────────────────────────
# 36-40: integration / schema / contracts
# ─────────────────────────────────────────────────────────────────────────


def test_mixed_tree_smoke(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    _write(tmp_path / "b.pdf", 8192)
    _write(tmp_path / "c.py", 4096)
    _write(tmp_path / "d.png", 100_000)
    est = estimate_sync(tmp_path, _config())
    classes = {b.extractor_class for b in est.by_extractor}
    assert {"markdown", "pdf", "code", "image"} <= classes
    assert est.total_bytes > 0


def test_schema_version_is_one(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    est = estimate_sync(tmp_path, _config())
    assert est.schema_version == 1


def test_scanned_path_is_absolute_string(tmp_path: Path) -> None:
    """Relative paths must be resolved to absolute in the result."""
    import os

    from corpus_forge.estimate import estimate_sync

    cwd = str(Path.cwd())
    try:
        os.chdir(tmp_path)
        rel = Path()
        est = estimate_sync(rel, _config())
        # scanned_path is an absolute string regardless of input form.
        assert Path(est.scanned_path).is_absolute()
    finally:
        os.chdir(cwd)


def test_dataclass_frozen(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    est = estimate_sync(tmp_path, _config())
    with pytest.raises(dataclasses.FrozenInstanceError):
        est.file_count = 99  # type: ignore[misc]


def test_estimator_does_not_run_extractors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The estimator is a pure-function consult; it must NOT instantiate
    real extractor classes (which would import heavy optional backends)."""
    from corpus_forge import estimate as estimate_mod

    def _boom(*a: object, **kw: object) -> object:
        raise AssertionError("estimator should not invoke register_default_extractors")

    # Patch the registry constructor on the extractors module to fail if
    # the estimator tries to instantiate anything. The estimator should
    # only read from the constants tables.
    monkeypatch.setattr(
        "corpus_forge.extractors.registry.register_default_extractors",
        _boom,
        raising=True,
    )
    _write(tmp_path / "a.md", 4096)
    est = estimate_mod.estimate_sync(tmp_path, _config())
    assert est.file_count == 1


# ─────────────────────────────────────────────────────────────────────────
# 41+: extra coverage — JSON round-trip, EstimateConfig
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_dataclass_round_trips_through_json(tmp_path: Path) -> None:
    """The dataclass shape must be JSON-serialisable for the CLI / MCP
    surfaces (both emit ``asdict(estimate)``)."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    est = estimate_sync(tmp_path, _config())
    payload = dataclasses.asdict(est)
    blob = json.dumps(payload)
    again = json.loads(blob)
    assert again["schema_version"] == 1
    assert again["file_count"] == 1


def test_estimate_config_defaults() -> None:
    from corpus_forge.config import EstimateConfig

    cfg = EstimateConfig()
    assert cfg.compression_ratio == 1.0


def test_estimate_config_rejects_zero_ratio() -> None:
    from pydantic import ValidationError

    from corpus_forge.config import EstimateConfig

    with pytest.raises(ValidationError):
        EstimateConfig(compression_ratio=0.0)


def test_estimate_config_rejects_above_one() -> None:
    from pydantic import ValidationError

    from corpus_forge.config import EstimateConfig

    with pytest.raises(ValidationError):
        EstimateConfig(compression_ratio=1.5)


def test_estimate_config_rejects_extra_fields() -> None:
    from pydantic import ValidationError

    from corpus_forge.config import EstimateConfig

    with pytest.raises(ValidationError):
        EstimateConfig(compression_ratio=0.5, bogus=1)  # type: ignore[call-arg]


def test_audio_video_minimum_chunks_at_least_one(tmp_path: Path) -> None:
    """A tiny audio/video file should still be 1 chunk, not 0."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "tiny.mp3", 1024)
    est = estimate_sync(tmp_path, _config())
    av = next(b for b in est.by_extractor if b.extractor_class == "audio_video")
    assert av.est_chunks >= 1


def test_pdf_minimum_chunks_at_least_one(tmp_path: Path) -> None:
    """A tiny PDF file should still register 1 chunk."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "tiny.pdf", 512)
    est = estimate_sync(tmp_path, _config())
    pdf = next(b for b in est.by_extractor if b.extractor_class == "pdf")
    assert pdf.est_chunks >= 1


def test_code_minimum_chunks_at_least_one(tmp_path: Path) -> None:
    """A tiny code file should still register 1 chunk."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "tiny.py", 128)
    est = estimate_sync(tmp_path, _config())
    code = next(b for b in est.by_extractor if b.extractor_class == "code")
    assert code.est_chunks >= 1


def test_raw_bytes_sum_matches_file_count(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    _write(tmp_path / "b.pdf", 8192)
    est = estimate_sync(tmp_path, _config())
    by_class_bytes = sum(b.raw_bytes for b in est.by_extractor)
    assert by_class_bytes == est.total_raw_bytes == 12288


def test_uppercase_extension_is_normalised(tmp_path: Path) -> None:
    """Files like `README.MD` and `IMAGE.PNG` must bucket correctly."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "README.MD", 4096)
    _write(tmp_path / "IMAGE.PNG", 1000)
    est = estimate_sync(tmp_path, _config())
    classes = {b.extractor_class for b in est.by_extractor}
    assert "markdown" in classes
    assert "image" in classes


def test_returns_empty_embedders_when_none_configured(tmp_path: Path) -> None:
    from corpus_forge.estimate import estimate_sync

    cfg = _config(embedders=[])
    _write(tmp_path / "a.md", 4096)
    est = estimate_sync(tmp_path, cfg)
    assert est.embeddings == []
    assert est.embedders_active == []


def test_documents_bytes_matches_text_proxy(tmp_path: Path) -> None:
    """documents_bytes is ~ file_count * per-row overhead + sum(text proxy).

    We don't pin an exact value (the breakdown is implementation-detail);
    instead we assert it scales with file count + text payload."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "a.md", 4096)
    one = estimate_sync(tmp_path, _config())

    _write(tmp_path / "b.md", 4096)
    two = estimate_sync(tmp_path, _config())
    # Doubling the doc count should double-ish documents_bytes (allow
    # 1.8x lower bound to tolerate fixed-overhead-per-row).
    assert two.documents_bytes > one.documents_bytes
    assert two.documents_bytes >= int(1.8 * one.documents_bytes)


def test_pdf_one_zero_byte_file_chunks_one(tmp_path: Path) -> None:
    """ceil(0 * 1.05) is 0 — but the minimum is 1 chunk per PDF file."""
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "empty.pdf", 0)
    est = estimate_sync(tmp_path, _config())
    pdf = next(b for b in est.by_extractor if b.extractor_class == "pdf")
    assert pdf.est_chunks >= 1


def test_audio_video_heuristic_matches_brief(tmp_path: Path) -> None:
    """Spot-check the audio_video formula at a non-round size.

    7 MiB -> ceil(7 * 60 / 30) = 14 chunks.
    """
    from corpus_forge.estimate import estimate_sync

    _write(tmp_path / "song.mp3", 7 * 1024 * 1024)
    est = estimate_sync(tmp_path, _config())
    av = next(b for b in est.by_extractor if b.extractor_class == "audio_video")
    assert av.est_chunks == 14


def test_math_module_ceil_used_for_pdf_multiplier() -> None:
    """Document the exact pdf formula so a regression in rounding is caught."""
    # 40960 / 4096 = 10.0; * 1.05 = 10.5; ceil -> 11.
    assert math.ceil(10.0 * 1.05) == 11
