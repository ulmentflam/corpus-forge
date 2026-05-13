"""Semantic search a SQLite corpus-forge db built by vectorize_repo_sqlite.py.

Usage:
    uv run python scripts/query_repo_sqlite.py "how does the SQLite lock work"
    uv run python scripts/query_repo_sqlite.py "phase B coordination" --k 5 --db /tmp/my.db

Embeds the query with the same model used at ingest time, runs a nearest-
neighbour search via the sqlite-vec MATCH operator, and prints the top-k
chunks with their source path + a short preview.

The model and embedder-name default to the same values as
vectorize_repo_sqlite.py so the round-trip works out of the box.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import sqlite_vec
from sentence_transformers import SentenceTransformer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--db", default="/tmp/corpus-forge-test.db", help="SQLite db path")
    parser.add_argument("--k", type=int, default=10, help="Top-k results")
    parser.add_argument("--embedder-name", default="minilm", help="Logical embedder name in db")
    parser.add_argument(
        "--model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="sentence-transformers model_id (must match ingest)",
    )
    parser.add_argument("--preview-chars", type=int, default=240, help="Chunk preview length")
    args = parser.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"DB not found: {args.db}. Run scripts/vectorize_repo_sqlite.py first.")

    # Embed the query.
    model = SentenceTransformer(args.model, device="cpu")
    vec = model.encode([args.query], normalize_embeddings=True)[0].astype(np.float32)

    # Open the db with sqlite-vec loaded.
    conn = sqlite3.connect(args.db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Look up the per-embedder vec0 table.
    row = cur.execute(
        "SELECT id, table_name, dimension FROM embedders WHERE name = ?", (args.embedder_name,)
    ).fetchone()
    if row is None:
        sys.exit(f"No embedder named {args.embedder_name!r} in {args.db}.")
    emb_table, dim = row["table_name"], row["dimension"]
    if dim != len(vec):
        sys.exit(f"Query model dim {len(vec)} != db embedder dim {dim}; pick a matching --model.")

    # sqlite-vec MATCH expects a serialized FLOAT32 blob.
    hits = cur.execute(
        f"""
        SELECT chunk_id, distance
        FROM {emb_table}
        WHERE embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (sqlite_vec.serialize_float32(vec.tolist()), args.k),
    ).fetchall()

    if not hits:
        print("No hits.")
        return

    # Fetch chunk text + source path.
    chunk_ids = [h["chunk_id"] for h in hits]
    placeholders = ",".join("?" * len(chunk_ids))
    rows = cur.execute(
        f"""
        SELECT c.id AS chunk_id, c.text, d.source_uri, d.title
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    by_id = {r["chunk_id"]: r for r in rows}

    print(f"Query: {args.query!r}")
    print(f"DB:    {args.db}   (embedder={args.embedder_name}, dim={dim})")
    print()

    for i, hit in enumerate(hits, 1):
        r = by_id.get(hit["chunk_id"])
        if r is None:
            print(f"{i:>2}. [chunk {hit['chunk_id']} missing] distance={hit['distance']:.4f}")
            continue
        source = Path(r["source_uri"]).name if r["source_uri"] else "(unknown)"
        title = r["title"] or "—"
        preview = " ".join((r["text"] or "").split())[: args.preview_chars]
        print(f"{i:>2}. distance={hit['distance']:.4f}   {source}   ({title})")
        print(f"    {preview}{'…' if len(r['text'] or '') > args.preview_chars else ''}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
