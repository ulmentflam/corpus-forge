"""Re-embed corpus-forge chunks via Ollama's Qwen3 embedding, then query.

Reuses the existing SQLite db built by scripts/vectorize_repo_sqlite.py
(so you must run that first). Registers a NEW embedder (Qwen3 via Ollama's
OpenAI-compatible /v1/embeddings endpoint), embeds all existing chunks,
stores them in a separate vec0 table, then runs a few sample queries.

Usage:
    ollama pull qwen3-embedding:8b   # one-time, ~5 GB
    uv run python scripts/vectorize_repo_sqlite.py   # populates chunks
    uv run python scripts/qwen3_via_ollama.py "your query"
    uv run python scripts/qwen3_via_ollama.py --queries "q1" "q2" "q3"

Default: re-embeds with qwen3-embedding:8b (4096-d) via http://localhost:11434/v1
and runs three illustrative queries.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import sqlite_vec
from openai import OpenAI

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.embedders.openai import OpenAIEmbedder


def make_client(base_url: str) -> OpenAI:
    """OpenAI SDK pointed at Ollama's /v1 — needs any non-empty api_key."""
    os.environ.setdefault("OPENAI_API_KEY", "ollama-no-auth")
    return OpenAI(base_url=base_url, api_key=os.environ["OPENAI_API_KEY"])


def detect_dimension(client: OpenAI, model: str) -> int:
    """One-shot embed of a tiny string to discover the actual output dim."""
    resp = client.embeddings.create(model=model, input=["dimension probe"])
    return len(resp.data[0].embedding)


class _OllamaEmbedder(OpenAIEmbedder):
    """OpenAIEmbedder pointed at Ollama. Provider is reported as 'openai' so
    the rest of the corpus-forge stack treats it like the OpenAI provider —
    the only difference is the base_url override."""

    def __init__(self, name: str, model_id: str, dimension: int, base_url: str):
        super().__init__(
            name=name,
            model_id=model_id,
            dimension=dimension,
            normalized=True,
            distance="cosine",
            batch_size=8,  # Qwen3-8B is heavy; keep batches small to avoid timeouts
        )
        self._base_url = base_url

    def _get_client(self):
        if self._client is None:
            self._client = make_client(self._base_url)
        return self._client


def reembed_existing(db_path: str, embedder_name: str, model: str, dim: int, base_url: str) -> int:
    """Register a new embedder on the existing db and embed all chunks."""
    backend = SQLiteBackend(path=db_path, schema="corpus")
    backend.migrate()
    embedder = _OllamaEmbedder(name=embedder_name, model_id=model, dimension=dim, base_url=base_url)
    embedder.warmup()

    # Register the embedder; this also creates the per-embedder vec0 table.
    embedder_id = backend.register_embedder(embedder)
    missing = list(backend.chunks_missing_embedding(embedder_id))
    if not missing:
        print(f"[{embedder_name}] all chunks already embedded ({embedder_id=}).")
        return 0
    chunk_ids, texts = zip(*missing, strict=True)
    print(f"[{embedder_name}] embedding {len(texts)} chunks via {base_url}…")
    t0 = time.time()
    vectors = embedder.encode(list(texts))
    print(f"[{embedder_name}] encoded in {time.time() - t0:.1f}s; writing to db…")
    backend.write_embeddings(embedder_id, list(zip(chunk_ids, vectors, strict=True)))
    return len(texts)


def run_query(
    db_path: str, embedder_name: str, model: str, base_url: str, query: str, k: int = 5
) -> None:
    """Run one semantic-search query against the qwen3 vec0 table."""
    client = make_client(base_url)
    resp = client.embeddings.create(model=model, input=[query])
    qvec = np.array(resp.data[0].embedding, dtype=np.float32)
    qvec /= max(np.linalg.norm(qvec), 1e-12)

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    row = cur.execute(
        "SELECT table_name, dimension FROM embedders WHERE name = ?", (embedder_name,)
    ).fetchone()
    if row is None:
        sys.exit(f"No embedder named {embedder_name!r}")
    emb_table, dim = row["table_name"], row["dimension"]
    if dim != len(qvec):
        sys.exit(f"Query dim {len(qvec)} != db embedder dim {dim}")

    hits = cur.execute(
        f"SELECT chunk_id, distance FROM {emb_table} "
        f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(qvec.tolist()), k),
    ).fetchall()
    if not hits:
        print(f"  (no hits for {query!r})")
        return
    chunk_ids = [h["chunk_id"] for h in hits]
    placeholders = ",".join("?" * len(chunk_ids))
    rows = cur.execute(
        f"""
        SELECT c.id AS chunk_id, c.text, d.source_uri, d.title
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    by_id = {r["chunk_id"]: r for r in rows}

    preview_n = 220
    print(f"\n=== Query: {query!r} ===")
    for i, hit in enumerate(hits, 1):
        r = by_id.get(hit["chunk_id"])
        if r is None:
            print(f"{i:>2}. [chunk {hit['chunk_id']} missing] distance={hit['distance']:.4f}")
            continue
        source = Path(r["source_uri"]).name if r["source_uri"] else "(unknown)"
        title = r["title"] or "—"
        preview = " ".join((r["text"] or "").split())[:preview_n]
        print(f"{i:>2}. distance={hit['distance']:.4f}   {source}   ({title})")
        print(f"    {preview}{'…' if len(r['text'] or '') > preview_n else ''}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("queries", nargs="*", help="One or more query strings")
    parser.add_argument("--db", default="/tmp/corpus-forge-test.db")
    parser.add_argument("--model", default="qwen3-embedding:8b")
    parser.add_argument("--embedder-name", default="qwen3-8b")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--dimension", type=int, default=0, help="0 = probe at runtime")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--skip-embed", action="store_true", help="Just query, skip re-embed")
    args = parser.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"DB not found: {args.db}. Run scripts/vectorize_repo_sqlite.py first.")

    client = make_client(args.base_url)
    if args.dimension == 0:
        print(f"Probing dimension for {args.model}…")
        args.dimension = detect_dimension(client, args.model)
        print(f"Detected dimension: {args.dimension}")

    if not args.skip_embed:
        n = reembed_existing(args.db, args.embedder_name, args.model, args.dimension, args.base_url)
        print(f"[{args.embedder_name}] {n} new embeddings written.")

    queries = args.queries or [
        "how does the SQLite lock_source mutex work",
        "what does the validate_sync_gate validator do",
        "Phase B coordination between parallel lanes",
    ]
    for q in queries:
        run_query(args.db, args.embedder_name, args.model, args.base_url, q, k=args.k)


if __name__ == "__main__":
    main()
