"""Passive embed-run telemetry tests (rfc-fleet-1 item 5).

Covers the ``source="embed-run"`` ``model_benchmarks`` rows written by
:func:`corpus_forge.embed.backfill_embedder`:

* end-of-run row written with the aggregate rate;
* checkpoint rows fire every ``_TELEMETRY_CHECKPOINT_EVERY`` chunks so a
  crashed run still reports;
* the rate math (chunks / wall seconds);
* failure isolation — a backend whose ``insert_model_benchmark`` raises
  must NOT break the backfill;
* the standalone helper's zero-work / zero-time guards.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge import embed as embed_mod
from corpus_forge.config import Config

# ---------------------------------------------------------------------------
# _write_embed_run_telemetry — direct unit tests
# ---------------------------------------------------------------------------


class _Cfg:
    def host_id(self) -> str:
        return "host-1"


class _EmbCfg:
    provider = "openai"
    model_id = "text-embedding-3-small"
    batch_size = 32


def test_helper_writes_row_with_rate() -> None:
    backend = MagicMock()
    embed_mod._write_embed_run_telemetry(
        backend,
        _Cfg(),
        _EmbCfg(),
        transport="api",
        device="remote",
        processed=100,
        elapsed_s=4.0,
    )
    backend.insert_model_benchmark.assert_called_once()
    kwargs = backend.insert_model_benchmark.call_args.kwargs
    assert kwargs["source"] == "embed-run"
    assert kwargs["model_key"] == "openai:text-embedding-3-small"
    assert kwargs["sample_chunks"] == 100
    assert kwargs["chunks_per_s"] == pytest.approx(25.0)
    assert kwargs["transport"] == "api"
    assert kwargs["device"] == "remote"
    assert kwargs["latency_p50_ms"] is None
    assert kwargs["batch_size"] == 32


def test_helper_zero_work_skips() -> None:
    backend = MagicMock()
    embed_mod._write_embed_run_telemetry(
        backend, _Cfg(), _EmbCfg(), transport="local", device="cpu", processed=0, elapsed_s=5.0
    )
    backend.insert_model_benchmark.assert_not_called()


def test_helper_zero_time_skips() -> None:
    backend = MagicMock()
    embed_mod._write_embed_run_telemetry(
        backend, _Cfg(), _EmbCfg(), transport="local", device="cpu", processed=10, elapsed_s=0.0
    )
    backend.insert_model_benchmark.assert_not_called()


def test_helper_failure_isolated() -> None:
    backend = MagicMock()
    backend.insert_model_benchmark.side_effect = RuntimeError("no table")
    # Must not raise.
    embed_mod._write_embed_run_telemetry(
        backend, _Cfg(), _EmbCfg(), transport="local", device="cpu", processed=10, elapsed_s=1.0
    )


# ---------------------------------------------------------------------------
# backfill_embedder — end-of-run + checkpoint integration
# ---------------------------------------------------------------------------


def _embedder_config_mock() -> MagicMock:
    cfg = MagicMock()
    cfg.name = "test-embedder"
    cfg.provider = "openai"
    cfg.model_id = "text-embedding-3-small"
    cfg.dimension = 4
    cfg.normalize = True
    cfg.distance = "cosine"
    cfg.active = True
    cfg.batch_size = 32
    cfg.device = "auto"
    cfg.api_key_env = "OPENAI_API_KEY"
    cfg.base_url = None
    cfg.extensions = []
    return cfg


def _run_backfill(
    *,
    pending_pages: list[list[tuple[int, str, str]]],
    insert_side_effect: Any = None,
) -> MagicMock:
    """Drive ``backfill_embedder`` with a scripted backend, return the backend mock.

    ``pending_pages`` is consumed one page per ``chunks_missing_embedding``
    call; an empty trailing page ends the loop.
    """

    ec = _embedder_config_mock()

    with patch.object(Config, "load") as mock_load:
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://test@test/memory"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [ec]
        mock_config.host_id.return_value = "host-1"
        mock_load.return_value = mock_config

        embedder = MagicMock()
        embedder.name = "test-embedder"
        embedder.extensions = []
        embedder.last_failed_indices = []

        def _encode(texts: Any) -> Any:
            return [[0.1] * 4 for _ in texts]

        embedder.encode.side_effect = _encode

        backend = MagicMock()
        backend.register_embedder.return_value = 1
        backend.count_chunks_missing_embedding.return_value = sum(len(p) for p in pending_pages)
        pages = iter([*pending_pages, []])
        backend.chunks_missing_embedding.side_effect = lambda *a, **k: next(pages, [])
        if insert_side_effect is not None:
            backend.insert_model_benchmark.side_effect = insert_side_effect

        with (
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
            patch("corpus_forge.embed.register_from_config", return_value=embedder),
            patch("corpus_forge.embed.registry.register", return_value=embedder),
            patch("corpus_forge.telemetry_registry.heartbeat", lambda b, c: None),
            patch("corpus_forge.admin.bench.resolve_device", lambda t: "cpu"),
        ):
            embed_mod.backfill_embedder("test-embedder")

    return backend


def test_backfill_writes_end_of_run_row() -> None:
    backend = _run_backfill(pending_pages=[[(1, "a", ""), (2, "b", ""), (3, "c", "")]])
    # One end-of-run row (3 chunks < checkpoint interval → no checkpoint).
    embed_runs = [
        c
        for c in backend.insert_model_benchmark.call_args_list
        if c.kwargs["source"] == "embed-run"
    ]
    assert len(embed_runs) == 1
    assert embed_runs[0].kwargs["sample_chunks"] == 3
    assert embed_runs[0].kwargs["transport"] == "local"


def test_backfill_checkpoint_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lower the checkpoint interval so a small test triggers it.
    monkeypatch.setattr(embed_mod, "_TELEMETRY_CHECKPOINT_EVERY", 2)
    # Two pages of 2 chunks each → processed hits 2 (checkpoint) then 4
    # (checkpoint), plus the end-of-run row.
    backend = _run_backfill(
        pending_pages=[[(1, "a", ""), (2, "b", "")], [(3, "c", ""), (4, "d", "")]]
    )
    embed_runs = [
        c
        for c in backend.insert_model_benchmark.call_args_list
        if c.kwargs["source"] == "embed-run"
    ]
    # 2 checkpoints + 1 end-of-run = 3 rows.
    assert len(embed_runs) == 3
    sample_counts = [c.kwargs["sample_chunks"] for c in embed_runs]
    assert sample_counts == [2, 4, 4]


def test_backfill_telemetry_failure_does_not_break_run() -> None:
    # insert_model_benchmark raises every time; backfill must still finish.
    backend = _run_backfill(
        pending_pages=[[(1, "a", ""), (2, "b", "")]],
        insert_side_effect=RuntimeError("telemetry down"),
    )
    # Real work still happened — write_embeddings called for the 2 chunks.
    backend.write_embeddings.assert_called()
