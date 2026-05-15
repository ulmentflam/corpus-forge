"""E2E: ``corpus-forge rechunk`` against testcontainers Postgres +
fixture corpus.

Phase F / F-04 — the rechunk pipeline.

The flow:

1. Ingest the multi-format fixture corpus (same path as the Phase D /
   Phase E E2E tests).
2. Run ``corpus-forge classify --dataset rechunk-e2e`` so each document
   carries a ``class=*`` label.
3. Run ``corpus-forge rechunk --dataset rechunk-e2e`` and assert:
   - Prose-class documents (book/textbook/paper/article/note/other)
     emerge with ``cdc_fingerprint`` metadata on every chunk.
   - Code-class documents keep their ``kind`` / ``name`` metadata
     (CodeChunker output).
   - Reference-class documents have neither.
4. Re-run rechunk: every document is a no-op (idempotent).
"""

from __future__ import annotations

import socket
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.cli import app
from corpus_forge.config import ExtractionConfig
from corpus_forge.ingest import ChunkerDispatcher, ingest_one
from corpus_forge.sources.filesystem import FilesystemSource

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]


_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"
_FAKE_DIM = 8


class _FakeEmbedder:
    """Same fake embedder shape used elsewhere — keeps ingest happy."""

    name: str = "fake_rechunk_e2e"
    provider: str = "fake"
    model_id: str = "fake-v1"
    dimension: int = _FAKE_DIM
    normalized: bool = True
    distance: str = "cosine"

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        n = len(texts)
        vecs = np.zeros((n, _FAKE_DIM), dtype=np.float32)
        for i in range(n):
            vecs[i, i % _FAKE_DIM] = 1.0
        return vecs

    def warmup(self) -> None:  # pragma: no cover
        pass


def _make_backend(pg_dsn: str) -> PostgresBackend:
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()
    return backend


def _create_dataset(backend: PostgresBackend, name: str) -> int:
    rows = backend._execute(
        "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
        (name, "text"),
    )
    return int(rows[0]["id"])


def _ingest_corpus(
    backend: PostgresBackend,
    source: FilesystemSource,
    dataset_id: int,
    embedder: _FakeEmbedder,
) -> int:
    from corpus_forge.chunkers.markdown import MarkdownChunker

    backend.register_source(
        dataset_id,
        source.name,
        source.identity(),
        socket.gethostname(),
    )
    dispatcher = ChunkerDispatcher()
    fallback = MarkdownChunker()
    parsed = 0
    for raw_item in source.scan():
        if raw_item is None:
            continue
        chunker = dispatcher.dispatch_for(raw_item, fallback)
        ingest_one(backend, raw_item, chunker, [embedder], dataset_id)
        parsed += 1
    return parsed


def _write_config_for_dsn(tmp_path: Path, pg_dsn: str, dataset: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''
[backend]
kind = "postgres"
dsn  = "{pg_dsn}"

[daemon]

[[datasets]]
name = "{dataset}"
kind = "text"
sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]

[[embedders]]
name      = "fake_rechunk_e2e"
provider  = "sentence_transformers"
model_id  = "fake-1"
dimension = 8

[classifier]
chain = ["rule"]
escalation_threshold = 0.4
''',
        encoding="utf-8",
    )
    return cfg


def _chunks_by_doc(
    backend: PostgresBackend,
    dataset_id: int,
) -> dict[str, list[dict]]:
    """Return {source_uri: [chunk_metadata_dict, ...]} for a dataset."""
    rows = backend._execute(
        """
        SELECT d.source_uri, c.chunk_index, c.metadata
        FROM corpus.chunks c
        JOIN corpus.documents d ON d.id = c.document_id
        WHERE d.dataset_id = %s
        ORDER BY d.source_uri, c.chunk_index
        """,
        (dataset_id,),
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["source_uri"], []).append(r["metadata"] or {})
    return out


def _classes_by_doc(backend: PostgresBackend, dataset_id: int) -> dict[str, str]:
    rows = backend._execute(
        """
        SELECT d.source_uri, l.value
        FROM corpus.documents d
        JOIN corpus.document_labels dl ON dl.document_id = d.id
        JOIN corpus.labels l ON l.id = dl.label_id
        WHERE d.dataset_id = %s AND l.namespace = 'class'
        """,
        (dataset_id,),
    )
    return {r["source_uri"]: r["value"] for r in rows}


class TestRechunkCliE2E:
    def test_rechunk_against_multi_format_corpus(
        self,
        pg_dsn: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "rechunk-e2e")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=_FIXTURE_ROOT,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )
        parsed = _ingest_corpus(backend, source, dataset_id, embedder)
        assert parsed > 0

        cfg_path = _write_config_for_dsn(tmp_path, pg_dsn, "rechunk-e2e")
        monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

        # ── Step 1: classify ──
        runner = CliRunner()
        result = runner.invoke(app, ["classify", "--dataset", "rechunk-e2e"])
        assert result.exit_code == 0, f"classify failed: {result.output}"

        # Map of (source_uri -> class)
        classes = _classes_by_doc(backend, dataset_id)
        assert classes, "expected at least one class label after classify"

        # ── Step 2: rechunk ──
        result = runner.invoke(app, ["rechunk", "--dataset", "rechunk-e2e"])
        assert result.exit_code == 0, f"rechunk failed: {result.output}"

        chunks_after = _chunks_by_doc(backend, dataset_id)

        # (a) Prose-class documents have cdc_fingerprint metadata on every chunk
        prose_classes = {"book", "textbook", "paper", "article", "note", "other"}
        prose_doc_count = 0
        for src, cls in classes.items():
            if cls in prose_classes and src in chunks_after:
                doc_chunks = chunks_after[src]
                assert doc_chunks, f"no chunks for {src}"
                for chunk_md in doc_chunks:
                    assert "cdc_fingerprint" in chunk_md, (
                        f"prose doc {src} (class={cls}) missing cdc_fingerprint "
                        f"in chunk metadata: {chunk_md}"
                    )
                    assert "byte_range" in chunk_md
                prose_doc_count += 1
        assert prose_doc_count > 0, "expected at least one prose-class document"

        # (b) Code-class documents still have CodeChunker-style metadata
        # ('kind' / 'name' or 'byte_range' from byte-line fallback) but
        # NOT cdc_fingerprint.
        code_doc_count = 0
        for src, cls in classes.items():
            if cls == "code" and src in chunks_after:
                doc_chunks = chunks_after[src]
                for chunk_md in doc_chunks:
                    assert "cdc_fingerprint" not in chunk_md, (
                        f"code doc {src} unexpectedly has cdc_fingerprint"
                    )
                code_doc_count += 1
        assert code_doc_count > 0, "expected at least one code-class document"

        # (c) Reference-class documents have no cdc_fingerprint
        for src, cls in classes.items():
            if cls == "reference" and src in chunks_after:
                for chunk_md in chunks_after[src]:
                    assert "cdc_fingerprint" not in chunk_md

        # ── Step 3: re-running rechunk is a no-op ──
        result2 = runner.invoke(app, ["rechunk", "--dataset", "rechunk-e2e"])
        assert result2.exit_code == 0, f"second rechunk failed: {result2.output}"

        chunks_after2 = _chunks_by_doc(backend, dataset_id)
        # Same set of source_uri / metadata shape.
        assert set(chunks_after.keys()) == set(chunks_after2.keys())
        for src in chunks_after:
            assert len(chunks_after[src]) == len(chunks_after2[src]), (
                f"chunk count changed on second rechunk for {src}"
            )

        # The output of the second rechunk must mention every prose doc
        # as noop (chunk lists unchanged).
        out = result2.output
        # Spot-check: at least one noop annotation present
        assert "noop" in out or "0 applied" in out or "applied 0" in out, (
            f"expected idempotent run to log noops; got:\n{out}"
        )

    def test_rechunk_dry_run_writes_nothing(
        self,
        pg_dsn: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "rechunk-dry")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=_FIXTURE_ROOT,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )
        _ingest_corpus(backend, source, dataset_id, embedder)

        cfg_path = _write_config_for_dsn(tmp_path, pg_dsn, "rechunk-dry")
        monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

        runner = CliRunner()
        runner.invoke(app, ["classify", "--dataset", "rechunk-dry"])

        chunks_before = _chunks_by_doc(backend, dataset_id)

        result = runner.invoke(app, ["rechunk", "--dataset", "rechunk-dry", "--dry-run"])
        assert result.exit_code == 0, result.output

        chunks_after = _chunks_by_doc(backend, dataset_id)
        # Counts identical (and metadata too — we only check counts here
        # because the pre-rechunk chunks come from MarkdownChunker /
        # PassthroughChunker etc., not CDC, so the metadata SHAPE may
        # differ between runs in the real path — but on dry-run nothing
        # changes).
        assert {k: len(v) for k, v in chunks_before.items()} == {
            k: len(v) for k, v in chunks_after.items()
        }
