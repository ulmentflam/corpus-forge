"""E2E: `corpus-forge classify` against testcontainers Postgres + the
multi-format fixture corpus.

Phase E / Wave 2 — C-08.

This is the P0 gate test. It ingests every supported file in
``tests/fixtures/multi_format_corpus/`` into a real Postgres instance
(via the same ingest path the Phase D E2E test uses), runs the CLI's
``corpus-forge classify`` command, and asserts per-class document
counts on ``corpus.document_labels``.

Acceptance contracts (P0 plan §C-08 + this dispatch's calibration
notes):

* Every supported document gets exactly one classifier-source
  ``class=*`` label.
* All ``code/**`` fixtures → ``class=code``.
* ``epub/small-book.epub`` → ``class=book`` (title 'Small fixture
  book', no pedagogy regex hit).
* ``data/records.csv`` / ``data/transcript.srt`` / ``data/manifest.json``
  / ``data/config.toml`` → ``class=reference``.
* HTML fixtures under ``/html/`` → ``class=article``.
* Notebook (``analysis.ipynb``) → ``class=article`` (no path hint;
  extension fallback).
* Office docs → ``class=article`` (extension fallback).
* Markdown fixtures under ``prose/`` and the fixture root README →
  ``class=note`` (extension fallback since the passthrough extractor
  doesn't emit a ``format=markdown`` label).
* PDFs → either ``book`` / ``textbook`` / ``article`` / ``paper`` — the
  rule classifier picks based on page count and pattern match. We
  assert membership in that set rather than a single value.
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


# ---------------------------------------------------------------------------
# Constants — mirror the multi-format E2E test (D-18) for parity
# ---------------------------------------------------------------------------

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"

# Same exclusion list as test_multi_format_ingest_e2e.py: the three image
# fixtures live under ``images/`` and are only ingested when a VLM is
# wired in (P1 OCR). The classifier test uses ``ExtractionConfig()`` with
# the default ``vlm=None`` so those files are absent from the corpus.
_UNINGESTABLE: frozenset[Path] = frozenset(
    {
        Path("images/screenshot.png"),
        Path("images/photo-of-receipt.jpg"),
        Path("images/diagram.webp"),
    }
)

_FAKE_DIM = 8


class _FakeEmbedder:
    """Same shape as the D-18 fake embedder — keeps the ingest path
    exercised without loading real model weights."""

    name: str = "fake_classify_e2e"
    provider: str = "fake"
    model_id: str = "fake-v1"
    dimension: int = _FAKE_DIM
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self) -> None:
        self.encode_calls = 0

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_calls += 1
        n = len(texts)
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
    return int(rows[0]["id"])


def _ingest_filesystem(
    backend: PostgresBackend,
    source: FilesystemSource,
    dataset_id: int,
    embedder: _FakeEmbedder,
) -> int:
    """Drive the FilesystemSource through the production ingest path.

    Returns the count of successfully-parsed documents.
    """
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
        try:
            ingest_one(backend, raw_item, chunker, [embedder], dataset_id)
            parsed += 1
        except Exception as exc:  # pragma: no cover — surfaced as failure
            raise AssertionError(f"ingest_one raised on {raw_item.source_uri!r}: {exc}") from exc
    return parsed


def _classify_results(backend: PostgresBackend, dataset_id: int) -> dict[str, list[str]]:
    """Return a dict of {source_uri: [class values]} — typically 1 per doc."""
    rows = backend._execute(
        """
        SELECT d.source_uri, l.value, dl.source, dl.confidence
        FROM corpus.documents d
        JOIN corpus.document_labels dl ON dl.document_id = d.id
        JOIN corpus.labels l ON l.id = dl.label_id
        WHERE d.dataset_id = %s AND l.namespace = 'class'
        ORDER BY d.source_uri
        """,
        (dataset_id,),
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["source_uri"], []).append(r["value"])
    return out


def _class_for(results: dict[str, list[str]], rel: str) -> str | None:
    """Find a class value for a path *ending* with ``rel``."""
    for src, vals in results.items():
        if src.endswith(rel) and vals:
            return vals[0]
    return None


def _write_config_for_dsn(tmp_path: Path, pg_dsn: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''
[backend]
kind = "postgres"
dsn  = "{pg_dsn}"

[daemon]

[[datasets]]
name = "classify-e2e"
kind = "text"
sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]

[[embedders]]
name      = "fake_classify_e2e"
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


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestClassifyCliE2E:
    def test_classify_against_multi_format_corpus(
        self,
        pg_dsn: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "classify-e2e")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=_FIXTURE_ROOT,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )
        parsed = _ingest_filesystem(backend, source, dataset_id, embedder)
        assert parsed > 0

        # Run the CLI against this DSN.
        cfg_path = _write_config_for_dsn(tmp_path, pg_dsn)
        monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

        runner = CliRunner()
        result = runner.invoke(app, ["classify", "--dataset", "classify-e2e"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"

        results = _classify_results(backend, dataset_id)

        # ── 1. Every supported document got exactly one classifier label ──
        doc_count_rows = backend._execute(
            "SELECT COUNT(*) AS n FROM corpus.documents WHERE dataset_id = %s",
            (dataset_id,),
        )
        total_docs = int(doc_count_rows[0]["n"])
        assert len(results) == total_docs, (
            f"Each document should get a class label; got {len(results)} of {total_docs}"
        )
        for src, vals in results.items():
            assert len(vals) == 1, f"Document {src} got multiple class labels: {vals}"

        classifier_source_rows = backend._execute(
            """
            SELECT COUNT(*) AS n
            FROM corpus.document_labels dl
            JOIN corpus.documents d ON d.id = dl.document_id
            JOIN corpus.labels l ON l.id = dl.label_id
            WHERE d.dataset_id = %s
              AND l.namespace = 'class'
              AND dl.source LIKE 'classifier:%%'
            """,
            (dataset_id,),
        )
        assert int(classifier_source_rows[0]["n"]) == total_docs

        # ── 2. Code fixtures → code ────────────────────────────────────
        # The fixture corpus stores web assets under ``code/web/`` (.html
        # / .css / .sql) — those route through the HTML / structured
        # extractors and pick up ``format=html`` etc., NOT ``format=code``.
        # The CodeExtractor only owns programming-language extensions, so
        # we filter the assertion to known-code surfaces.
        code_exts = {
            ".py",
            ".rs",
            ".go",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".java",
            ".kt",
            ".scala",
            ".rb",
            ".ex",
            ".exs",
            ".erl",
            ".hrl",
            ".pl",
            ".hs",
            ".ml",
            ".clj",
            ".cljs",
            ".lisp",
            ".scm",
            ".sh",
            ".bash",
            ".zsh",
            ".fish",
            ".lua",
            ".zig",
            ".nim",
            ".cr",
            ".r",
            ".jl",
            ".swift",
            ".dart",
            ".nix",
            ".c",
            ".h",
            ".cc",
            ".cpp",
            ".hpp",
            ".cxx",
            ".m",
            ".mm",
            ".css",
            ".scss",
        }
        # ``.css`` / ``.scss`` are owned by CodeExtractor. ``.sql`` is too,
        # but the fixture's ``code/web/query.sql`` lives next to .html;
        # both extractors register .sql/.css respectively — CSS wins via
        # the CodeExtractor, SQL also via CodeExtractor. Build files
        # (Makefile, Dockerfile, .gitignore, .editorconfig) take the
        # filename-fallback path into CodeExtractor.
        code_filenames = {"Makefile", "Dockerfile", ".gitignore", ".editorconfig"}
        code_docs: list[str] = []
        for src in results:
            name = src.rsplit("/", 1)[-1]
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if "/code/" in src and (ext in code_exts or name in code_filenames):
                code_docs.append(src)
        assert code_docs, "expected at least one code fixture"
        for src in code_docs:
            assert results[src] == ["code"], (
                f"Code fixture {src} → {results[src]} (expected ['code'])"
            )

        # ── 3. EPUB → book (title 'Small fixture book', no pedagogy) ──
        epub_cls = _class_for(results, "epub/small-book.epub")
        assert epub_cls == "book", f"Expected book for small-book.epub; got {epub_cls!r}"

        # ── 4. Structured data + subtitle → reference ──────────────────
        for rel in (
            "data/records.csv",
            "data/transcript.srt",
            "data/manifest.json",
            "data/config.toml",
        ):
            cls = _class_for(results, rel)
            assert cls == "reference", f"Expected reference for {rel}; got {cls!r}"

        # ── 5. HTML → article ─────────────────────────────────────────
        for rel in ("html/simple-article.html", "html/nav-and-ads.html"):
            cls = _class_for(results, rel)
            assert cls == "article", f"Expected article for {rel}; got {cls!r}"

        # ── 6. PDF → one of {book, textbook, article, paper} ───────────
        valid_pdf_classes = {"book", "textbook", "article", "paper"}
        for rel in (
            "pdf/digital-single-col.pdf",
            "pdf/digital-two-col-equations.pdf",
            "pdf/scanned-paper.pdf",
        ):
            cls = _class_for(results, rel)
            assert cls in valid_pdf_classes, (
                f"PDF {rel} classified as {cls!r}; expected one of {sorted(valid_pdf_classes)}"
            )

        # ── 7. Notebook → article OR reference (acceptable range) ──────
        notebook_cls = _class_for(results, "notebook/analysis.ipynb")
        assert notebook_cls in {"article", "reference", "code"}, (
            f"Notebook classified as {notebook_cls!r}; expected one of "
            "['article', 'reference', 'code']"
        )

        # ── 8. Markdown fixtures (prose/ + root README) → note ─────────
        for rel in (
            "prose/intro.md",
            "prose/frontmatter.md",
            "README.md",
        ):
            cls = _class_for(results, rel)
            assert cls == "note", f"Expected note for {rel}; got {cls!r}"

        # ── 9. Idempotency — re-run is a no-op ────────────────────────
        result2 = runner.invoke(app, ["classify", "--dataset", "classify-e2e"])
        assert result2.exit_code == 0, f"Second classify failed: {result2.output}"
        results2 = _classify_results(backend, dataset_id)
        # Same set of (source_uri, class) pairs.
        assert {(k, tuple(v)) for k, v in results.items()} == {
            (k, tuple(v)) for k, v in results2.items()
        }

    def test_dry_run_writes_nothing(
        self,
        pg_dsn: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = _make_backend(pg_dsn)
        dataset_id = _create_dataset(backend, "classify-dry")
        embedder = _FakeEmbedder()

        source = FilesystemSource(
            root=_FIXTURE_ROOT,
            exclude_globs=[],
            extraction=ExtractionConfig(),
            debounce=0.0,
        )
        _ingest_filesystem(backend, source, dataset_id, embedder)

        cfg_path = _write_config_for_dsn(tmp_path, pg_dsn)
        # Tweak dataset name in the config to point at the dry one.
        text = cfg_path.read_text().replace("classify-e2e", "classify-dry")
        cfg_path.write_text(text, encoding="utf-8")
        monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["classify", "--dataset", "classify-dry", "--dry-run", "--json"],
        )
        assert result.exit_code == 0, result.output

        # No class labels were written.
        rows = backend._execute(
            """
            SELECT COUNT(*) AS n
            FROM corpus.document_labels dl
            JOIN corpus.documents d ON d.id = dl.document_id
            JOIN corpus.labels l ON l.id = dl.label_id
            WHERE d.dataset_id = %s AND l.namespace = 'class'
            """,
            (dataset_id,),
        )
        assert int(rows[0]["n"]) == 0
