"""Multi-format end-to-end integration test — Phase D / Wave 3 / D-18.

Drives the entire P0 multi-format pipeline against a testcontainers
Postgres + pgvector database using the synthetic fixture corpus at
``tests/fixtures/multi_format_corpus/`` (built by D-17).

Contracts asserted (see ``.planning/tdd/multi_format.md`` Wave 3):

1. ``corpus.documents`` count matches the file count of the fixture tree,
   minus the small fixed set of "non-ingestable" supplementary files
   (e.g. the fixture's own README, which the source includes).
2. Every supported document has at least one ``corpus.chunks`` row.
3. Each P0 chunker hint ("markdown", "code", "passthrough") shows up in
   the metadata of at least one chunk's parent document.
4. Code chunks carry ``language`` metadata; multiple distinct languages
   appear (python, rust, ts, go, ...).
5. Per-format labels (``("format", "pdf")``, ``("format", "code")``, ...)
   are persisted on the document rows by the upsert path.
6. Idempotency — a second run leaves document and chunk counts unchanged.
7. Graceful degradation — a corrupt 1-byte PDF and a 100-MB-of-zeros file
   do not abort the run, are absent from ``documents``, and the oversize
   file triggers the ``ExtractionConfig.max_bytes`` WARNING.
8. Unknown extensions are silently skipped with no DB rows.

The test deliberately uses a fake, no-op embedder so end-to-end wiring
is exercised without the cost of real embedding model loads — the unit
suite covers real embedders. Wall-clock budget: under 60 seconds on a
warm Docker daemon.
"""

from __future__ import annotations

import logging
import shutil
import socket
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.config import ExtractionConfig
from corpus_forge.ingest import ChunkerDispatcher, ingest_one
from corpus_forge.sources.filesystem import FilesystemSource

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"

# Files in the fixture tree that the FilesystemSource will SKIP because
# they have no extension match and no filename fallback. Used to compute
# the expected ``corpus.documents`` row count off the on-disk shape.
#
# Wave 6 / P1 added an ``images/`` subdirectory (screenshot.png,
# photo-of-receipt.jpg, diagram.webp) that's intentionally only ingested
# when a VLM is configured (``ocr_enabled=True`` + non-Noop backend).
# This P0 e2e test uses the default ``ExtractionConfig()`` which has no
# VLM, so ``ImageExtractor`` is never registered and those three files
# are silently skipped — they must therefore be excluded from the
# expected document count.
_UNINGESTABLE: frozenset[Path] = frozenset(
    {
        Path("images/screenshot.png"),
        Path("images/photo-of-receipt.jpg"),
        Path("images/diagram.webp"),
    }
)

# Fake embedder constants
_FAKE_NAME = "fake_e2e_embedder"
_FAKE_DIM = 8


class _FakeEmbedder:
    """Deterministic, zero-op embedder for the e2e test.

    Returns a stable unit-length vector per input text. The unit-test
    suite covers real embedder behaviour; here we only need an Embedder
    surface so ``ingest_one`` can register a row in ``corpus.embedders``
    and call ``encode``.
    """

    name: str = _FAKE_NAME
    provider: str = "fake"
    model_id: str = "fake-v1"
    dimension: int = _FAKE_DIM
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self) -> None:
        self.encode_calls: int = 0

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_calls += 1
        n = len(texts)
        # Stable unit-length vector seeded by index — never zero.
        vecs = np.zeros((n, _FAKE_DIM), dtype=np.float32)
        for i in range(n):
            vecs[i, i % _FAKE_DIM] = 1.0
        return vecs

    def warmup(self) -> None:  # pragma: no cover — trivial
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(pg_dsn: str) -> PostgresBackend:
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()
    return backend


def _create_dataset(backend: PostgresBackend, name: str) -> int:
    rows = backend._execute(
        "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
        (name, "text"),
    )
    return rows[0]["id"]


def _ingest_filesystem_source(
    backend: PostgresBackend,
    source: FilesystemSource,
    dataset_id: int,
    embedder: _FakeEmbedder,
) -> tuple[int, int]:
    """Drive a ``FilesystemSource`` through the production wiring.

    Mirrors ``ingest_once`` but takes the already-constructed backend +
    source + embedder so the test stays focused on the multi-format
    surface rather than re-running the full Config bootstrap.

    Returns ``(parsed_count, skipped_count)`` for assertion clarity.
    """
    # Register the source row (mirrors ingest_once's setup).
    backend.register_source(
        dataset_id,
        source.name,
        source.identity(),
        socket.gethostname(),
    )

    # Dispatcher mirrors production: chunker_hint per-document.
    dispatcher = ChunkerDispatcher()
    # Fallback chunker — used only if a parse() result lacks a
    # chunker_hint. Won't normally fire for FilesystemSource (every
    # extractor sets one), but ChunkerDispatcher's API requires a
    # fallback so we provide a real Markdown chunker just in case.
    from corpus_forge.chunkers.markdown import MarkdownChunker

    fallback = MarkdownChunker()

    parsed = 0
    skipped = 0
    for raw_item in source.scan():
        if raw_item is None:
            skipped += 1
            continue
        chunker = dispatcher.dispatch_for(raw_item, fallback)
        try:
            ingest_one(backend, raw_item, chunker, [embedder], dataset_id)
            parsed += 1
        except Exception as exc:
            # The test asserts that real corruption / oversize / unknown
            # files are skipped *before* ingest_one. Anything that gets
            # here and raises is a regression — surface the path so the
            # failure message is actionable.
            raise AssertionError(f"ingest_one raised on {raw_item.source_uri!r}: {exc}") from exc

    return parsed, skipped


def _count_documents(backend: PostgresBackend, dataset_id: int) -> int:
    rows = backend._execute(
        "SELECT COUNT(*) AS n FROM corpus.documents WHERE dataset_id = %s",
        (dataset_id,),
    )
    return int(rows[0]["n"])


def _count_chunks(backend: PostgresBackend, dataset_id: int) -> int:
    rows = backend._execute(
        """
        SELECT COUNT(*) AS n
        FROM corpus.chunks c
        JOIN corpus.documents d ON d.id = c.document_id
        WHERE d.dataset_id = %s
        """,
        (dataset_id,),
    )
    return int(rows[0]["n"])


def _document_labels(backend: PostgresBackend, dataset_id: int) -> set[tuple[str, str]]:
    rows = backend._execute(
        """
        SELECT l.namespace AS ns, l.value AS val
        FROM corpus.document_labels dl
        JOIN corpus.labels l ON l.id = dl.label_id
        JOIN corpus.documents d ON d.id = dl.document_id
        WHERE d.dataset_id = %s
        """,
        (dataset_id,),
    )
    return {(r["ns"], r["val"]) for r in rows}


def _document_metadata_rows(backend: PostgresBackend, dataset_id: int) -> list[dict]:
    return backend._execute(
        "SELECT id, source_uri, metadata FROM corpus.documents WHERE dataset_id = %s",
        (dataset_id,),
    )


def _expected_file_count(root: Path) -> int:
    """Count every file under ``root`` minus the no-VLM-uningestable set.

    The FilesystemSource has no extractor for unknown extensions, so the
    counted set must mirror the extractor-registered surface. The
    fixture's own README is markdown (``.md`` → ``PassthroughMarkdownExtractor``),
    so it counts. The Wave 6 image fixtures under ``images/`` are
    excluded via ``_UNINGESTABLE`` because the P0 default
    ``ExtractionConfig`` doesn't configure a VLM and therefore doesn't
    register the ``ImageExtractor``.
    """
    total = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel in _UNINGESTABLE:
            continue
        total += 1
    return total


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiFormatIngestE2E:
    """End-to-end multi-format ingestion pinned against the fixture tree."""

    def test_fixture_tree_is_present(self) -> None:
        """Pre-flight: the fixture tree exists and has the expected shape.

        Catches a stale checkout / forgotten ``build_fixture_corpus.py``
        run before the rest of the suite fails with a confusing error.
        """
        assert _FIXTURE_ROOT.is_dir(), (
            f"Fixture tree missing: {_FIXTURE_ROOT}. Re-run "
            "`uv run python scripts/build_fixture_corpus.py`."
        )
        # A handful of cardinal files we always expect.
        for rel in (
            "README.md",
            "prose/intro.md",
            "pdf/digital-single-col.pdf",
            "html/simple-article.html",
            "epub/small-book.epub",
            "office/report.docx",
            "office/slides.pptx",
            "office/tiny-sheet.xlsx",
            "notebook/analysis.ipynb",
            "data/records.csv",
            "data/transcript.srt",
            "code/python/module.py",
            "code/rust/main.rs",
            "code/build/Makefile",
            "code/build/Dockerfile",
        ):
            assert (_FIXTURE_ROOT / rel).is_file(), f"Expected fixture file missing: {rel}"

    def test_full_ingest_against_postgres(self, pg_dsn: str, tmp_path: Path) -> None:
        """Wave 3 P0 gate — full multi-format ingest happy path."""
        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "multi-format-e2e")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=_FIXTURE_ROOT,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )

        parsed, _ = _ingest_filesystem_source(backend, source, dataset_id, embedder)

        # ── 1. Document count = fixture file count ─────────────────────
        expected = _expected_file_count(_FIXTURE_ROOT)
        doc_count = _count_documents(backend, dataset_id)
        assert doc_count == expected, (
            f"corpus.documents has {doc_count} rows; expected {expected} "
            "(one per file in the fixture tree). Mismatch suggests an "
            "extractor silently dropped a file family — inspect "
            "`backend._execute('SELECT source_uri FROM corpus.documents ...')` "
            "and the FilesystemSource discover/parse logs."
        )
        assert parsed == expected, (
            f"FilesystemSource.scan yielded {parsed} parsed docs; expected {expected}."
        )

        # ── 2. Every supported document has at least one chunk ─────────
        chunk_count = _count_chunks(backend, dataset_id)
        assert chunk_count >= doc_count, (
            f"Expected at least one chunk per document; got "
            f"chunks={chunk_count} documents={doc_count}."
        )

        # ── 3. All three P0 chunker hints appear ───────────────────────
        rows = _document_metadata_rows(backend, dataset_id)
        hints_seen: set[str] = set()
        languages_seen: set[str] = set()
        for r in rows:
            md = r["metadata"] or {}
            hint = md.get("chunker_hint")
            if hint:
                hints_seen.add(hint)
            lang = md.get("language")
            if lang:
                languages_seen.add(lang)

        for required in ("markdown", "code", "passthrough"):
            assert required in hints_seen, (
                f"chunker_hint {required!r} missing from corpus.documents "
                f"metadata. Saw: {sorted(hints_seen)}. The dispatcher is "
                "silently dropping documents — inspect "
                "FilesystemSource.parse and the per-extractor "
                "chunker_hint declarations."
            )

        # ── 4. Code documents carry language metadata; multiple langs ──
        for required_lang in ("python", "rust", "go", "typescript"):
            assert required_lang in languages_seen, (
                f"Expected language={required_lang!r} in document metadata. "
                f"Saw: {sorted(languages_seen)}."
            )

        # ── 5. Per-format labels were persisted on documents ──────────
        labels = _document_labels(backend, dataset_id)
        for expected_label in (
            ("format", "pdf"),
            ("format", "html"),
            ("format", "epub"),
            ("format", "code"),
            ("format", "ipynb"),
        ):
            assert expected_label in labels, (
                f"Expected document label {expected_label!r} not found. "
                f"Saw: {sorted(labels)}. The extractor emits this label but "
                "PostgresBackend.upsert_document never persisted it — "
                "check _apply_document_labels wiring."
            )

        # Code-extractor also emits a per-language label.
        language_labels = {val for ns, val in labels if ns == "language"}
        assert "python" in language_labels, (
            f"Expected language=python in document labels. Got language "
            f"labels: {sorted(language_labels)}."
        )
        assert "rust" in language_labels

    def test_idempotency_second_run_is_a_noop(self, pg_dsn: str) -> None:
        """Re-running the same ingest leaves document + chunk counts unchanged."""
        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "multi-format-e2e-idem")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=_FIXTURE_ROOT,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )

        # Pass 1
        _ingest_filesystem_source(backend, source, dataset_id, embedder)
        docs_a = _count_documents(backend, dataset_id)
        chunks_a = _count_chunks(backend, dataset_id)

        # Pass 2 — same source, same dataset.
        _ingest_filesystem_source(backend, source, dataset_id, embedder)
        docs_b = _count_documents(backend, dataset_id)
        chunks_b = _count_chunks(backend, dataset_id)

        assert docs_b == docs_a, (
            f"Second ingest changed document count: {docs_a} -> {docs_b}. "
            "Content-hash short-circuit in upsert_document is broken or "
            "the source emitted different source_uri / content_hash for "
            "unchanged files."
        )
        assert chunks_b == chunks_a, f"Second ingest changed chunk count: {chunks_a} -> {chunks_b}."

    def test_graceful_degradation_on_corrupt_and_oversize_files(
        self, pg_dsn: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Corrupt PDF + 100MB-of-zeros file do not abort ingest.

        Both must be absent from ``corpus.documents``; the oversize file
        triggers a WARNING via the ``max_bytes`` skip path; the corrupt
        PDF triggers the extractor-exception WARNING in
        ``FilesystemSource.parse``.
        """
        # Copy the fixture tree into a writable scratch dir.
        scratch = tmp_path / "fixture_with_corruption"
        shutil.copytree(_FIXTURE_ROOT, scratch)

        corrupt_pdf = scratch / "pdf" / "corrupt.pdf"
        corrupt_pdf.write_bytes(b"\x00")  # 1 byte, not a valid PDF

        oversize_pdf = scratch / "pdf" / "oversize.pdf"
        # 100 MB of zeros — exceeds the default 50 MB max_bytes.
        with oversize_pdf.open("wb") as fh:
            fh.write(b"\x00" * (100 * 1024 * 1024))

        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "multi-format-e2e-degrade")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=scratch,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )

        with caplog.at_level(logging.WARNING, logger="corpus_forge.sources.filesystem"):
            _ingest_filesystem_source(backend, source, dataset_id, embedder)

        # Neither bad file shows up.
        rows = backend._execute(
            "SELECT source_uri FROM corpus.documents WHERE dataset_id = %s",
            (dataset_id,),
        )
        uris = {r["source_uri"] for r in rows}
        assert not any("corrupt.pdf" in u for u in uris), (
            "Corrupt 1-byte PDF reached corpus.documents — "
            "FilesystemSource.parse should swallow extractor exceptions."
        )
        assert not any("oversize.pdf" in u for u in uris), (
            "100 MB oversize PDF reached corpus.documents — the "
            "max_bytes guard in FilesystemSource.parse is not firing."
        )

        # WARNING was logged for the oversize file.
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        oversize_warned = any(
            "oversize" in msg.lower() or "max_bytes" in msg.lower() for msg in warning_msgs
        )
        assert oversize_warned, (
            "Expected a WARNING log about the oversize PDF; saw none. "
            f"Captured warnings: {warning_msgs}"
        )

        # The healthy documents still ingested.
        assert _count_documents(backend, dataset_id) >= 10, (
            "Healthy fixture documents missing after a degraded run — "
            "a corrupt file should not poison the rest of the ingest."
        )

    def test_unknown_extension_is_silently_skipped(
        self, pg_dsn: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A `.xyz` file is ignored: no DB row, no WARNING, only DEBUG."""
        scratch = tmp_path / "fixture_with_unknown_ext"
        shutil.copytree(_FIXTURE_ROOT, scratch)

        unknown = scratch / "data" / "weird.xyz"
        unknown.write_bytes(b"\x01\x02\x03\x04\x05random-bytes")

        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "multi-format-e2e-unknown")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=scratch,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )

        with caplog.at_level(logging.DEBUG, logger="corpus_forge.sources.filesystem"):
            _ingest_filesystem_source(backend, source, dataset_id, embedder)

        rows = backend._execute(
            "SELECT source_uri FROM corpus.documents WHERE dataset_id = %s",
            (dataset_id,),
        )
        uris = {r["source_uri"] for r in rows}
        assert not any("weird.xyz" in u for u in uris), (
            "An unknown-extension file (.xyz) reached corpus.documents — "
            "the registry should have returned None and the source skipped it."
        )

        # No WARNING for the unknown file — only DEBUG.
        warnings_about_xyz = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "weird.xyz" in r.getMessage()
        ]
        assert not warnings_about_xyz, (
            "Unknown-extension files should be DEBUG, not WARNING. "
            f"Saw: {[r.getMessage() for r in warnings_about_xyz]}"
        )
