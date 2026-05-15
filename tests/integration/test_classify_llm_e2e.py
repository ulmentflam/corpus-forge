"""Live-Ollama LLM-classifier end-to-end tests — Phase E / Wave 4 (C-12).

These tests exercise the real Ollama daemon serving
``qwen2.5:7b-instruct``. They are gated by the ``requires_ollama_text``
pytest marker — ``tests/integration/conftest.py`` auto-skips them at
collection time when the daemon is unreachable or the model is not
pulled. CI stays green; developer machines with ``ollama serve`` +
``ollama pull qwen2.5:7b-instruct`` actually exercise every assertion.

Wall-clock budget: each test < 60 s, full file < 120 s on M-series.
The cost-guard short-circuit test (#3) does not call Ollama at all.

Fixtures used (built by ``scripts/build_fixture_corpus.py``):

- ``tests/fixtures/multi_format_corpus/prose/intro.md`` — ambiguous
  prose; the rule classifier emits a low-confidence ``note`` label
  and the LLM is consulted.
- ``tests/fixtures/multi_format_corpus/code/python/hello.py`` —
  obvious code; the rule classifier short-circuits at confidence 0.99.
"""

from __future__ import annotations

import socket
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from typer.testing import CliRunner

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.classifiers.base import (
    ALLOWED_CLASS_VALUES,
    ClassifiableDocument,
    ClassLabel,
)
from corpus_forge.classifiers.llm import LLMClassifier
from corpus_forge.classifiers.registry import ClassifierRegistry
from corpus_forge.classifiers.rule_based import RuleBasedClassifier
from corpus_forge.cli import app
from corpus_forge.config import ExtractionConfig
from corpus_forge.ingest import ChunkerDispatcher, ingest_one
from corpus_forge.sources.filesystem import FilesystemSource

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_ollama_text,
    pytest.mark.requires_docker,
]

_MODEL = "qwen2.5:7b-instruct"
_OLLAMA_URL = "http://localhost:11434"
_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"


def _make_classifier(*, timeout_s: float = 60.0) -> LLMClassifier:
    return LLMClassifier(
        model=_MODEL,
        llm_url=_OLLAMA_URL,
        timeout_s=timeout_s,
    )


@pytest.mark.timeout(60)
def test_llm_classifier_real_document() -> None:
    """End-to-end round trip against a real qwen2.5:7b-instruct.

    The fixture is an ambiguous prose markdown doc — the LLM should
    return SOMETHING in the 9-enum at a valid confidence. Quality
    assertions stay loose; this test pins the round-trip contract,
    not the model's taxonomy preferences.
    """
    doc = ClassifiableDocument(
        document_id=1,
        source_uri="file:///tests/llm-e2e/prose.md",
        title="Reflections on the year",
        text=(
            "Looking back on this year I find myself drawn to certain memories — "
            "the way the light hit the kitchen window in October, the smell of "
            "the new book on my desk. These are private moments, not the kind of "
            "things I would publish, but worth keeping in a notebook somewhere. "
            "Tomorrow I'll write about the trip to Maine."
        ),
        format_labels=[("format", "markdown")],
        metadata={},
    )
    clf = _make_classifier()
    label = clf.classify(doc)
    assert isinstance(label, ClassLabel)
    assert label.value in ALLOWED_CLASS_VALUES
    assert 0.0 <= label.confidence <= 1.0
    # The model should produce *some* rationale — empty would be a
    # contract violation (model failed to honour the JSON schema).
    assert label.rationale  # not empty


@pytest.mark.timeout(90)
def test_escalation_chain_fires_llm() -> None:
    """Build [Rule, LLM] with threshold=0.4; LLM wins on a weak rule signal.

    The fixture text has no path heuristics, no chat markers, and no
    PDF signal. The rule classifier falls back to ``other`` at 0.3,
    which is below the 0.4 threshold, so the chain walks to the LLM.
    """
    reg = ClassifierRegistry()
    reg.register(RuleBasedClassifier())
    reg.register(_make_classifier())

    doc = ClassifiableDocument(
        document_id=2,
        source_uri="file:///opaque/blob.dat",
        title=None,
        text=(
            "Once upon a time there was a kingdom by the sea, and in that "
            "kingdom there lived a princess named Linda who hated rules. "
            "Every morning she would walk down to the harbour and speak to "
            "the gulls, and the gulls — being polite — would speak back."
        ),
        format_labels=[],
        metadata={},
    )
    outcome = reg.classify(doc, threshold=0.4)
    assert outcome is not None
    winner, label = outcome
    # Either the LLM won outright (confidence >= 0.4) or it landed as
    # the fallback last-seen label. Either way the chain produced an
    # LLM-source label here, because the rule classifier's "no rule
    # matched" → other 0.3 is below threshold and the LLM is the only
    # later classifier.
    assert winner == "llm"
    assert label.value in ALLOWED_CLASS_VALUES


@pytest.mark.timeout(30)
def test_high_confidence_rule_skips_llm() -> None:
    """Cost guard: an obvious-code document never reaches the LLM.

    Spy on ``requests.post`` to confirm no HTTP request fires when
    the rule classifier returns a 0.99 confidence ``code`` label.
    """
    reg = ClassifierRegistry()
    reg.register(RuleBasedClassifier())
    reg.register(_make_classifier())

    doc = ClassifiableDocument(
        document_id=3,
        source_uri="file:///proj/main.py",
        title=None,
        text="def main(): print('hello')",
        format_labels=[("format", "code"), ("language", "python")],
        metadata={},
    )
    with patch("requests.post") as mock_post:
        outcome = reg.classify(doc, threshold=0.4)
    assert outcome is not None
    winner, label = outcome
    assert winner == "rule"
    assert label.value == "code"
    assert mock_post.call_count == 0


# ---------------------------------------------------------------------------
# CLI end-to-end against testcontainers Postgres
# ---------------------------------------------------------------------------


_FAKE_DIM = 8


class _FakeEmbedder:
    """Same shape as the D-18 fake embedder — keeps the ingest path
    exercised without loading real model weights."""

    name: str = "fake_classify_llm_e2e"
    provider: str = "fake"
    model_id: str = "fake-v1"
    dimension: int = _FAKE_DIM
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self) -> None:
        self.encode_calls = 0

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_calls += 1
        n = len(texts)
        vecs = np.zeros((n, _FAKE_DIM), dtype=np.float32)
        for i in range(n):
            vecs[i, i % _FAKE_DIM] = 1.0
        return vecs

    def warmup(self) -> None:  # pragma: no cover — trivial
        pass


def _make_backend(pg_dsn: str) -> PostgresBackend:
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()
    return backend


def _create_dataset(backend: PostgresBackend, name: str) -> int:
    rows = backend._execute(
        "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
        (name, "text"),
    )
    return int(rows[0]["id"])


def _ingest_filesystem(
    backend: PostgresBackend,
    source: FilesystemSource,
    dataset_id: int,
    embedder: _FakeEmbedder,
) -> int:
    from corpus_forge.chunkers.markdown import MarkdownChunker

    backend.register_source(
        dataset_id,
        source.name,
        source.identity(),
        socket.gethostname(),
    )
    dispatcher = ChunkerDispatcher()
    fallback = MarkdownChunker()
    parsed = 0
    for raw_item in source.scan():
        if raw_item is None:
            continue
        chunker = dispatcher.dispatch_for(raw_item, fallback)
        try:
            ingest_one(backend, raw_item, chunker, [embedder], dataset_id)
            parsed += 1
        except Exception as exc:  # pragma: no cover — surfaced as failure
            raise AssertionError(f"ingest_one raised on {raw_item.source_uri!r}: {exc}") from exc
    return parsed


def _write_config_for_dsn(tmp_path: Path, pg_dsn: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''
[backend]
kind = "postgres"
dsn  = "{pg_dsn}"

[daemon]

[[datasets]]
name = "classify-llm-e2e"
kind = "text"
sources = [{{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}}]

[[embedders]]
name      = "fake_classify_llm_e2e"
provider  = "sentence_transformers"
model_id  = "fake-1"
dimension = 8

[classifier]
chain = ["rule", "llm"]
escalation_threshold = 0.4
llm_model = "{_MODEL}"
llm_url = "{_OLLAMA_URL}"
llm_timeout_s = 60.0
''',
        encoding="utf-8",
    )
    return cfg


@pytest.mark.timeout(180)
def test_cli_end_to_end_postgres(
    pg_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI E2E: ingest fixture corpus + a low-signal doc → classify
    with ``[rule, llm]`` → assert both ``classifier:rule`` AND
    ``classifier:llm`` sources land on ``document_labels``.

    The fixture corpus has a lot of strong-signal documents — code
    files (rule: 0.99), markdown notes (rule: 0.5+), structured data
    (rule: 0.7/0.9), etc. To guarantee the LLM path fires we add a
    single hand-crafted doc with NO format labels and NO path
    heuristics so the rule classifier falls back to ``other`` 0.3 and
    the chain escalates.
    """
    import json as _json

    backend = _make_backend(pg_dsn)
    dataset_id = _create_dataset(backend, "classify-llm-e2e")
    embedder = _FakeEmbedder()

    # Walk a small ingested subset (code → rule:code 0.99). Keep the
    # exclude list aggressive: the whole-corpus walk would push the
    # wall-clock past 180 s when every uncovered doc fires an LLM call.
    source = FilesystemSource(
        root=_FIXTURE_ROOT / "code" / "python",
        exclude_globs=[],
        extraction=ExtractionConfig(),
        debounce=0.0,
    )
    parsed = _ingest_filesystem(backend, source, dataset_id, embedder)
    assert parsed > 0

    # Hand-craft a single low-signal document so the LLM path is
    # guaranteed to fire (no format=code, no chat URI, no path hint,
    # no structured-data extension → rule fallback to ``other`` 0.3).
    src_uri = "file:///llm-e2e/opaque-blob.dat"
    text = (
        "An entry written in the small hours of the night. The author rambles "
        "about clouds, the price of coffee, and a friend's recent move. "
        "Nothing here is structured, machine-readable, or pedagogical."
    )
    backend._execute(
        """
        INSERT INTO corpus.documents
            (dataset_id, source_uri, title, text, content_hash, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (dataset_id, src_uri, None, text, src_uri, _json.dumps({})),
    )

    cfg_path = _write_config_for_dsn(tmp_path, pg_dsn)
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(cfg_path))

    runner = CliRunner()
    result = runner.invoke(app, ["classify", "--dataset", "classify-llm-e2e"])
    assert result.exit_code == 0, f"CLI failed: {result.output}"

    rows = backend._execute(
        """
        SELECT dl.source
        FROM corpus.documents d
        JOIN corpus.document_labels dl ON dl.document_id = d.id
        JOIN corpus.labels l ON l.id = dl.label_id
        WHERE d.dataset_id = %s AND l.namespace = 'class'
        """,
        (dataset_id,),
    )
    sources = {r["source"] for r in rows}
    assert "classifier:rule" in sources, (
        f"Expected at least one classifier:rule label; saw sources={sources}"
    )
    assert "classifier:llm" in sources, (
        f"Expected at least one classifier:llm label; saw sources={sources}"
    )
