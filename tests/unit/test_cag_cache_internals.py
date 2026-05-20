"""Unit tests for ``corpus_forge.cag.cache`` internals.

The structural / contract tests already live in
``tests/unit/test_cag_cache_builder.py``.  This file targets the
internal helpers (``_fetch_chunks``, ``_render_template``) plus the
end-to-end ``build_cache`` flow against a minimal SQLite schema so the
lines around lines 92-136 (the un-monkeypatched real DB path) get hit.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from corpus_forge.cag.cache import (
    _fetch_chunks,
    _render_template,
    build_cache,
)


def _seed_db(conn: sqlite3.Connection) -> None:
    """Seed the minimal datasets/documents/chunks schema needed by _fetch_chunks."""
    conn.executescript(
        """
        CREATE TABLE datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            source_uri TEXT,
            content_hash TEXT
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            content_hash TEXT
        );

        INSERT INTO datasets (id, name) VALUES (1, 'demo');
        INSERT INTO documents (id, dataset_id, source_uri, content_hash)
            VALUES (1, 1, 'file:///a.md', 'doc-h-1');
        INSERT INTO chunks (id, document_id, text, content_hash)
            VALUES (1, 1, 'first chunk text', 'h1');
        INSERT INTO chunks (id, document_id, text, content_hash)
            VALUES (2, 1, 'second chunk text', 'h2');
        INSERT INTO chunks (id, document_id, text, content_hash)
            VALUES (3, 1, 'third chunk text', 'h3');
        """
    )
    conn.commit()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    _seed_db(c)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_chunks
# ─────────────────────────────────────────────────────────────────────────────


def test_fetch_chunks_returns_dataset_id_and_rows(conn: sqlite3.Connection) -> None:
    dataset_id, chunks = _fetch_chunks(conn, "demo", top_k=10)
    assert dataset_id == 1
    assert len(chunks) == 3
    # Ordered by chunks.id ASC.
    assert [c["id"] for c in chunks] == [1, 2, 3]
    assert chunks[0]["text"] == "first chunk text"
    assert chunks[0]["content_hash"] == "h1"


def test_fetch_chunks_respects_top_k(conn: sqlite3.Connection) -> None:
    _, chunks = _fetch_chunks(conn, "demo", top_k=2)
    assert len(chunks) == 2
    assert [c["id"] for c in chunks] == [1, 2]


def test_fetch_chunks_unknown_dataset_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        _fetch_chunks(conn, "missing-dataset", top_k=10)


# ─────────────────────────────────────────────────────────────────────────────
# _render_template
# ─────────────────────────────────────────────────────────────────────────────


def test_render_template_chatml_includes_sentinels() -> None:
    chunks = [{"text": "alpha"}, {"text": "beta"}]
    out = _render_template(chunks, "chatml")
    assert "<|im_start|>system" in out
    assert "alpha" in out
    assert "beta" in out
    assert "<|im_end|>" in out


def test_render_template_fallback_joins_with_double_newline() -> None:
    chunks = [{"text": "alpha"}, {"text": "beta"}]
    out = _render_template(chunks, "any-other-template")
    assert out == "alpha\n\nbeta"


def test_render_template_handles_missing_text_key() -> None:
    chunks = [{"text": "alpha"}, {}]  # second chunk has no text
    out = _render_template(chunks, "chatml")
    # No crash; missing text becomes empty.
    assert "alpha" in out


# ─────────────────────────────────────────────────────────────────────────────
# build_cache — full flow
# ─────────────────────────────────────────────────────────────────────────────


def test_build_cache_writes_json_with_expected_fields(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    out_path = build_cache(conn, "demo", root=tmp_path)
    assert out_path.is_file()
    import json

    payload = json.loads(out_path.read_text())
    assert payload["dataset"] == "demo"
    assert payload["template"] == "chatml"
    assert "content_hashes" in payload
    assert "messages" in payload
    assert "built_at" in payload
    assert "cache_key" in payload


def test_build_cache_top_k_limits_chunks(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out_path = build_cache(conn, "demo", top_k=2, root=tmp_path)
    import json

    payload = json.loads(out_path.read_text())
    assert len(payload["content_hashes"]) == 2


def test_build_cache_path_layout(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out_path = build_cache(conn, "demo", root=tmp_path)
    # File lives at root/<dataset>/<key>.json
    assert out_path.parent.name == "demo"
    assert out_path.parent.parent == tmp_path
    assert out_path.suffix == ".json"


def test_build_cache_unknown_dataset_raises(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        build_cache(conn, "missing-dataset", root=tmp_path)


def test_build_cache_with_custom_template(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out_path = build_cache(conn, "demo", template="raw", root=tmp_path)
    import json

    payload = json.loads(out_path.read_text())
    assert payload["template"] == "raw"
    # Generic fallback joins with double newline.
    assert "first chunk text" in payload["messages"]
