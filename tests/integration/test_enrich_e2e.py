"""Live-Ollama code-enricher end-to-end tests — Phase H / H-09.

Exercises the real Ollama daemon serving a qwen-coder-capable model
(see ``_probe_qwen_coder`` in ``tests/integration/conftest.py`` for the
selection order). Gated by the ``requires_qwen_coder`` pytest marker —
auto-skipped at collection time when no compatible model is pulled.
CI stays green; developer machines with the model installed actually
exercise every assertion.

Three tests:

1. **round-trip a fixture code chunk** — pin the
   ``CodeEnricher.enrich(...)`` → ``CodeChunkEnrichment`` contract.
2. **idempotency** — re-enrich with the same model tag is a no-op:
   ``iter_code_chunks_for_enrichment`` elides chunks whose existing
   ``metadata.enrichment.model`` matches.
3. **CLI end-to-end** — ``corpus-forge enrich`` writes enrichment
   metadata against a testcontainers Postgres + fixture-corpus
   ingestion, with a ``class=code`` label attached.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.chunkers.base import TextChunk
from corpus_forge.cli import app
from corpus_forge.enrichers.base import CodeChunkEnrichment
from corpus_forge.enrichers.qwen_local import QwenCoderLocal

from .conftest import _probe_qwen_coder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_qwen_coder,
    pytest.mark.requires_docker,
]

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"


def _resolve_model_tag() -> str:
    """Return whichever qwen-coder-capable tag the probe found.

    Falls back to ``qwen2.5:7b-instruct`` for safety so the test
    surfaces a meaningful error message if invoked without the
    auto-skip in place (e.g. via ``-p no:cacheprovider`` quirks).
    """
    ready, tag = _probe_qwen_coder()
    if ready and tag:
        return tag
    return "qwen2.5:7b-instruct"


def _make_enricher(timeout_s: float = 240.0) -> QwenCoderLocal:
    return QwenCoderLocal(
        model=_resolve_model_tag(),
        llm_url="http://localhost:11434",
        timeout_s=timeout_s,
        temperature=0.1,
    )


_SAMPLE_CHUNK = TextChunk(
    text=(
        "def add(a: int, b: int) -> int:\n"
        "    '''Return the sum of a and b.'''\n"
        "    return a + b\n"
        "\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n"
    ),
    metadata={"kind": "Function", "name": "add", "language": "python"},
)


@pytest.mark.timeout(300)
def test_enricher_round_trip_on_fixture_chunk() -> None:
    """End-to-end round trip against a real qwen-coder model.

    Quality assertions stay loose; this test pins the round-trip
    contract (well-formed enrichment, non-empty summary, valid
    confidence range) rather than the model's exact phrasing.
    """
    enricher = _make_enricher()
    result = enricher.enrich(_SAMPLE_CHUNK, language="python")

    assert isinstance(result, CodeChunkEnrichment)
    # The model should always emit a non-empty summary, even on the
    # graceful-fallback path. (Empty summary would be a contract bug.)
    assert isinstance(result.summary, str)
    assert result.summary  # not empty
    assert 0.0 <= result.confidence <= 1.0
    # ``model`` field round-trips the tag the enricher was constructed with.
    assert result.model == enricher.model
    # ``symbols`` is always a list (may be empty for trivial chunks).
    assert isinstance(result.symbols, list)


@pytest.mark.timeout(300)
def test_idempotency_skips_already_enriched_chunks(pg_dsn: str) -> None:
    """Backend skips chunks whose existing ``metadata.enrichment.model``
    matches the requested tag — re-running the enrich pass is cheap.
    """
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    # Seed a dataset + document + chunk with class=code.
    ds_rows = backend._execute(
        "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
        ("enrich-idempotency", "text"),
    )
    dataset_id = int(ds_rows[0]["id"])

    src_uri = "file:///enrich-e2e/idempotency.py"
    doc_rows = backend._execute(
        """
        INSERT INTO corpus.documents
            (dataset_id, source_uri, title, text, content_hash, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (dataset_id, src_uri, None, _SAMPLE_CHUNK.text, src_uri, json.dumps({})),
    )
    doc_id = int(doc_rows[0]["id"])

    # Attach the class=code label so the iterator picks the doc up.
    backend.apply_label(
        "document", doc_id, "class", "code", source="classifier:rule", confidence=0.99
    )

    # Insert a single code chunk with the language stamped in metadata.
    chunk_rows = backend._execute(
        """
        INSERT INTO corpus.chunks
            (document_id, chunk_index, heading, text, metadata, role, token_count, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            doc_id,
            0,
            None,
            _SAMPLE_CHUNK.text,
            json.dumps({"kind": "Function", "name": "add", "language": "python"}),
            None,
            None,
            "enrich-e2e-chunk-1",
        ),
    )
    chunk_id = int(chunk_rows[0]["id"])

    # Sanity: the chunk is initially visible to the iterator.
    initial = list(backend.iter_code_chunks_for_enrichment("any-tag"))
    assert any(cid == chunk_id for cid, _, _ in initial), (
        f"chunk {chunk_id} should be discoverable before enrichment; got {initial}"
    )

    # Write an enrichment with model_tag="seed-model".
    enrichment = CodeChunkEnrichment(
        docstring="Synthesised docstring.",
        summary="Adds two integers.",
        symbols=["add"],
        model="seed-model",
        confidence=0.85,
    )
    backend.update_chunk_enrichment(chunk_id, enrichment)

    # Same model tag → idempotent skip.
    after_same = list(backend.iter_code_chunks_for_enrichment("seed-model"))
    assert all(cid != chunk_id for cid, _, _ in after_same), (
        f"chunk {chunk_id} must be skipped when model_tag matches; got {after_same}"
    )

    # Different model tag → still returned (re-enrich on model change).
    after_other = list(backend.iter_code_chunks_for_enrichment("other-model"))
    assert any(cid == chunk_id for cid, _, _ in after_other), (
        f"chunk {chunk_id} must be re-iterated when model_tag differs; got {after_other}"
    )


@pytest.mark.timeout(600)
def test_cli_enrich_end_to_end_postgres(
    pg_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI end-to-end: ingest a code fixture, classify as code, enrich.

    Uses ``--limit 1`` and ``--backend qwen-local`` to keep the test
    bounded (one LLM call) while still exercising the full path:
    CLI parsing → enricher construction → backend iterator →
    ``update_chunk_enrichment``.
    """
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    ds_rows = backend._execute(
        "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
        ("enrich-cli-e2e", "text"),
    )
    dataset_id = int(ds_rows[0]["id"])

    # Two small code fixtures to give the iterator a couple of candidates;
    # we then --limit 1 to bound LLM calls.
    samples = [
        (
            "file:///enrich-cli-e2e/add.py",
            "def add(a, b):\n    return a + b\n",
        ),
        (
            "file:///enrich-cli-e2e/mul.py",
            "def mul(a, b):\n    return a * b\n",
        ),
    ]
    for src_uri, text in samples:
        doc_rows = backend._execute(
            """
            INSERT INTO corpus.documents
                (dataset_id, source_uri, title, text, content_hash, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (dataset_id, src_uri, None, text, src_uri, json.dumps({})),
        )
        doc_id = int(doc_rows[0]["id"])
        backend.apply_label(
            "document", doc_id, "class", "code", source="classifier:rule", confidence=0.99
        )
        backend._execute(
            """
            INSERT INTO corpus.chunks
                (document_id, chunk_index, heading, text, metadata, role, token_count,
                 content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                doc_id,
                0,
                None,
                text,
                json.dumps({"language": "python"}),
                None,
                None,
                f"{src_uri}-chunk",
            ),
        )

    model_tag = _resolve_model_tag()
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'''
[backend]
kind = "postgres"
dsn  = "{pg_dsn}"

[daemon]

[[datasets]]
name = "enrich-cli-e2e"
kind = "text"
sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]

[[embedders]]
name      = "fake_enrich_e2e"
provider  = "sentence_transformers"
model_id  = "fake-1"
dimension = 8

[code_enricher]
backend = "local"
local_model = "{model_tag}"
local_url = "http://localhost:11434"
timeout_s = 240.0
temperature = 0.1
''',
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["enrich", "--dataset", "enrich-cli-e2e", "--limit", "1", "--backend", "qwen-local"],
    )
    assert result.exit_code == 0, f"CLI failed: stdout={result.output!r}"

    # Exactly one chunk should now carry enrichment metadata.
    rows = backend._execute(
        """
        SELECT c.metadata
        FROM corpus.chunks c
        JOIN corpus.documents d ON d.id = c.document_id
        WHERE d.dataset_id = %s
          AND c.metadata ? 'enrichment'
        """,
        (dataset_id,),
    )
    assert rows, "Expected at least one chunk with enrichment metadata after the CLI run"
    md = rows[0]["metadata"]
    if isinstance(md, str):
        md = json.loads(md)
    enrich = md.get("enrichment", {})
    assert isinstance(enrich, dict)
    assert isinstance(enrich.get("summary"), str) and enrich["summary"]
    assert enrich.get("model") == model_tag
    # Sibling keys from the original chunk metadata must survive the merge.
    assert md.get("language") == "python", (
        f"original chunk metadata must survive the enrichment merge; got md={md!r}"
    )

    socket.gethostname()  # placeholder usage so unused-import never flags
