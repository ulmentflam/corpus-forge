"""The telemetry heartbeat is wired into daemon startup and the embed entry.

rfc-fleet-1: ``record host + models once per process start, never on a
hot path``.  These tests assert the single call site in each entry
point fires with the resolved backend + config, and that the heartbeat
is a best-effort hook that does not change the entry point's behaviour.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from corpus_forge.daemon import run_daemon


def test_daemon_startup_calls_heartbeat() -> None:
    """``run_daemon`` records a heartbeat with the resolved backend + config."""
    config = MagicMock()
    config.datasets = []  # no sync engines to construct
    config.host_id.return_value = "test-host"
    backend = MagicMock()

    with (
        patch("corpus_forge.daemon._get_any_backend", return_value=backend),
        patch("corpus_forge.telemetry_registry.heartbeat") as mock_hb,
    ):
        run_daemon(config)

    mock_hb.assert_called_once_with(backend, config)


def test_daemon_heartbeat_fires_even_when_backend_unreachable() -> None:
    """A ``None`` backend still triggers the (no-op) heartbeat call."""
    config = MagicMock()
    config.datasets = []
    config.host_id.return_value = "test-host"

    with (
        patch("corpus_forge.daemon._get_any_backend", return_value=None),
        patch("corpus_forge.telemetry_registry.heartbeat") as mock_hb,
    ):
        run_daemon(config)

    mock_hb.assert_called_once_with(None, config)


def test_embed_entry_calls_heartbeat(tmp_path: Path) -> None:
    """``backfill_embedder`` records a heartbeat after migrate, before backfill."""
    from corpus_forge import embed as embed_mod

    config = MagicMock()
    config.backend.kind = "sqlite"
    config.backend.dsn = str(tmp_path / "corpus.db")
    config.backend.schema = "corpus"
    ec = MagicMock()
    ec.name = "qwen"
    ec.active = True
    config.embedders = [ec]

    backend = MagicMock()
    embedder = MagicMock()
    embedder.extensions = []
    # Stop the backfill loop immediately: no chunks pending.
    backend.count_chunks_missing_embedding.return_value = 0
    backend.chunks_missing_embedding.return_value = iter([])

    with (
        patch.object(embed_mod.Config, "load", return_value=config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        patch.object(embed_mod, "register_from_config", return_value=embedder),
        patch("corpus_forge.telemetry_registry.heartbeat") as mock_hb,
    ):
        embed_mod.backfill_embedder("qwen")

    mock_hb.assert_called_once_with(backend, config)
    # Migrate must have run before the heartbeat (tables exist first).
    backend.migrate.assert_called_once()
