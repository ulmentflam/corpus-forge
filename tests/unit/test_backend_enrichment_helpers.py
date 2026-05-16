"""Unit tests for ``iter_code_chunks_for_enrichment`` + ``update_chunk_enrichment``.

Phase H / H-05.

The helpers are exercised against the SQLite backend (the Postgres path
is exercised end-to-end in ``tests/integration/test_enrich_e2e.py``).

Covered behaviours:

- Iterator filters on ``class=code`` document label.
- Iterator falls back to ``namespace='language'`` document label when
  the chunk metadata lacks a ``language`` key.
- Iterator falls back to ``"unknown"`` when neither signal is present.
- Iterator is idempotent: chunks already enriched with the requested
  model tag are skipped.
- Iterator returns chunks again when the requested model tag differs
  from the stored one (forces re-enrichment).
- ``update_chunk_enrichment`` preserves sibling metadata keys
  (``kind``, ``name``, ``byte_range``).
- ``update_chunk_enrichment`` overwrites an existing ``enrichment`` key
  in place.
- ``update_chunk_enrichment`` handles NULL/empty metadata gracefully.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.chunkers.base import TextChunk
from corpus_forge.enrichers.base import CodeChunkEnrichment


def _backend(tmp_path: Path) -> SQLiteBackend:
    db_path = tmp_path / "corpus.db"
    backend = SQLiteBackend(path=str(db_path), schema="corpus")
    backend.migrate()
    return backend


def _create_dataset(backend: SQLiteBackend, name: str = "ds") -> int:
    backend._execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", (name, "text"))
    rows = backend._execute("SELECT id FROM datasets WHERE name = ?", (name,))
    return int(rows[0]["id"])


def _insert_doc(
    backend: SQLiteBackend,
    dataset_id: int,
    *,
    source_uri: str,
    text: str = "def foo(): pass",
    content_hash: str | None = None,
) -> int:
    ch = content_hash or source_uri
    backend._execute(
        """
        INSERT INTO documents (dataset_id, source_uri, title, text, content_hash, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (dataset_id, source_uri, None, text, ch, json.dumps({})),
    )
    rows = backend._execute(
        "SELECT id FROM documents WHERE dataset_id = ? AND source_uri = ?",
        (dataset_id, source_uri),
    )
    return int(rows[0]["id"])


def _insert_chunk(
    backend: SQLiteBackend,
    document_id: int,
    *,
    text: str = "def foo(): pass",
    metadata: dict | None = None,
    content_hash: str | None = None,
) -> int:
    md = json.dumps(metadata or {})
    backend._execute(
        """
        INSERT INTO chunks
            (document_id, chunk_index, heading, text, metadata, role, token_count,
             content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, 0, None, text, md, None, None, content_hash or text),
    )
    rows = backend._execute(
        "SELECT id FROM chunks WHERE document_id = ? ORDER BY id DESC LIMIT 1",
        (document_id,),
    )
    return int(rows[0]["id"])


class TestIterCodeChunksForEnrichment:
    def test_yields_chunks_of_class_code_documents(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        code_doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        prose_doc = _insert_doc(backend, dataset_id, source_uri="file:///b.md")
        backend.apply_label(
            "document", code_doc, "class", "code", source="classifier:rule", confidence=0.99
        )
        backend.apply_label(
            "document", prose_doc, "class", "note", source="classifier:rule", confidence=0.99
        )
        code_chunk = _insert_chunk(backend, code_doc, metadata={"language": "python"})
        prose_chunk = _insert_chunk(backend, prose_doc, metadata={})

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        ids = {cid for cid, _, _ in seen}
        assert code_chunk in ids
        assert prose_chunk not in ids

    def test_yields_language_from_chunk_metadata(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        _insert_chunk(backend, doc, metadata={"language": "rust"})

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        assert len(seen) == 1
        _, _, language = seen[0]
        assert language == "rust"

    def test_falls_back_to_document_language_label(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.go")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        backend.apply_label("document", doc, "language", "go", source="extractor")
        _insert_chunk(backend, doc, metadata={})  # no chunk-level language

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        assert len(seen) == 1
        _, _, language = seen[0]
        assert language == "go"

    def test_falls_back_to_unknown_when_no_signal(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///x")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        _insert_chunk(backend, doc, metadata={})

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        assert len(seen) == 1
        _, _, language = seen[0]
        assert language == "unknown"

    def test_yields_text_chunk_with_metadata(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        chunk_id = _insert_chunk(
            backend,
            doc,
            text="def foo(): return 1",
            metadata={"language": "python", "kind": "Function", "name": "foo"},
        )

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        assert len(seen) == 1
        cid, chunk, _ = seen[0]
        assert cid == chunk_id
        assert isinstance(chunk, TextChunk)
        assert chunk.text == "def foo(): return 1"
        assert chunk.metadata is not None
        assert chunk.metadata.get("name") == "foo"

    def test_idempotency_skips_same_model_tag(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        chunk_id = _insert_chunk(
            backend,
            doc,
            metadata={
                "language": "python",
                "enrichment": {"model": "model-A", "summary": "s", "confidence": 0.5},
            },
        )

        same = list(backend.iter_code_chunks_for_enrichment("model-A"))
        assert all(cid != chunk_id for cid, _, _ in same)

        different = list(backend.iter_code_chunks_for_enrichment("model-B"))
        assert any(cid == chunk_id for cid, _, _ in different)

    def test_dataset_filter(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        ds_a = _create_dataset(backend, "alpha")
        ds_b = _create_dataset(backend, "beta")
        doc_a = _insert_doc(backend, ds_a, source_uri="file:///a.py")
        doc_b = _insert_doc(backend, ds_b, source_uri="file:///b.py")
        backend.apply_label("document", doc_a, "class", "code", source="classifier:rule")
        backend.apply_label("document", doc_b, "class", "code", source="classifier:rule")
        chunk_a = _insert_chunk(backend, doc_a, metadata={"language": "python"})
        chunk_b = _insert_chunk(backend, doc_b, metadata={"language": "python"})

        only_a = list(backend.iter_code_chunks_for_enrichment("m", dataset_id=ds_a))
        ids = {cid for cid, _, _ in only_a}
        assert chunk_a in ids
        assert chunk_b not in ids

    def test_no_class_label_excludes_chunks(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        # No class label attached at all.
        _insert_chunk(backend, doc, metadata={"language": "python"})

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        assert seen == []

    def test_only_class_other_excluded(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a")
        backend.apply_label("document", doc, "class", "other", source="classifier:rule")
        _insert_chunk(backend, doc, metadata={"language": "python"})

        seen = list(backend.iter_code_chunks_for_enrichment("model-x"))
        assert seen == []


class TestUpdateChunkEnrichment:
    def test_writes_enrichment_metadata(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        chunk_id = _insert_chunk(backend, doc, metadata={"language": "python"})

        e = CodeChunkEnrichment(
            docstring="Synthesised.",
            summary="Returns 1.",
            symbols=["one"],
            model="model-A",
            confidence=0.9,
        )
        backend.update_chunk_enrichment(chunk_id, e)

        rows = backend._execute("SELECT metadata FROM chunks WHERE id = ?", (chunk_id,))
        md = rows[0]["metadata"]
        if isinstance(md, str):
            md = json.loads(md)
        enrich = md["enrichment"]
        assert enrich["summary"] == "Returns 1."
        assert enrich["symbols"] == ["one"]
        assert enrich["model"] == "model-A"
        assert enrich["confidence"] == pytest.approx(0.9)

    def test_preserves_sibling_metadata(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        chunk_id = _insert_chunk(
            backend,
            doc,
            metadata={
                "language": "python",
                "kind": "Function",
                "name": "foo",
                "byte_range": [0, 25],
                "cdc_fingerprint": "abc123",
            },
        )

        e = CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=0.5)
        backend.update_chunk_enrichment(chunk_id, e)

        rows = backend._execute("SELECT metadata FROM chunks WHERE id = ?", (chunk_id,))
        md = rows[0]["metadata"]
        if isinstance(md, str):
            md = json.loads(md)
        # Every original sibling key survives.
        assert md["language"] == "python"
        assert md["kind"] == "Function"
        assert md["name"] == "foo"
        assert md["byte_range"] == [0, 25]
        assert md["cdc_fingerprint"] == "abc123"
        # Plus the new enrichment block.
        assert md["enrichment"]["model"] == "m"

    def test_overwrites_existing_enrichment(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        chunk_id = _insert_chunk(
            backend,
            doc,
            metadata={
                "language": "python",
                "enrichment": {"model": "old", "summary": "old", "confidence": 0.1},
            },
        )

        new = CodeChunkEnrichment(
            docstring=None, summary="new", symbols=[], model="new-model", confidence=0.7
        )
        backend.update_chunk_enrichment(chunk_id, new)

        rows = backend._execute("SELECT metadata FROM chunks WHERE id = ?", (chunk_id,))
        md = rows[0]["metadata"]
        if isinstance(md, str):
            md = json.loads(md)
        assert md["enrichment"]["model"] == "new-model"
        assert md["enrichment"]["summary"] == "new"

    def test_missing_chunk_is_a_silent_noop(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        # Should not raise.
        backend.update_chunk_enrichment(
            999_999,
            CodeChunkEnrichment(docstring=None, summary="s", symbols=[], model="m", confidence=0.5),
        )

    def test_accepts_dict_payload(self, tmp_path: Path) -> None:
        """``update_chunk_enrichment`` accepts either an enrichment
        dataclass OR a raw dict (the storage layer doesn't impose a
        rigid type)."""
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc = _insert_doc(backend, dataset_id, source_uri="file:///a.py")
        backend.apply_label("document", doc, "class", "code", source="classifier:rule")
        chunk_id = _insert_chunk(backend, doc, metadata={"language": "python"})

        backend.update_chunk_enrichment(
            chunk_id, {"model": "m", "summary": "s", "symbols": [], "confidence": 0.5}
        )
        rows = backend._execute("SELECT metadata FROM chunks WHERE id = ?", (chunk_id,))
        md = rows[0]["metadata"]
        if isinstance(md, str):
            md = json.loads(md)
        assert md["enrichment"]["model"] == "m"
