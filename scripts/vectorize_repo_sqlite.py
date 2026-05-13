"""Vectorize a directory of markdown into a local SQLite + sqlite-vec db.

Usage:
    uv run python scripts/vectorize_repo_sqlite.py
    uv run python scripts/vectorize_repo_sqlite.py --vault-root /path/to/dir --db /tmp/my.db
    uv run python scripts/vectorize_repo_sqlite.py --model BAAI/bge-small-en-v1.5 --dimension 384

Defaults: ingest this repo's markdown files into /tmp/corpus-forge-test.db
using sentence-transformers/all-MiniLM-L6-v2 (384-dim, CPU). After ingest,
print row counts and the per-embedder vec0 table.

Pair with scripts/query_repo_sqlite.py to run semantic search against the
resulting database.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.ingest import ingest_once

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EXCLUDES = [
    ".git/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "node_modules/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    "htmlcov/**",
    "dist/**",
    "build/**",
    "*.egg-info/**",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--vault-root", default=str(REPO_ROOT), help="Directory to scan for markdown"
    )
    parser.add_argument("--db", default="/tmp/corpus-forge-test.db", help="SQLite db path")
    parser.add_argument(
        "--model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="sentence-transformers model_id",
    )
    parser.add_argument("--dimension", type=int, default=384, help="Embedding dimension")
    parser.add_argument("--embedder-name", default="minilm", help="Logical embedder name in db")
    parser.add_argument("--dataset-name", default="cf-self-docs", help="Logical dataset name")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda", "auto"])
    parser.add_argument("--max-chars", type=int, default=1500, help="Chunker max_chars")
    parser.add_argument("--overlap", type=int, default=200, help="Chunker overlap")
    parser.add_argument("--keep-existing", action="store_true", help="Do not wipe existing db")
    parser.add_argument("--exclude", action="append", default=None, help="Extra glob to exclude")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    excludes = list(DEFAULT_EXCLUDES)
    if args.exclude:
        excludes.extend(args.exclude)

    config = Config(
        backend=BackendConfig(kind="sqlite", dsn=args.db, schema="corpus"),
        daemon=DaemonConfig(debounce_seconds=2.0, log_level="INFO", log_format="text"),
        datasets=[
            DatasetConfig(
                name=args.dataset_name,
                kind="text",
                sync_enabled=False,
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root=args.vault_root,
                        exclude_globs=excludes,
                        chunker="markdown",
                        chunker_config={"max_chars": args.max_chars, "overlap": args.overlap},
                    )
                ],
            )
        ],
        embedders=[
            EmbedderConfig(
                name=args.embedder_name,
                provider="sentence_transformers",
                model_id=args.model,
                dimension=args.dimension,
                normalize=True,
                distance="cosine",
                active=True,
                batch_size=32,
                device=args.device,
            )
        ],
    )

    if not args.keep_existing:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{args.db}{suffix}").unlink(missing_ok=True)

    print(f"Vault:    {args.vault_root}")
    print(f"DB:       {args.db}")
    print(f"Model:    {args.model} ({args.dimension}-d)")
    print(f"Device:   {args.device}")
    print()

    ingest_once(config)

    print()
    print("=== Row counts ===")
    conn = sqlite3.connect(args.db)
    try:
        import sqlite_vec  # noqa: PLC0415

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except ImportError:
        pass  # vec0 table count will be skipped
    cur = conn.cursor()
    for table in ("datasets", "sources", "documents", "chunks", "embedders"):
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<12} {n}")
    row = cur.execute(
        "SELECT table_name FROM embedders WHERE name = ?", (args.embedder_name,)
    ).fetchone()
    if row:
        emb_table = row[0]
        try:
            n_emb = cur.execute(f"SELECT COUNT(*) FROM {emb_table}").fetchone()[0]
            print(f"  {emb_table:<20} {n_emb}")
        except sqlite3.OperationalError as e:
            print(f"  {emb_table:<20} (vec0 module not loaded: {e})")
    conn.close()


if __name__ == "__main__":
    main()
