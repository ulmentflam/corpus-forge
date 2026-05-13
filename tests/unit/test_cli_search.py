"""R5-04 — `corpus-forge search` CLI command.

Pins the top-level ``search`` command surface and dispatch semantics:

- Help text advertises ``search`` at the top level and documents
  ``--k`` / ``--dataset`` / ``--fusion`` / ``--alpha`` / ``--rerank`` /
  ``--json`` flags.
- Dispatch reuses ``_build_retriever_for_eval`` (R3/R4 contract carried
  forward — same lazy retriever wiring as ``eval``).
- Default-off rerank: ``--no-rerank`` is the default; ``--rerank`` flips
  ``SearchOptions.rerank`` to ``True`` AND triggers
  ``_build_reranker_for_eval`` exactly once.
- ``--json`` writes a JSON object ``{"query": ..., "hits": [...]}`` to
  the given path (no JSON to stdout in non-json mode; pretty text only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner


# ── Fakes ────────────────────────────────────────────────────────────────


@dataclass
class _FakeHit:
    chunk_id: int
    score: float
    text: str
    document_id: int | None = None
    source_uri: str | None = None
    title: str | None = None
    dataset_id: int = 1
    metadata: dict | None = None
    source: str = "fused"

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class _FakeRetriever:
    def __init__(self, hits: list[_FakeHit]):
        self.hits = hits
        self.calls: list[tuple[str, Any]] = []
        self.reranker = None

    def search(self, query: str, options: Any) -> list[_FakeHit]:
        self.calls.append((query, options))
        return list(self.hits)


# ── Help surface ─────────────────────────────────────────────────────────


def test_search_command_appears_in_root_help() -> None:
    """`corpus-forge --help` advertises a top-level `search` command."""
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "search" in result.output, (
        f"`search` must appear in `corpus-forge --help`; got:\n{result.output}"
    )


def test_search_help_lists_required_flags() -> None:
    """`corpus-forge search --help` documents all the load-bearing flags."""
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--k", "--dataset", "--fusion", "--alpha", "--rerank", "--json"):
        assert flag in result.output, (
            f"`search --help` must list {flag!r}; got:\n{result.output}"
        )


# ── Dispatch ─────────────────────────────────────────────────────────────


def test_search_dispatches_query_to_retriever(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user's query string is passed through to retriever.search verbatim."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([_FakeHit(1, 0.9, "alpha hit")])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "how does lock_source work"])
    assert result.exit_code == 0, result.output
    assert len(fake.calls) == 1
    query, _opts = fake.calls[0]
    assert query == "how does lock_source work"


def test_search_default_k_is_10(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q"])
    assert result.exit_code == 0, result.output
    _, opts = fake.calls[0]
    assert opts.k == 10


def test_search_k_override_flows_into_options(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q", "--k", "5"])
    assert result.exit_code == 0, result.output
    _, opts = fake.calls[0]
    assert opts.k == 5


def test_search_dataset_override_flows_into_options(monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q", "--dataset", "vault"])
    assert result.exit_code == 0, result.output
    _, opts = fake.calls[0]
    assert opts.dataset == "vault"


def test_search_default_rerank_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-off rerank discipline carries from R4."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)
    # If a reranker builder were called, fail loudly.
    reranker_calls = {"count": 0}

    def boom(*_a, **_k):
        reranker_calls["count"] += 1
        raise AssertionError("Reranker must not be constructed when --no-rerank")

    monkeypatch.setattr(cli, "_build_reranker_for_eval", boom)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q"])
    assert result.exit_code == 0, result.output
    _, opts = fake.calls[0]
    assert opts.rerank is False
    assert reranker_calls["count"] == 0


def test_search_rerank_true_builds_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--rerank` triggers _build_reranker_for_eval exactly once."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    reranker_calls = {"count": 0}

    def fake_reranker_builder(**_kw):
        reranker_calls["count"] += 1
        return MagicMock(), 50

    monkeypatch.setattr(cli, "_build_reranker_for_eval", fake_reranker_builder)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q", "--rerank"])
    assert result.exit_code == 0, result.output
    assert reranker_calls["count"] == 1, (
        f"--rerank must build the reranker exactly once; got {reranker_calls['count']}"
    )
    _, opts = fake.calls[0]
    assert opts.rerank is True


def test_search_prints_hits_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --json, stdout shows the hits in a human-readable form."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever(
        [
            _FakeHit(11, 0.91, "alpha body", source_uri="file://a.md", title="Alpha"),
            _FakeHit(22, 0.84, "bravo body", source_uri="file://b.md", title="Bravo"),
        ]
    )
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q"])
    assert result.exit_code == 0, result.output
    # Both texts (or at least their chunk_ids) should appear.
    assert "11" in result.output or "alpha body" in result.output
    assert "22" in result.output or "bravo body" in result.output


def test_search_json_writes_payload_to_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--json PATH` writes a structured JSON dump."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever(
        [
            _FakeHit(11, 0.91, "alpha body", source_uri="file://a.md", title="Alpha"),
            _FakeHit(22, 0.84, "bravo body", source_uri="file://b.md", title="Bravo"),
        ]
    )
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    out_path = tmp_path / "hits.json"
    runner = CliRunner()
    result = runner.invoke(app, ["search", "lock_source", "--json", str(out_path)])
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    # Top-level shape:
    assert "query" in payload
    assert payload["query"] == "lock_source"
    assert "hits" in payload
    hits = payload["hits"]
    assert len(hits) == 2
    for hit in hits:
        assert "chunk_id" in hit
        assert "score" in hit
        assert "text" in hit
    chunk_ids = {h["chunk_id"] for h in hits}
    assert chunk_ids == {11, 22}


def test_search_fusion_alpha_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--fusion alpha --alpha 0.7` flows into SearchOptions."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q", "--fusion", "alpha", "--alpha", "0.7"])
    assert result.exit_code == 0, result.output
    _, opts = fake.calls[0]
    assert opts.fusion == "alpha"
    assert abs(opts.alpha - 0.7) < 1e-9
