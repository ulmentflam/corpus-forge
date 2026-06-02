"""T6 — smoke: ``corpus-forge chunk doc <doc_id> --reassemble --json``.

End-to-end happy path:

1. Build a tiny multi-chunk markdown document directly in a temp
   SQLite-backed corpus (no extractors, no embedders, no network).
2. Invoke the CLI via CliRunner with ``chunk doc <doc_id> --reassemble
   --json``.
3. Parse stdout as a single JSON object.
4. Assert ``payload["text"]`` is the concatenation of chunk texts in
   ``chunk_index`` order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.sources.base import RawDocument

pytestmark = pytest.mark.smoke


def _seed_backend(db_path: Path, *, chunk_texts: list[str]) -> int:
    """Create the dataset+document with chunk_texts and return doc_id."""
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (1, "smoke", "text"),
    )
    doc = RawDocument(
        source_uri="filesystem://smoke/long.md",
        content_hash="hsmoke",
        text="\n\n".join(chunk_texts),
        title="long",
        modified_at=1000.0,
        metadata={},
        labels=[],
    )
    chunks = [(None, t) for t in chunk_texts]
    return backend.upsert_document(1, doc, chunks)


def _smoke_config(db_path: Path) -> Config:
    return Config(
        backend=BackendConfig(kind="sqlite", dsn=str(db_path)),
        daemon=DaemonConfig(),
        datasets=[
            DatasetConfig(
                name="smoke",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root=str(db_path.parent),
                        chunker="markdown",
                    )
                ],
            )
        ],
        embedders=[
            EmbedderConfig(
                name="fake",
                provider="sentence_transformers",
                model_id="fake/m",
                dimension=8,
            )
        ],
    )


def test_chunk_doc_reassemble_concats_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reassembled text equals concat of chunk texts in chunk_index order."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    db_path = tmp_path / "corpus.db"
    chunk_texts = [
        "Alpha is the first chunk.",
        "Bravo is the second chunk.",
        "Charlie is the third chunk.",
        "Delta is the fourth chunk.",
    ]
    doc_id = _seed_backend(db_path, chunk_texts=chunk_texts)

    cfg = _smoke_config(db_path)

    def _fake_loader():
        return cfg

    def _fake_backend(_cfg):
        b = SQLiteBackend(path=str(db_path))
        b.migrate()
        return b

    # Patch out config + backend lookups so the CLI runs against tmp_path.
    monkeypatch.setattr(cli, "_load_config_quietly", _fake_loader)
    monkeypatch.setattr(cli, "_build_backend_from_config", _fake_backend)

    runner = CliRunner()
    result = runner.invoke(app, ["chunk", "doc", str(doc_id), "--reassemble", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["text"] == "".join(chunk_texts)
    assert payload["document"]["id"] == doc_id
    assert payload["document"]["source_uri"] == "filesystem://smoke/long.md"
    assert "chunks" not in payload


def test_chunk_doc_without_reassemble_lists_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default `chunk doc --json` emits ordered per-chunk entries."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    db_path = tmp_path / "corpus2.db"
    chunk_texts = ["one", "two", "three"]
    doc_id = _seed_backend(db_path, chunk_texts=chunk_texts)

    cfg = _smoke_config(db_path)
    monkeypatch.setattr(cli, "_load_config_quietly", lambda: cfg)

    def _fake_backend(_cfg):
        b = SQLiteBackend(path=str(db_path))
        b.migrate()
        return b

    monkeypatch.setattr(cli, "_build_backend_from_config", _fake_backend)

    runner = CliRunner()
    result = runner.invoke(app, ["chunk", "doc", str(doc_id), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [c["text"] for c in payload["chunks"]] == chunk_texts
    assert [c["chunk_index"] for c in payload["chunks"]] == [0, 1, 2]
