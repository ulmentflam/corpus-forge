"""Phase L Wave 4 — ingest --once progress + logger taxonomy.

Validates that ``ingest_once`` wraps each source's per-file loop in the
shared progress factory (so the bookend INFO lines land in the rotating
log) and that the dedicated ``corpus_forge.ingest.*`` taxonomy loggers
fire on scan start/end, extractor failure, and chunk milestones.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


def _stub_raw_doc(uri: str):
    """Cheap stand-in for a ``RawDocument`` the source produces."""

    raw = MagicMock()
    raw.source_uri = uri
    raw.text = "hello"
    raw.metadata = {}
    return raw


def _build_stub_config(*, source_root):
    """Single-dataset, single filesystem source config."""

    source_cfg = MagicMock()
    source_cfg.plugin = "filesystem"
    source_cfg.root = source_root
    source_cfg.exclude_globs = None
    source_cfg.extraction = None

    dataset_cfg = MagicMock()
    dataset_cfg.name = "default"
    dataset_cfg.kind = "text"
    dataset_cfg.description = ""
    dataset_cfg.sources = [source_cfg]

    backend_cfg = MagicMock()
    backend_cfg.kind = "sqlite"
    backend_cfg.dsn = ":memory:"
    backend_cfg.schema = "corpus"

    config = MagicMock()
    config.backend = backend_cfg
    config.datasets = [dataset_cfg]
    config.embedders = []
    return config


def _build_stub_backend():
    backend = MagicMock()
    backend.migrate = MagicMock()
    backend.get_or_create_dataset.return_value = 1
    backend.register_source = MagicMock()
    return backend


def _patch_source_factory(raw_iterable):
    """Patch ``_instantiate_source`` to return a stub source iterator."""

    src = MagicMock()
    src.name = "filesystem"
    src.identity.return_value = "stub://"
    src.scan.return_value = list(raw_iterable)
    src.root = "/dev/null"
    return src


def test_ingest_once_emits_scan_bookends(caplog):
    """``corpus_forge.ingest.scan`` records both start and complete lines."""

    from corpus_forge import ingest as ingest_module

    config = _build_stub_config(source_root="/dev/null")
    backend = _build_stub_backend()
    source = _patch_source_factory([_stub_raw_doc("s://a.md"), _stub_raw_doc("s://b.md")])

    with (
        patch.object(ingest_module.Config, "load", return_value=config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        patch.object(ingest_module, "get_active_embedders", return_value=[]),
        patch.object(ingest_module, "_instantiate_source", return_value=source),
        patch.object(ingest_module, "get_chunker_for_source"),
        patch.object(ingest_module, "ingest_one"),
        patch.object(ingest_module, "init_logging"),
        caplog.at_level(logging.INFO, logger="corpus_forge.ingest.scan"),
    ):
        ingest_module.main(once=True)

    messages = [r.message for r in caplog.records]
    assert any("Scanning source" in m for m in messages), messages
    assert any("Scan complete" in m and "2 documents" in m for m in messages), messages
    # The progress factory also emits "Ingest (filesystem) started/complete"
    # at INFO on this logger because we pass ``scan_logger`` to ``make_progress``.
    assert any("Ingest" in m and "started" in m for m in messages), messages
    assert any("Ingest" in m and "complete" in m for m in messages), messages


def test_extractor_failure_logs_to_extract_taxonomy(caplog):
    """A raising ``ingest_one`` should log via ``corpus_forge.ingest.extract``."""

    from corpus_forge import ingest as ingest_module

    config = _build_stub_config(source_root="/dev/null")
    backend = _build_stub_backend()
    source = _patch_source_factory([_stub_raw_doc("s://broken.md")])

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated extractor failure")

    with (
        patch.object(ingest_module.Config, "load", return_value=config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        patch.object(ingest_module, "get_active_embedders", return_value=[]),
        patch.object(ingest_module, "_instantiate_source", return_value=source),
        patch.object(ingest_module, "get_chunker_for_source"),
        patch.object(ingest_module, "ingest_one", side_effect=_explode),
        patch.object(ingest_module, "init_logging"),
        caplog.at_level(logging.INFO, logger="corpus_forge.ingest.extract"),
    ):
        ingest_module.main(once=True)

    extract_records = [r for r in caplog.records if r.name == "corpus_forge.ingest.extract"]
    assert extract_records, "no extract-logger records captured"
    assert any("Extractor failed on s://broken.md" in r.message for r in extract_records)


def test_chunk_milestone_emitted_every_100_docs(caplog):
    """A 100-doc batch should emit one ``chunk_logger`` INFO record."""

    from corpus_forge import ingest as ingest_module

    config = _build_stub_config(source_root="/dev/null")
    backend = _build_stub_backend()
    source = _patch_source_factory([_stub_raw_doc(f"s://doc-{i:03d}.md") for i in range(100)])

    with (
        patch.object(ingest_module.Config, "load", return_value=config),
        patch("corpus_forge.backends.sqlite.SQLiteBackend", return_value=backend),
        patch.object(ingest_module, "get_active_embedders", return_value=[]),
        patch.object(ingest_module, "_instantiate_source", return_value=source),
        patch.object(ingest_module, "get_chunker_for_source"),
        patch.object(ingest_module, "ingest_one"),
        patch.object(ingest_module, "init_logging"),
        caplog.at_level(logging.INFO, logger="corpus_forge.ingest.chunk"),
    ):
        ingest_module.main(once=True)

    chunk_records = [r for r in caplog.records if r.name == "corpus_forge.ingest.chunk"]
    assert any("Chunked 100 documents" in r.message for r in chunk_records), [
        r.message for r in chunk_records
    ]


@pytest.fixture(autouse=True)
def _reset_loggers():
    """Ensure log propagation is on so caplog sees taxonomy records."""

    return
