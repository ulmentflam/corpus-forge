"""T4 — ``corpus-forge search --json -`` clean-stdout contract.

The current ``--json`` accepts a file path. This adds the ``--json -``
sentinel meaning "emit ONE JSON object to stdout, suppress all logging
chatter, exit 0".

Back-compat: ``--json <PATH>`` still writes to a file (existing tests
in test_cli_search.py cover that).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from typer.testing import CliRunner


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


def test_search_json_stdout_emits_single_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--json -` writes ONE JSON object to stdout, parses cleanly."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever(
        [
            _FakeHit(11, 0.91, "alpha body", source_uri="filesystem://x/a.md"),
            _FakeHit(22, 0.84, "bravo body", source_uri="filesystem://x/b.md"),
        ]
    )
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(app, ["search", "q", "--k", "2", "--json", "-"])
    assert result.exit_code == 0, result.output
    # Single JSON object: no leading log lines, no trailing chatter.
    payload = json.loads(result.stdout)
    assert payload["query"] == "q"
    assert payload["k"] == 2
    assert isinstance(payload["took_ms"], int | float)
    assert payload["took_ms"] >= 0
    assert isinstance(payload["hits"], list)
    assert len(payload["hits"]) == 2
    assert payload["hits"][0]["chunk_id"] == 11


def test_search_json_stdout_no_log_chatter(monkeypatch: pytest.MonkeyPatch) -> None:
    """No INFO/agent-event lines may appear on stdout when `--json -`."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([_FakeHit(1, 0.9, "alpha")])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(app, ["search", "q", "--json", "-"])
    assert result.exit_code == 0, result.output
    # Stdout must be parseable as JSON in its entirety — no extra lines.
    stripped = result.stdout.strip()
    json.loads(stripped)  # raises if there's trailing garbage
    # And no `event=` agent-mode lines.
    assert "event=" not in result.stdout


def test_search_json_file_path_backcompat(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--json <PATH>` still writes to a file (existing contract)."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever([_FakeHit(11, 0.91, "alpha body")])
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    out_path = tmp_path / "hits.json"
    runner = CliRunner()
    result = runner.invoke(app, ["search", "q", "--json", str(out_path)])
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["query"] == "q"
    assert "hits" in payload


def test_search_without_json_keeps_human_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `--json` → existing human rank-list behavior unchanged."""
    from corpus_forge import cli
    from corpus_forge.cli import app

    fake = _FakeRetriever(
        [
            _FakeHit(11, 0.91, "alpha body", source_uri="file://a.md"),
            _FakeHit(22, 0.84, "bravo body", source_uri="file://b.md"),
        ]
    )
    monkeypatch.setattr(cli, "_build_retriever_for_eval", lambda *a, **k: fake)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "q"])
    assert result.exit_code == 0, result.output
    # Either chunk_id or text body is present — pre-existing assertion shape.
    assert "11" in result.output or "alpha body" in result.output
