"""T3 — `corpus-forge chunk {show,neighbors,doc}` CLI commands.

These commands let an agent explore corpus chunks without raw SQL:

- ``chunk show <id>`` — full chunk text + metadata. ``--json`` emits a
  single clean JSON object. ``--neighbors-hint`` (default on for JSON)
  appends ``prev_chunk_id`` / ``next_chunk_id``.
- ``chunk neighbors <id>`` — N before/after, JSON envelope splits
  ``before`` and ``after`` arrays.
- ``chunk doc <doc_id>`` — every chunk of a document, ordered. With
  ``--reassemble``, concatenates chunk texts and prints a stderr
  caveat about overlap.

All ``--json`` paths emit ONE JSON object on stdout, no log chatter,
exit code 0 on success / 2 on not-found.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeBackend:
    def __init__(self) -> None:
        self.chunks: dict[int, dict] = {}
        self.doc_chunks: dict[int, list[dict]] = {}

    def get_chunk(self, chunk_id: int) -> dict | None:
        return self.chunks.get(chunk_id)

    def get_chunk_neighbors(self, chunk_id: int, *, before: int = 1, after: int = 1) -> list[dict]:
        chunk = self.chunks.get(chunk_id)
        if chunk is None:
            return []
        doc_id = chunk.get("document_id")
        if doc_id is None:
            return []
        siblings = sorted(self.doc_chunks.get(doc_id, []), key=lambda c: c["chunk_index"])
        anchor_idx = chunk["chunk_index"]
        out: list[dict] = []
        for c in siblings:
            if c["chunk_index"] == anchor_idx:
                continue
            idx = c["chunk_index"]
            below = idx < anchor_idx and (anchor_idx - idx) <= before
            above = idx > anchor_idx and (idx - anchor_idx) <= after
            if below or above:
                out.append(c)
        out.sort(key=lambda c: c["chunk_index"])
        return out

    def get_document_chunks(self, document_id: int) -> list[dict]:
        return sorted(self.doc_chunks.get(document_id, []), key=lambda c: c["chunk_index"])


def _seed_doc(backend: _FakeBackend, doc_id: int, *, source_uri: str, texts: list[str]) -> None:
    rows: list[dict] = []
    chunk_id_base = doc_id * 100
    for i, t in enumerate(texts):
        cid = chunk_id_base + i
        rows.append(
            {
                "id": cid,
                "document_id": doc_id,
                "conversation_id": None,
                "message_id": None,
                "chunk_index": i,
                "text": t,
                "heading": None,
                "role": None,
                "token_count": len(t.split()),
                "metadata": {},
                "content_hash": f"h{cid}",
                "dataset_id": 1,
                "source_uri": source_uri,
                "title": f"doc-{doc_id}",
            }
        )
    for i, row in enumerate(rows):
        row["prev_chunk_id"] = rows[i - 1]["id"] if i > 0 else None
        row["next_chunk_id"] = rows[i + 1]["id"] if i + 1 < len(rows) else None
        backend.chunks[row["id"]] = row
    backend.doc_chunks[doc_id] = rows


@pytest.fixture
def patched_backend(monkeypatch: pytest.MonkeyPatch):
    backend = _FakeBackend()

    def _builder(*_a: Any, **_kw: Any) -> _FakeBackend:
        return backend

    from corpus_forge import cli

    # CI runs without ~/.config/corpus-forge/config.toml, so the eager
    # `_load_config_quietly()` returns None and the command exits with
    # code 2 ("No configuration found") before reaching the patched
    # backend builder. Stub it with a sentinel — the chunk commands
    # only pass it through to `_build_backend_from_config` which we
    # already stub to ignore its arg.
    monkeypatch.setattr(cli, "_load_config_quietly", lambda *_a, **_kw: object())
    monkeypatch.setattr(cli, "_build_backend_from_config", _builder)
    return backend


# ── chunk show ─────────────────────────────────────────────────────────


class TestChunkShow:
    def test_command_registered(self) -> None:
        from corpus_forge.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["chunk", "--help"])
        assert result.exit_code == 0, result.output
        assert "show" in result.output

    def test_json_emits_single_object(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=1,
            source_uri="filesystem://Notes/a.md",
            texts=["alpha block", "bravo block", "charlie block"],
        )
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "show", "101", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["chunk_id"] == 101
        assert payload["text"] == "bravo block"
        assert payload["chunk_index"] == 1
        assert payload["source_uri"] == "filesystem://Notes/a.md"
        # Default --neighbors-hint for JSON: on.
        assert payload["prev_chunk_id"] == 100
        assert payload["next_chunk_id"] == 102

    def test_json_no_log_chatter(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=1,
            source_uri="filesystem://Notes/a.md",
            texts=["alpha"],
        )
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "show", "100", "--json"])
        assert result.exit_code == 0, result.output
        # Whole stdout must parse as JSON.
        json.loads(result.stdout.strip())
        assert "event=" not in result.stdout

    def test_not_found_returns_exit_2(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "show", "9999", "--json"])
        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["code"] == "NOT_FOUND"

    def test_human_mode_prints_text(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=1,
            source_uri="filesystem://Notes/a.md",
            texts=["alpha block"],
        )
        runner = CliRunner()
        result = runner.invoke(app, ["chunk", "show", "100"])
        assert result.exit_code == 0, result.output
        assert "alpha block" in result.output


# ── chunk neighbors ────────────────────────────────────────────────────


class TestChunkNeighbors:
    def test_default_before_after_one_one(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=1,
            source_uri="filesystem://Notes/a.md",
            texts=["c0", "c1", "c2", "c3", "c4"],
        )
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "neighbors", "102", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["anchor_chunk_id"] == 102
        assert [c["chunk_index"] for c in payload["before"]] == [1]
        assert [c["chunk_index"] for c in payload["after"]] == [3]

    def test_before_after_overrides(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=1,
            source_uri="filesystem://Notes/a.md",
            texts=["c0", "c1", "c2", "c3", "c4"],
        )
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            app,
            ["chunk", "neighbors", "102", "--before", "2", "--after", "2", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert [c["chunk_index"] for c in payload["before"]] == [0, 1]
        assert [c["chunk_index"] for c in payload["after"]] == [3, 4]


# ── chunk doc ──────────────────────────────────────────────────────────


class TestChunkDoc:
    def test_doc_lists_all_chunks(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=7,
            source_uri="filesystem://Notes/long.md",
            texts=["a", "b", "c"],
        )
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "doc", "7", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["document"]["id"] == 7
        assert payload["document"]["source_uri"] == "filesystem://Notes/long.md"
        assert [c["chunk_index"] for c in payload["chunks"]] == [0, 1, 2]
        assert [c["text"] for c in payload["chunks"]] == ["a", "b", "c"]

    def test_reassemble_concats_texts(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        _seed_doc(
            patched_backend,
            doc_id=8,
            source_uri="filesystem://Notes/r.md",
            texts=["alpha", "bravo", "charlie"],
        )
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "doc", "8", "--reassemble", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["text"] == "alphabravocharlie"
        assert "chunks" not in payload

    def test_doc_not_found_exit_2(self, patched_backend: _FakeBackend) -> None:
        from corpus_forge.cli import app

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["chunk", "doc", "9999", "--json"])
        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["code"] == "NOT_FOUND"
