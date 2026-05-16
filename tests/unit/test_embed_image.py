"""Phase G (G-15) — :func:`backfill_image_embedder` + ``--image`` CLI flag."""

from __future__ import annotations

import base64
import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import corpus_forge.embed as embed_module
from corpus_forge.cli import app
from corpus_forge.embed import _resolve_image_bytes

# ── _resolve_image_bytes ────────────────────────────────────────────────


def test_resolve_inline_base64() -> None:
    raw = b"\x89PNG\r\n\x1a\nfake"
    out = _resolve_image_bytes({"image_bytes_b64": base64.b64encode(raw).decode("ascii")})
    assert out == raw


def test_resolve_inline_base64_invalid() -> None:
    assert _resolve_image_bytes({"image_bytes_b64": "%%not base64%%"}) is None


def test_resolve_image_path(tmp_path: Path) -> None:
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNGfake")
    out = _resolve_image_bytes({"image_path": str(p)})
    assert out == b"\x89PNGfake"


def test_resolve_image_path_missing(tmp_path: Path) -> None:
    out = _resolve_image_bytes({"image_path": str(tmp_path / "missing.png")})
    assert out is None


def test_resolve_returns_none_when_no_path() -> None:
    assert _resolve_image_bytes({}) is None


def test_resolve_inline_b64_takes_priority(tmp_path: Path) -> None:
    raw = b"inline-bytes"
    p = tmp_path / "img.png"
    p.write_bytes(b"file-bytes")
    out = _resolve_image_bytes(
        {
            "image_bytes_b64": base64.b64encode(raw).decode("ascii"),
            "image_path": str(p),
        }
    )
    assert out == raw


# ── backfill_image_embedder ─────────────────────────────────────────────


def _mk_backend(missing_batches: list[list[tuple[int, dict]]]) -> MagicMock:
    """Return a MagicMock backend that yields the supplied batches.

    Subsequent calls to ``image_chunks_missing_embedding`` return the
    batches in order then an empty list.
    """
    backend = MagicMock()
    backend.kind = "sqlite"
    backend.register_multimodal_embedder.return_value = 7
    backend.find_dataset_id_by_name.return_value = 1

    state = {"calls": 0}

    def _missing(emb_id, *, limit=128):
        idx = state["calls"]
        state["calls"] += 1
        return iter(missing_batches[idx]) if idx < len(missing_batches) else iter([])

    backend.image_chunks_missing_embedding.side_effect = _missing
    backend.write_image_embeddings.return_value = None
    return backend


@contextlib.contextmanager
def _patch_config_and_backend(backend: MagicMock):
    """Context manager: swap in a fake config + backend for embed.main()."""
    fake_config = MagicMock()
    fake_config.backend.kind = "sqlite"
    fake_config.backend.dsn = ":memory:"
    fake_config.backend.schema = "corpus"

    def _backend_ctor(*_a, **_kw):
        return backend

    with (
        patch("corpus_forge.embed.Config.load", return_value=fake_config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", _backend_ctor),
    ):
        yield


def test_backfill_skips_unresolvable_chunks(tmp_path: Path) -> None:
    """Chunks without resolvable image bytes are logged + skipped."""
    backend = _mk_backend([[(1, {})]])

    fake_emb = MagicMock()
    fake_emb.name = "clip_local"
    fake_emb.model_id = "clip-ViT-B-32"
    fake_emb.dimension = 4
    fake_emb.encode_image.return_value = [[0.0, 0.0, 0.0, 0.0]]

    with (
        _patch_config_and_backend(backend),
        patch("corpus_forge.embedders.clip_local.ClipLocalEmbedder", return_value=fake_emb),
    ):
        processed = embed_module.backfill_image_embedder("clip_local")
    assert processed == 0
    # Backend never received writes (no resolvable bytes).
    backend.write_image_embeddings.assert_not_called()


def test_backfill_embeds_resolvable_chunks(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNGfake")

    backend = _mk_backend([[(1, {"image_path": str(p)}), (2, {"image_path": str(p)})], []])

    fake_emb = MagicMock()
    fake_emb.name = "clip_local"
    fake_emb.model_id = "clip-ViT-B-32"
    fake_emb.dimension = 4
    fake_emb.encode_image.return_value = [
        [0.1, 0.2, 0.3, 0.4],
        [0.5, 0.6, 0.7, 0.8],
    ]

    with (
        _patch_config_and_backend(backend),
        patch("corpus_forge.embedders.clip_local.ClipLocalEmbedder", return_value=fake_emb),
    ):
        processed = embed_module.backfill_image_embedder("clip_local")
    assert processed == 2
    backend.write_image_embeddings.assert_called_once()
    _args, _kwargs = backend.write_image_embeddings.call_args
    written_pairs = _args[1] if len(_args) >= 2 else _kwargs["pairs"]
    assert [p[0] for p in written_pairs] == [1, 2]


def test_backfill_respects_limit(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNGfake")

    backend = _mk_backend(
        [
            [(1, {"image_path": str(p)}), (2, {"image_path": str(p)}), (3, {"image_path": str(p)})],
            [],
        ]
    )
    fake_emb = MagicMock()
    fake_emb.name = "clip_local"
    fake_emb.model_id = "m"
    fake_emb.dimension = 4
    fake_emb.encode_image.return_value = [[0.0] * 4, [0.0] * 4]

    with (
        _patch_config_and_backend(backend),
        patch("corpus_forge.embedders.clip_local.ClipLocalEmbedder", return_value=fake_emb),
    ):
        processed = embed_module.backfill_image_embedder("clip_local", limit=2)
    assert processed == 2


def test_backfill_dataset_filter_unknown_raises() -> None:
    backend = _mk_backend([])
    backend.find_dataset_id_by_name.return_value = None
    fake_emb = MagicMock()
    fake_emb.name = "x"
    fake_emb.model_id = "m"
    fake_emb.dimension = 4

    with (
        _patch_config_and_backend(backend),
        patch("corpus_forge.embedders.clip_local.ClipLocalEmbedder", return_value=fake_emb),
        pytest.raises(ValueError, match=r"not found"),
    ):
        embed_module.backfill_image_embedder("clip_local", dataset_name="missing")


# ── CLI wiring ──────────────────────────────────────────────────────────


def test_cli_embed_image_flag_routes_to_image_path() -> None:
    runner = CliRunner()
    with patch("corpus_forge.embed.main") as mp:
        result = runner.invoke(app, ["embed", "-e", "clip_local", "--image"])
    assert result.exit_code == 0, result.output
    mp.assert_called_once()
    _args, kwargs = mp.call_args
    assert kwargs["image"] is True


def test_cli_embed_without_image_flag_routes_to_text_path() -> None:
    runner = CliRunner()
    with patch("corpus_forge.embed.main") as mp:
        runner.invoke(app, ["embed", "-e", "qwen3_8b"])
    mp.assert_called_once()
    _args, kwargs = mp.call_args
    assert kwargs["image"] is False
