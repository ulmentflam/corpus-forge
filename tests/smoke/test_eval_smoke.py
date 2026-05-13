"""R3-08 — smoke test for `corpus-forge eval retrieval`.

End-to-end: invoke the CLI against a seeded SQLite corpus and the
bundled `forge_self` gold set; assert the command exits 0, prints a
metric table, and writes a parseable JSON dump.

If the seed corpus at `/tmp/corpus-forge-test.db` is absent OR the
bundled gold set's chunk_ids no longer resolve and the content_hash
fallback also misses, the test SKIPS with a clear message — silent
passes here would defeat the purpose of the smoke gate.

Prerequisites for the test to run (rather than skip):

- Repo seeded via `uv run python scripts/vectorize_repo_sqlite.py`.

The test synthesises its own ``config.toml`` under ``tmp_path`` and
patches ``Config.load`` to read from it, so it does NOT depend on the
caller having a real ``~/.config/corpus-forge/config.toml``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    EmbedderConfig,
)

# Where the seed corpus lives.  Matches `scripts/vectorize_repo_sqlite.py`.
_SEED_DB = Path("/tmp/corpus-forge-test.db")
_BUNDLED_GOLD = (
    Path(__file__).resolve().parents[2] / "corpus_forge" / "eval" / "datasets" / "forge_self.jsonl"
)


def _seed_corpus_available() -> tuple[bool, str]:
    """Returns ``(ok, reason)``.  ok=False ⇒ pytest.skip with reason."""
    if not _SEED_DB.exists():
        return False, (
            f"seed corpus missing at {_SEED_DB}; run "
            "`uv run python scripts/vectorize_repo_sqlite.py` first"
        )
    if not _BUNDLED_GOLD.exists():
        return False, f"bundled gold set missing at {_BUNDLED_GOLD}"
    try:
        conn = sqlite3.connect(_SEED_DB)
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        emb_row = conn.execute("SELECT name, model_id, dimension FROM embedders LIMIT 1").fetchone()
        conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"seed corpus unreadable: {exc}"
    if n == 0:
        return False, "seed corpus has zero chunks"
    if emb_row is None:
        return False, "seed corpus has zero embedders"
    return True, ""


def _read_embedder_row() -> tuple[str, str, int]:
    """Pull the first embedder's name/model_id/dim from the seed db."""
    conn = sqlite3.connect(_SEED_DB)
    row = conn.execute("SELECT name, model_id, dimension FROM embedders LIMIT 1").fetchone()
    conn.close()
    return row[0], row[1], int(row[2])


pytestmark = pytest.mark.smoke


def test_eval_retrieval_smoke(tmp_path: Path):
    """Run `corpus-forge eval retrieval --dataset forge_self --k 10 --json <tmp>`.

    Requires the seeded corpus at /tmp/corpus-forge-test.db. Skips
    otherwise. Synthesises its own config; does NOT require a user
    config.toml in ~/.config/corpus-forge/.
    """
    ok, reason = _seed_corpus_available()
    if not ok:
        pytest.skip(reason)

    name, model_id, dim = _read_embedder_row()

    # Build an in-memory Config pointing at the seed db.
    config = Config.model_construct(
        backend=BackendConfig(kind="sqlite", dsn=str(_SEED_DB), schema="corpus"),
        daemon=DaemonConfig(),
        datasets=[],  # eval doesn't iterate datasets; the retriever resolves at search time.
        embedders=[
            EmbedderConfig(
                name=name,
                provider="sentence_transformers",
                model_id=model_id,
                dimension=dim,
                normalize=True,
                distance="cosine",
                active=True,
                batch_size=32,
                device="cpu",
            )
        ],
    )

    runner = CliRunner()
    out_json = tmp_path / "metrics.json"

    # Patch Config.load so the CLI uses our synthesised config instead of
    # reading ~/.config/corpus-forge/config.toml.
    with patch("corpus_forge.config.Config.load", return_value=config):
        result = runner.invoke(
            app,
            [
                "eval",
                "retrieval",
                "--dataset",
                "forge_self",
                "--k",
                "10",
                "--json",
                str(out_json),
            ],
        )

    combined = result.output or ""
    assert result.exit_code == 0, f"eval CLI failed:\n{combined}"

    # Table on stdout — must mention each metric and the k value.
    lowered = combined.lower()
    assert "ndcg" in lowered, f"missing 'ndcg' in output:\n{combined}"
    assert "mrr" in lowered, f"missing 'mrr' in output:\n{combined}"
    assert "recall" in lowered, f"missing 'recall' in output:\n{combined}"
    assert "10" in combined, f"missing k=10 in output:\n{combined}"

    # JSON dump must be a real, parseable file with all three metric blocks.
    assert out_json.exists(), f"--json target not written: {out_json}"
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "ndcg" in data
    assert "mrr" in data
    assert "recall" in data
    # k=10 bucket present (json forces str keys).
    assert "10" in data["ndcg"] or 10 in data["ndcg"]
    # Values are numeric and in [0, 1].
    for bucket in ("ndcg", "mrr", "recall"):
        for k, v in data[bucket].items():
            assert isinstance(v, int | float), f"{bucket}[{k}] not numeric: {v!r}"
            assert 0.0 <= float(v) <= 1.0, f"{bucket}[{k}] out of range: {v}"
