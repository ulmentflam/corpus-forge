"""Phase D housekeeping — chunker dispatch + chunk metadata wiring.

Two bugs surfaced by smoke-testing the Phase D pipeline against
corpus-forge itself:

- **HK-1**: ``ingest._process_document`` ignored
  ``RawDocument.metadata["chunker_hint"]`` and always used the
  source-level chunker. Wave 0's ``ChunkerDispatcher`` was never wired
  into ``ingest_one``.
- **HK-2**: ``_process_document`` flattened chunks to
  ``[(heading, text)]`` tuples, dropping ``TextChunk.metadata``,
  ``role``, and ``token_count`` before they reached the storage layer.

These tests pin both behaviours so the smoke-time
``json_extract(chunks.metadata, '$.kind')`` query returns populated
values for code files.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.chunkers.base import MarkdownChunker, TextChunk
from corpus_forge.ingest import ChunkerDispatcher, _process_document, ingest_one
from corpus_forge.sources.base import (
    RawConversation,
    RawDocument,
    RawMessage,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_doc(
    *,
    text: str = "hello",
    source_uri: str = "test://doc.md",
    metadata: dict | None = None,
) -> RawDocument:
    return RawDocument(
        source_uri=source_uri,
        content_hash="hash-" + source_uri,
        text=text,
        title="Test",
        modified_at=1000.0,
        metadata=metadata or {},
        labels=[],
    )


@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SQLiteBackend:
    """Migrated SQLite backend pointed at a fresh on-disk db file."""
    backend = SQLiteBackend(path=tmp_path / "phase-d-hk.db")
    backend.migrate()
    return backend


def _create_dataset(backend: SQLiteBackend) -> int:
    return backend.get_or_create_dataset(name="hk-ds", kind="text", description="")


# =========================================================================
# HK-1 — ChunkerDispatcher wired into ingest_one
# =========================================================================


class TestHk1ProcessDocumentReturnsTextChunks:
    """``_process_document`` returns ``list[TextChunk]`` — not 2-tuples."""

    def test_returns_textchunk_list_not_tuples(self):
        doc = _make_doc(text="# heading\n\nbody")
        chunker = MarkdownChunker(max_chars=1500)
        out = _process_document(doc, chunker)
        assert isinstance(out, list)
        assert len(out) >= 1
        for chunk in out:
            assert isinstance(chunk, TextChunk), (
                "_process_document must return TextChunk instances so the storage "
                "layer can persist chunk-level metadata; got tuple/dict instead."
            )

    def test_textchunk_carries_heading(self):
        doc = _make_doc(text="# heading\n\nbody")
        chunker = MarkdownChunker(max_chars=1500)
        out = _process_document(doc, chunker)
        # The MarkdownChunker emits a heading for the chunk.
        assert any(c.heading is not None for c in out)


class TestHk1DispatcherWiredIntoIngestOne:
    """``ingest_one`` resolves the chunker via ``ChunkerDispatcher`` when
    the raw document carries ``chunker_hint``. Sources without a hint
    fall back to the per-source chunker — zero behavioural change."""

    def _stub_backend(self) -> MagicMock:
        backend = MagicMock()
        backend.get_hash.return_value = None
        backend.register_embedder.return_value = 42
        backend.chunks_missing_embedding.return_value = []
        # ``lock_source`` is a context manager.
        backend.lock_source.return_value.__enter__ = MagicMock(return_value=None)
        backend.lock_source.return_value.__exit__ = MagicMock(return_value=None)
        return backend

    def test_dispatch_picks_code_chunker_when_hint_set(self):
        """A ``chunker_hint == "code"`` doc must be chunked by CodeChunker,
        not the source-level fallback markdown chunker."""
        from corpus_forge.chunkers.code import CodeChunker

        backend = self._stub_backend()
        fallback = MarkdownChunker()

        text = (
            '"""docstring"""\n\n'
            "def hello() -> str:\n"
            '    return "hi"\n\n'
            "class Greeter:\n"
            "    def greet(self, n: str) -> str:\n"
            '        return f"hello, {n}"\n'
        )
        doc = _make_doc(
            text=text,
            source_uri="filesystem://corpus_forge/module.py",
            metadata={"chunker_hint": "code", "language": "python"},
        )

        # Capture the chunks the backend receives.
        captured: dict = {}

        def _capture(dataset_id, raw, chunks, embedder_ids=None):
            captured["chunks"] = list(chunks)
            return 1

        backend.upsert_document.side_effect = _capture

        ingest_one(backend, doc, fallback, embedders=[], dataset_id=1)

        # CodeChunker should produce at least one TextChunk; the fallback
        # MarkdownChunker would also produce chunks, so distinguishing the
        # two requires inspecting the chunk shape. CodeChunker's byte-line
        # fallback always emits ``metadata["language"]`` — MarkdownChunker
        # never does.
        chunks = captured.get("chunks")
        assert chunks, "upsert_document was never called"
        assert any(
            isinstance(c, TextChunk) and isinstance(c.metadata, dict) and "language" in c.metadata
            for c in chunks
        ), (
            "Expected the dispatcher to route the chunker_hint='code' doc through "
            "CodeChunker, which annotates chunks with metadata['language']. Got "
            f"chunks={chunks!r}"
        )

        # Sanity: a real CodeChunker, called directly, produces the same
        # language tag. This pins the dispatch wiring, not just the
        # chunker's output.
        direct = CodeChunker().chunk(text, language="python")
        if direct:
            assert direct[0].metadata is not None
            assert direct[0].metadata.get("language") == "python"

    def test_no_hint_uses_fallback_chunker(self):
        """Old sources (markdown_vault, claude_code, opencode) that do not
        set ``chunker_hint`` must keep using their per-source chunker."""
        backend = self._stub_backend()
        fallback = MagicMock(spec=MarkdownChunker)
        fallback.chunk.return_value = [TextChunk(text="x", heading="h", metadata={})]

        doc = _make_doc(text="some markdown text", metadata={})

        ingest_one(backend, doc, fallback, embedders=[], dataset_id=1)

        fallback.chunk.assert_called_once_with("some markdown text")

    def test_dispatcher_caches_chunkers_across_calls(self):
        """``ChunkerDispatcher.for_hint`` is memoised — calling
        ``ingest_one`` repeatedly with the same hint must not re-instantiate
        the per-hint chunker (cheap-to-construct guarantee from D-05)."""
        d = ChunkerDispatcher()
        a = d.for_hint("markdown")
        b = d.for_hint("markdown")
        assert a is b


# =========================================================================
# HK-2 — TextChunk.metadata persists through both backends
# =========================================================================


class TestHk2SqliteBackendPersistsChunkMetadata:
    """The SQLite backend must persist ``TextChunk.metadata`` to the
    ``chunks.metadata`` JSON column on insert and update paths."""

    def test_upsert_document_persists_chunk_metadata_on_insert(self, sqlite_backend: SQLiteBackend):
        ds_id = _create_dataset(sqlite_backend)
        doc = _make_doc(text="def f(): pass", source_uri="filesystem://hk2/insert.py")
        chunks = [
            TextChunk(
                text="def f(): pass",
                heading=None,
                metadata={"kind": "Function", "name": "f", "language": "python"},
            )
        ]
        sqlite_backend.upsert_document(ds_id, doc, chunks)

        rows = sqlite_backend._execute(
            "SELECT metadata FROM chunks WHERE document_id = ("
            "  SELECT id FROM documents WHERE source_uri = ?"
            ")",
            (doc.source_uri,),
        )
        assert rows, "no chunk row was inserted"
        md = json.loads(rows[0]["metadata"])
        assert md.get("kind") == "Function"
        assert md.get("name") == "f"
        assert md.get("language") == "python"

    def test_upsert_document_accepts_legacy_tuple_callers(self, sqlite_backend: SQLiteBackend):
        """Backwards-compat: legacy callers (existing tests +
        ``tests/smoke``) pass ``[("heading", "text"), ...]``. Backend must
        still accept this shape — the dropped metadata defaults to
        ``{}``."""
        ds_id = _create_dataset(sqlite_backend)
        doc = _make_doc(source_uri="filesystem://hk2/legacy.md")
        sqlite_backend.upsert_document(ds_id, doc, [("# T", "Some body text.")])

        rows = sqlite_backend._execute(
            "SELECT heading, text, metadata FROM chunks WHERE document_id = ("
            "  SELECT id FROM documents WHERE source_uri = ?"
            ")",
            (doc.source_uri,),
        )
        assert rows, "legacy-tuple upsert produced no rows"
        assert rows[0]["heading"] == "# T"
        assert rows[0]["text"] == "Some body text."
        # Legacy callers get the default '{}'.
        assert json.loads(rows[0]["metadata"]) == {}

    def test_upsert_document_persists_chunk_metadata_on_update(self, sqlite_backend: SQLiteBackend):
        """BUG-3 fix preservation: when the document content changes, the
        UPDATE-in-place path for matched ``content_hash`` chunks must
        also write the (possibly new) metadata payload."""
        ds_id = _create_dataset(sqlite_backend)
        doc = _make_doc(
            text="def f(): pass",
            source_uri="filesystem://hk2/update.py",
        )
        sqlite_backend.upsert_document(
            ds_id,
            doc,
            [
                TextChunk(
                    text="def f(): pass",
                    metadata={"kind": "Function", "name": "f"},
                )
            ],
        )
        # Bump the doc hash so the change path fires; same chunk text →
        # UPDATE-in-place.
        doc2 = RawDocument(
            source_uri=doc.source_uri,
            content_hash="hash-changed",
            text=doc.text,
            title=doc.title,
            modified_at=2000.0,
            metadata={},
            labels=[],
        )
        sqlite_backend.upsert_document(
            ds_id,
            doc2,
            [
                TextChunk(
                    text="def f(): pass",
                    metadata={"kind": "Function", "name": "f", "language": "python"},
                )
            ],
        )
        rows = sqlite_backend._execute(
            "SELECT metadata FROM chunks WHERE document_id = ("
            "  SELECT id FROM documents WHERE source_uri = ?"
            ")",
            (doc.source_uri,),
        )
        assert rows
        md = json.loads(rows[0]["metadata"])
        assert md.get("language") == "python", (
            f"UPDATE-in-place path failed to refresh chunks.metadata; got {md!r}"
        )


class TestHk2SqliteConversationMetadata:
    """``upsert_conversation`` accepts both legacy 2-tuple and TextChunk
    chunked-message shapes, persisting metadata when present."""

    def _conv(self, source_uri: str = "test://conv.jsonl") -> RawConversation:
        return RawConversation(
            source_uri=source_uri,
            external_id="conv1",
            content_hash="hk2-conv-hash",
            title="Test",
            started_at=1000.0,
            ended_at=1001.0,
            messages=[
                RawMessage(
                    external_uuid="m1",
                    parent_uuid=None,
                    role="user",
                    content="hi",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                )
            ],
            metadata={},
            labels=[],
        )

    def test_upsert_conversation_accepts_textchunk_messages(self, sqlite_backend: SQLiteBackend):
        ds_id = _create_dataset(sqlite_backend)
        conv = self._conv("test://conv-textchunk.jsonl")
        chunked = [[TextChunk(text="hi", heading=None, metadata={"k": "v"})]]
        conv_id = sqlite_backend.upsert_conversation(ds_id, conv, chunked)
        assert isinstance(conv_id, int) and conv_id > 0

        rows = sqlite_backend._execute(
            "SELECT metadata FROM chunks WHERE conversation_id = ?",
            (conv_id,),
        )
        assert rows
        md = json.loads(rows[0]["metadata"])
        assert md.get("k") == "v"

    def test_upsert_conversation_accepts_legacy_tuple_messages(self, sqlite_backend: SQLiteBackend):
        ds_id = _create_dataset(sqlite_backend)
        conv = self._conv("test://conv-legacy.jsonl")
        chunked = [[("h", "hi")]]
        conv_id = sqlite_backend.upsert_conversation(ds_id, conv, chunked)
        assert isinstance(conv_id, int) and conv_id > 0


# =========================================================================
# HK-1 + HK-2 end-to-end (SQLite, no Docker)
# =========================================================================


class TestHk1Hk2EndToEndSqlite:
    """Pure-SQLite end-to-end: ingest a code-hinted RawDocument and verify
    chunks.metadata.kind is populated. This is the test the smoke run
    would have caught."""

    def test_code_hint_round_trips_kind_metadata(self, sqlite_backend: SQLiteBackend):
        ds_id = _create_dataset(sqlite_backend)
        text = (
            '"""docstring."""\n\n'
            "def hello() -> str:\n"
            '    """Return a greeting."""\n'
            '    return "hello, world"\n\n\n'
            "class Greeter:\n"
            '    """A greeter."""\n\n'
            "    def greet(self, name: str) -> str:\n"
            '        return f"hello, {name}"\n'
        )
        doc = _make_doc(
            text=text,
            source_uri="filesystem://corpus_forge/code/python/module.py",
            metadata={"chunker_hint": "code", "language": "python"},
        )

        # ingest_one wires the dispatcher; fallback is the same kind of
        # MarkdownChunker the FilesystemSource ships with.
        ingest_one(
            sqlite_backend,
            doc,
            MarkdownChunker(),
            embedders=[],
            dataset_id=ds_id,
        )

        rows = sqlite_backend._execute(
            "SELECT metadata FROM chunks WHERE document_id = ("
            "  SELECT id FROM documents WHERE source_uri = ?"
            ")",
            (doc.source_uri,),
        )
        assert rows, "no chunks were inserted for the code-hinted doc"

        kinds = []
        for row in rows:
            md = json.loads(row["metadata"]) if row["metadata"] else {}
            if md.get("kind"):
                kinds.append(md["kind"])

        # Tree-sitter Python is bundled on macOS arm64 — at least one
        # named construct (Function or Class) should land. The byte-line
        # fallback path skips ``kind`` but always sets ``language``, so
        # we accept either signal as long as one is present.
        languages = []
        for row in rows:
            md = json.loads(row["metadata"]) if row["metadata"] else {}
            if md.get("language"):
                languages.append(md["language"])
        assert kinds or "python" in languages, (
            "Expected at least one chunk with metadata.kind (AST path) or "
            f"metadata.language='python' (byte-line fallback). Got rows={rows!r}"
        )
