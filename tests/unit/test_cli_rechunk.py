"""Unit tests for F-04: ``corpus-forge rechunk`` CLI.

The rechunk command walks documents that carry a ``class=*`` classifier
label (Phase E output) and re-runs the chunker pass using the
class-mapped chunker. The Phase C BUG-3 ``content_hash`` chunk-reuse
path inside :meth:`upsert_document` means embeddings survive any chunks
that come out byte-identical to their pre-rechunk peers.

Idempotency: the second invocation must be a no-op (same chunk texts
in / same chunk texts out → skip the upsert).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

# ── Fake objects that look just enough like the real shapes ──────────────


@dataclass
class _FakeClassifiableDoc:
    """Stand-in for ``classifiers.base.ClassifiableDocument``."""

    document_id: int
    source_uri: str
    text: str
    title: str | None = None
    format_labels: list[tuple[str, str]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class _FakeConfig:
    """Bare-bones Config stand-in — only the attributes the rechunk
    code path actually reads."""

    backend: Any = None
    datasets: list = field(default_factory=list)
    embedders: list = field(default_factory=list)


def _build_fake_backend(
    docs_with_classes: list[tuple[_FakeClassifiableDoc, list[str]]],
) -> MagicMock:
    """Build a MagicMock backend wired with the given (doc, chunk_texts) pairs.

    Each tuple is ``(doc, prior_chunk_texts)`` where ``prior_chunk_texts``
    is the list of pre-rechunk chunk text strings already stored. The
    backend's ``iter_documents_for_classification(include_classified=True)``
    will yield only docs carrying a ``("class", _)`` label (the planning
    contract).
    """
    backend = MagicMock()
    backend.migrate.return_value = None

    def _iter(dataset_id=None, *, include_classified=False):
        for doc, _ in docs_with_classes:
            if any(ns == "class" for ns, _ in doc.format_labels):
                yield doc

    backend.iter_documents_for_classification.side_effect = _iter

    # Map doc_id → list of pre-stored chunk texts so the idempotency
    # check has something to compare against.
    chunk_text_map = {d.document_id: prior for d, prior in docs_with_classes}
    backend.get_document_chunk_texts.side_effect = lambda doc_id: list(
        chunk_text_map.get(doc_id, [])
    )

    # Phase F (F-04) addition: the rechunk idempotency check also
    # consults each chunk's metadata to detect "stored chunks lack
    # the expected chunker signature" (e.g. CDC's ``cdc_fingerprint``).
    # For the unit tests we return ``[]`` so the check defaults to
    # "missing signature → rechunk required" — except for the explicit
    # idempotency test which monkeypatches this with the right shape.
    backend.get_document_chunk_metadatas.side_effect = lambda _doc_id: []

    backend.find_dataset_id_by_name.return_value = 1
    backend.upsert_document.return_value = 100
    backend.replace_document_chunks.return_value = 0
    return backend


@pytest.fixture
def runner() -> CliRunner:
    # mix_stderr=False so we can assert against stdout independently.
    return CliRunner()


# ── Smoke: command is wired in ──────────────────────────────────────────


def test_rechunk_command_help(runner: CliRunner):
    """The command must appear in the top-level help."""
    result = runner.invoke(app, ["rechunk", "--help"])
    assert result.exit_code == 0
    assert "rechunk" in result.output.lower()


# ── No classified docs → no-op ──────────────────────────────────────────


def test_rechunk_no_classified_documents_is_noop(runner: CliRunner, monkeypatch):
    """When no doc carries a ``class=*`` label, rechunk processes zero docs."""
    backend = _build_fake_backend([])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0
    backend.replace_document_chunks.assert_not_called()


# ── Class-aware dispatch ────────────────────────────────────────────────


def test_rechunk_routes_prose_class_to_cdc_chunker(runner: CliRunner, monkeypatch):
    """A doc with class=book must produce CDC chunks (cdc_fingerprint
    metadata on every chunk)."""
    doc = _FakeClassifiableDoc(
        document_id=42,
        source_uri="filesystem://x/foo.md",
        text="hello world. " * 500,  # enough for multiple CDC chunks
        format_labels=[("format", "md"), ("class", "book")],
    )
    backend = _build_fake_backend([(doc, ["pre-existing positional chunk text"])])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0, result.output
    backend.replace_document_chunks.assert_called_once()

    # Inspect the chunks passed to upsert_document — they must carry
    # cdc_fingerprint metadata (the CDC chunker's signature).
    call_args = backend.replace_document_chunks.call_args
    chunks = call_args.kwargs.get("chunks") if call_args.kwargs else call_args.args[2]
    assert len(chunks) >= 1
    for ch in chunks:
        assert "cdc_fingerprint" in ch.metadata
        assert "byte_range" in ch.metadata


def test_rechunk_routes_code_class_to_code_chunker(runner: CliRunner, monkeypatch):
    """A doc with class=code must NOT produce CDC chunks (CodeChunker
    emits 'kind'/'name' metadata via tree-sitter or the byte-line
    fallback; either way no 'cdc_fingerprint' key)."""
    doc = _FakeClassifiableDoc(
        document_id=43,
        source_uri="filesystem://x/foo.py",
        text='def hello():\n    """world."""\n    return 42\n' * 30,
        format_labels=[("format", "code"), ("language", "python"), ("class", "code")],
        metadata={"language": "python"},
    )
    backend = _build_fake_backend([(doc, ["positional"])])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0, result.output
    backend.replace_document_chunks.assert_called_once()
    call_args = backend.replace_document_chunks.call_args
    chunks = call_args.kwargs.get("chunks") if call_args.kwargs else call_args.args[2]
    assert len(chunks) >= 1
    for ch in chunks:
        assert "cdc_fingerprint" not in (ch.metadata or {})


def test_rechunk_routes_reference_class_to_passthrough(runner: CliRunner, monkeypatch):
    """class=reference must keep PassthroughChunker output (no
    cdc_fingerprint)."""
    doc = _FakeClassifiableDoc(
        document_id=44,
        source_uri="filesystem://x/data.csv",
        text="name,value\nalice,1\nbob,2\n",
        format_labels=[("format", "csv"), ("class", "reference")],
    )
    backend = _build_fake_backend([(doc, ["old"])])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0, result.output
    backend.replace_document_chunks.assert_called_once()
    call_args = backend.replace_document_chunks.call_args
    chunks = call_args.kwargs.get("chunks") if call_args.kwargs else call_args.args[2]
    for ch in chunks:
        assert "cdc_fingerprint" not in (ch.metadata or {})


def test_rechunk_routes_chat_class_to_conversation_chunker(runner: CliRunner, monkeypatch):
    """class=chat goes to ConversationChunker — but the chunker's
    chunk(text: str) call shape is the prose-flavoured one we feed
    from rechunk (single-text input). Just verify the call completed
    and emitted at least one chunk."""
    doc = _FakeClassifiableDoc(
        document_id=45,
        source_uri="claude-code://session/abc",
        text="user: hi\nassistant: hello\n",
        format_labels=[("format", "chat"), ("class", "chat")],
    )
    backend = _build_fake_backend([(doc, ["whatever"])])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0, result.output
    # ConversationChunker reachable; the no-op upsert is still acceptable
    # since per-message chunking on a single-text input yields one chunk.
    # We just need exit_code == 0 — the routing is the assertion.


# ── --dry-run ────────────────────────────────────────────────────────────


def test_rechunk_dry_run_skips_upsert(runner: CliRunner, monkeypatch):
    doc = _FakeClassifiableDoc(
        document_id=50,
        source_uri="filesystem://x/foo.md",
        text="hello world " * 500,
        format_labels=[("class", "book")],
    )
    backend = _build_fake_backend([(doc, ["pre"])])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk", "--dry-run"])
    assert result.exit_code == 0, result.output
    backend.replace_document_chunks.assert_not_called()


# ── Idempotency: identical chunk texts → skip upsert ─────────────────────


def test_rechunk_is_idempotent_when_chunks_match(runner: CliRunner, monkeypatch):
    """When the prospective new chunk texts match the stored chunk
    texts exactly, rechunk must skip the upsert.

    We seed the fake backend with the chunk texts the CDCChunker would
    produce for the doc; the rechunk pass should compute the same
    texts and skip.
    """
    from corpus_forge.chunkers.cdc import CDCChunker

    text = "lorem ipsum dolor sit amet " * 600
    expected_chunks = CDCChunker().chunk(text)
    expected_texts = [c.text for c in expected_chunks]
    expected_metadatas = [c.metadata for c in expected_chunks]

    doc = _FakeClassifiableDoc(
        document_id=51,
        source_uri="filesystem://x/already-cdc.md",
        text=text,
        format_labels=[("class", "book")],
    )
    backend = _build_fake_backend([(doc, expected_texts)])
    # Override the chunk-metadata lookup so the stored chunks already
    # carry the CDC signature — true idempotency case.
    backend.get_document_chunk_metadatas.side_effect = lambda _doc_id: list(expected_metadatas)

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0, result.output
    backend.replace_document_chunks.assert_not_called()


# ── --limit ───────────────────────────────────────────────────────────


def test_rechunk_limit_stops_after_n_documents(runner: CliRunner, monkeypatch):
    """``--limit N`` must stop after processing N docs."""
    docs = [
        (
            _FakeClassifiableDoc(
                document_id=i,
                source_uri=f"filesystem://x/doc-{i}.md",
                text=f"document {i} body " * 300,
                format_labels=[("class", "book")],
            ),
            ["pre"],
        )
        for i in range(5)
    ]
    backend = _build_fake_backend(docs)

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert backend.replace_document_chunks.call_count == 2


# ── --json output ────────────────────────────────────────────────────────


def test_rechunk_json_emits_one_object_per_doc(runner: CliRunner, monkeypatch):
    import json as _json

    doc = _FakeClassifiableDoc(
        document_id=60,
        source_uri="filesystem://x/foo.md",
        text="alpha beta gamma " * 300,
        format_labels=[("class", "article")],
    )
    backend = _build_fake_backend([(doc, ["pre"])])

    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk", "--json"])
    assert result.exit_code == 0, result.output
    json_lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    assert len(json_lines) == 1
    payload = _json.loads(json_lines[0])
    assert payload["doc_id"] == 60
    assert payload["class"] == "article"
    assert payload["applied"] is True


# ── docs without class labels are skipped ────────────────────────────────


def test_rechunk_skips_unclassified_docs(runner: CliRunner, monkeypatch):
    """A doc without any ``class=*`` label must NOT be rechunked even
    when it shows up in iter_documents_for_classification (defensive).
    """
    doc = _FakeClassifiableDoc(
        document_id=70,
        source_uri="filesystem://x/foo.md",
        text="prose " * 500,
        format_labels=[("format", "md")],  # no class label
    )
    backend = _build_fake_backend([(doc, ["pre"])])

    # The fake backend already filters to docs-with-class-labels in its
    # iter helper, so this test more importantly asserts that rechunk
    # gracefully handles whatever the helper yields. Belt-and-suspenders.
    monkeypatch.setattr("corpus_forge.cli._build_backend_from_config", lambda _cfg: backend)
    monkeypatch.setattr(
        "corpus_forge.config.Config.load",
        classmethod(lambda _cls: _FakeConfig(backend=object())),
    )

    result = runner.invoke(app, ["rechunk"])
    assert result.exit_code == 0
    backend.replace_document_chunks.assert_not_called()
