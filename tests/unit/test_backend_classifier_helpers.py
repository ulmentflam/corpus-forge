"""Unit tests for `iter_documents_for_classification` on both backends.

Phase E / Wave 1 — C-06.

The helper is a read-only iterator that yields :class:`ClassifiableDocument`
rows from the ``documents`` table joined to ``document_labels`` /
``labels`` so the classifier sees the structural labels already attached
by extractors.

We use in-memory SQLite (and the existing :class:`SQLiteBackend.migrate`
path) so this is a pure unit test — no Docker needed. The Postgres
implementation is exercised end-to-end by C-08.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.classifiers.base import ClassifiableDocument


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
    title: str | None = None,
    text: str = "body",
    content_hash: str = "deadbeef",
    metadata: dict | None = None,
) -> int:
    import json as _json

    md = _json.dumps(metadata or {})
    backend._execute(
        """
        INSERT INTO documents (dataset_id, source_uri, title, text, content_hash, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (dataset_id, source_uri, title, text, content_hash, md),
    )
    rows = backend._execute(
        "SELECT id FROM documents WHERE dataset_id = ? AND source_uri = ?",
        (dataset_id, source_uri),
    )
    return int(rows[0]["id"])


class TestIterDocumentsForClassification:
    def test_yields_documents_in_dataset(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend, "alpha")
        doc1 = _insert_doc(backend, dataset_id, source_uri="file:///a.md", title="A")
        doc2 = _insert_doc(backend, dataset_id, source_uri="file:///b.md", title="B")

        result = list(backend.iter_documents_for_classification(dataset_id))
        ids = sorted(r.document_id for r in result)
        assert ids == sorted([doc1, doc2])
        # All yielded rows are ClassifiableDocument dataclasses.
        for r in result:
            assert isinstance(r, ClassifiableDocument)

    def test_yields_all_datasets_when_id_is_none(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        ds_a = _create_dataset(backend, "alpha")
        ds_b = _create_dataset(backend, "beta")
        _insert_doc(backend, ds_a, source_uri="file:///a.md")
        _insert_doc(backend, ds_b, source_uri="file:///b.md")

        result = list(backend.iter_documents_for_classification(None))
        uris = sorted(r.source_uri for r in result)
        assert uris == ["file:///a.md", "file:///b.md"]

    def test_format_labels_are_attached(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        doc_id = _insert_doc(
            backend,
            dataset_id,
            source_uri="file:///x.py",
            text="def f(): pass",
        )
        # Attach format + language labels via the existing apply_label path.
        backend.apply_label("document", doc_id, "format", "code", source="extractor")
        backend.apply_label("document", doc_id, "language", "python", source="extractor")

        result = list(backend.iter_documents_for_classification(dataset_id))
        assert len(result) == 1
        cd = result[0]
        labels = set(cd.format_labels)
        assert ("format", "code") in labels
        assert ("language", "python") in labels

    def test_excludes_already_classified_by_default(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        d_classified = _insert_doc(backend, dataset_id, source_uri="file:///c.md")
        d_fresh = _insert_doc(backend, dataset_id, source_uri="file:///f.md")

        # Apply a classifier-source class label to d_classified.
        backend.apply_label(
            "document",
            d_classified,
            "class",
            "note",
            source="classifier:rule",
            confidence=0.5,
        )

        # Default include_classified=False excludes d_classified.
        result = list(backend.iter_documents_for_classification(dataset_id))
        ids = {r.document_id for r in result}
        assert d_classified not in ids
        assert d_fresh in ids

    def test_user_class_label_does_not_block(self, tmp_path: Path) -> None:
        """A ``source='user'`` class label must NOT block the doc — the
        classifier won't overwrite user labels, but it should still be
        invited to attach its own row alongside."""
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        d = _insert_doc(backend, dataset_id, source_uri="file:///x.md")

        backend.apply_label("document", d, "class", "note", source="user")

        result = list(backend.iter_documents_for_classification(dataset_id))
        ids = {r.document_id for r in result}
        assert d in ids, (
            "User-attached class label should not block classifier "
            "iteration (classifier writes its own source-distinct row)"
        )

    def test_include_classified_returns_everyone(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        d_classified = _insert_doc(backend, dataset_id, source_uri="file:///c.md")
        d_fresh = _insert_doc(backend, dataset_id, source_uri="file:///f.md")
        backend.apply_label(
            "document",
            d_classified,
            "class",
            "note",
            source="classifier:rule",
            confidence=0.5,
        )

        result = list(
            backend.iter_documents_for_classification(dataset_id, include_classified=True)
        )
        ids = {r.document_id for r in result}
        assert d_classified in ids
        assert d_fresh in ids


class TestApplyLabelConfidenceOnDocument:
    """The shared ``apply_label`` path must persist ``confidence`` for
    document entities (mirrors chunk_labels behaviour)."""

    def test_document_confidence_round_trip(self, tmp_path: Path) -> None:
        backend = _backend(tmp_path)
        dataset_id = _create_dataset(backend)
        d = _insert_doc(backend, dataset_id, source_uri="file:///x.md")

        backend.apply_label(
            "document",
            d,
            "class",
            "note",
            source="classifier:rule",
            confidence=0.55,
        )

        rows = backend._execute(
            """
            SELECT dl.confidence, l.namespace, l.value, dl.source
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE dl.document_id = ? AND l.namespace = ?
            """,
            (d, "class"),
        )
        assert len(rows) == 1
        assert rows[0]["value"] == "note"
        assert rows[0]["source"] == "classifier:rule"
        assert rows[0]["confidence"] == pytest.approx(0.55)
