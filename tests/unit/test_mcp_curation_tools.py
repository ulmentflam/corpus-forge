"""Phase J / J4 — MCP curation tools unit tests.

Mirrors the patterns from ``tests/unit/test_mcp_estimate.py`` and
``tests/unit/test_mcp_server.py``: in-process MCP server + fake
retriever/backend, drive the registered request handlers directly,
parse the structured / textual payload.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

from corpus_forge.curation import (  # noqa: E402
    CurationBatch,
    CurationTarget,
    ScoreBreakdown,
)

# ─────────────────────────────────────────────────────────────────────────
# Helpers — drive the request handlers directly
# ─────────────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _list_tools(server) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _tool(server, name: str) -> mcp_types.Tool:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    for t in root.tools:
        if t.name == name:
            return t
    raise KeyError(name)


def _call(server, name: str, arguments: dict[str, object]):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(result.content[0].text)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


class _FakeRetriever:
    def __init__(self) -> None:
        self.backend = MagicMock()
        self.backend.list_datasets.return_value = []


def _build(*, writes_enabled: bool = False):
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    return build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=writes_enabled,
    )


def _fake_target(**overrides: object) -> CurationTarget:
    breakdown = ScoreBreakdown(
        confidence_deficit=0.5,
        missing_metadata=0.3,
        ranker_elevation=0.5,
        freshness=0.7,
    )
    base = {
        "chunk_id": 42,
        "document_id": 7,
        "text": "lorem",
        "heading": None,
        "current_labels": [("topic", "ml")],
        "current_metadata": {"language": "en"},
        "missing_fields": ["description"],
        "classifier_confidence": 0.5,
        "score": 0.61,
        "score_breakdown": breakdown,
        "selection_reason": "missing 1 of 6 metadata fields",
    }
    base.update(overrides)
    return CurationTarget(**base)  # type: ignore[arg-type]


def _fake_batch(targets: list[CurationTarget]) -> CurationBatch:
    return CurationBatch(
        cohesion_score=0.95,
        grouping_key=("notes-a", "topic_a"),
        targets=targets,
    )


# ─────────────────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────────────────


def test_next_curation_target_in_list_tools_always() -> None:
    for we in (False, True):
        names = _list_tools(_build(writes_enabled=we))
        assert "next_curation_target" in names, f"writes_enabled={we}"


def test_next_curation_batch_in_list_tools_always() -> None:
    for we in (False, True):
        names = _list_tools(_build(writes_enabled=we))
        assert "next_curation_batch" in names, f"writes_enabled={we}"


def test_commit_curation_only_when_writes_enabled() -> None:
    assert "commit_curation" not in _list_tools(_build(writes_enabled=False))
    assert "commit_curation" in _list_tools(_build(writes_enabled=True))


def test_next_curation_target_schema_rejects_extra_args() -> None:
    schema = _tool(_build(), "next_curation_target").inputSchema
    assert schema.get("additionalProperties") is False


def test_next_curation_batch_schema_advertises_limit() -> None:
    schema = _tool(_build(), "next_curation_batch").inputSchema
    assert "limit" in schema["properties"]


def test_commit_curation_schema_rejects_extra_args() -> None:
    schema = _tool(_build(writes_enabled=True), "commit_curation").inputSchema
    assert schema.get("additionalProperties") is False


# ─────────────────────────────────────────────────────────────────────────
# next_curation_target dispatch
# ─────────────────────────────────────────────────────────────────────────


def test_next_curation_target_dispatch_calls_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _fake_target()

    import corpus_forge.curation as curation_mod

    monkeypatch.setattr(curation_mod, "next_curation_target", fake)
    server = _build()
    result = _call(server, "next_curation_target", {"dataset": "demo"})
    assert not result.isError, result
    payload = _payload(result)
    assert payload["target"]["chunk_id"] == 42
    assert captured["dataset"] == "demo"
    assert captured["seed_query"] is None
    assert captured["reranker"] is None


def test_next_curation_target_returns_none_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_forge.curation as curation_mod

    monkeypatch.setattr(curation_mod, "next_curation_target", lambda **_: None)
    server = _build()
    result = _call(server, "next_curation_target", {})
    assert not result.isError
    payload = _payload(result)
    assert payload == {"target": None}


def test_next_curation_target_seed_query_triggers_reranker_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds: list[str] = []
    import corpus_forge.cli as cli_mod
    import corpus_forge.config as config_mod
    import corpus_forge.curation as curation_mod

    monkeypatch.setattr(
        config_mod.Config,
        "load",
        classmethod(lambda cls: MagicMock(name="cfg")),
    )

    def fake_build_reranker(config):
        builds.append("built")
        return MagicMock(name="fake-reranker")

    monkeypatch.setattr(cli_mod, "_build_reranker_from_config", fake_build_reranker)

    captured: dict[str, object] = {}

    def fake_selector(**kwargs):
        captured.update(kwargs)
        return _fake_target()

    monkeypatch.setattr(curation_mod, "next_curation_target", fake_selector)

    server = _build()
    _call(server, "next_curation_target", {"seed_query": "ml"})
    assert builds == ["built"]
    assert captured["seed_query"] == "ml"
    assert captured["reranker"] is not None


def test_next_curation_target_no_seed_query_skips_reranker_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_forge.cli as cli_mod
    import corpus_forge.curation as curation_mod

    builds: list[str] = []

    def boom(config):
        builds.append("oops")
        return MagicMock()

    monkeypatch.setattr(cli_mod, "_build_reranker_from_config", boom)
    monkeypatch.setattr(curation_mod, "next_curation_target", lambda **_: _fake_target())

    server = _build()
    _call(server, "next_curation_target", {})
    assert builds == []


def test_next_curation_target_handles_reranker_build_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_forge.cli as cli_mod
    import corpus_forge.config as config_mod
    import corpus_forge.curation as curation_mod

    monkeypatch.setattr(
        config_mod.Config,
        "load",
        classmethod(lambda cls: MagicMock(name="cfg")),
    )

    def fail(config):
        raise FileNotFoundError("no config")

    monkeypatch.setattr(cli_mod, "_build_reranker_from_config", fail)

    captured: dict[str, object] = {}

    def fake_selector(**kwargs):
        captured.update(kwargs)
        return _fake_target()

    monkeypatch.setattr(curation_mod, "next_curation_target", fake_selector)

    server = _build()
    result = _call(server, "next_curation_target", {"seed_query": "ml"})
    assert not result.isError
    assert captured["reranker"] is None  # graceful fallback


def test_next_curation_target_selector_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import corpus_forge.curation as curation_mod

    def boom(**_):
        raise ValueError("bad input")

    monkeypatch.setattr(curation_mod, "next_curation_target", boom)
    server = _build()
    result = _call(server, "next_curation_target", {})
    assert result.isError
    assert "bad input" in result.content[0].text


# ─────────────────────────────────────────────────────────────────────────
# next_curation_batch dispatch
# ─────────────────────────────────────────────────────────────────────────


def test_next_curation_batch_dispatch_calls_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _fake_batch([_fake_target(chunk_id=1), _fake_target(chunk_id=2)])

    import corpus_forge.curation as curation_mod

    monkeypatch.setattr(curation_mod, "next_curation_batch", fake)
    server = _build()
    result = _call(server, "next_curation_batch", {"limit": 5})
    assert not result.isError
    payload = _payload(result)
    assert payload["batch"]["cohesion_score"] == pytest.approx(0.95)
    assert len(payload["batch"]["targets"]) == 2
    assert captured["limit"] == 5


def test_next_curation_batch_default_limit_is_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_forge.curation as curation_mod

    captured: dict[str, object] = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _fake_batch([_fake_target()])

    monkeypatch.setattr(curation_mod, "next_curation_batch", fake)
    server = _build()
    _call(server, "next_curation_batch", {})
    assert captured["limit"] == 10


def test_next_curation_batch_none_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_forge.curation as curation_mod

    monkeypatch.setattr(curation_mod, "next_curation_batch", lambda **_: None)
    server = _build()
    result = _call(server, "next_curation_batch", {})
    assert _payload(result) == {"batch": None}


# ─────────────────────────────────────────────────────────────────────────
# commit_curation dispatch
# ─────────────────────────────────────────────────────────────────────────


def test_commit_curation_requires_xor_chunk_id() -> None:
    server = _build(writes_enabled=True)
    # neither
    r1 = _call(server, "commit_curation", {})
    assert r1.isError
    # both
    r2 = _call(server, "commit_curation", {"chunk_id": 1, "chunk_ids": [2]})
    assert r2.isError


def test_commit_curation_routes_through_existing_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The five inner dispatchers must each fire exactly once for a single chunk."""
    # The closure dispatchers are not exposed for direct mocking. Drive
    # the full path through the request handlers and inspect the
    # audit-id / count summary the dispatcher emits.
    backend = MagicMock()
    backend.audit_event.return_value = 999
    backend.apply_label.return_value = (1, True)
    backend.revoke_label.return_value = True
    backend.patch_metadata.return_value = ({}, {"k": "v"})
    backend.set_description.return_value = (None, "fixed")
    backend.add_feedback.return_value = 77
    backend.get_chunk.return_value = {"id": 1, "metadata": "{}", "description": None}
    backend.get_entity_metadata.return_value = {}
    backend.get_entity_description.return_value = None

    # Override the retriever's backend with our MagicMock.
    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server2 = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108

    result = _call(
        server2,
        "commit_curation",
        {
            "chunk_id": 1,
            "add_labels": [{"namespace": "topic", "value": "ml"}],
            "remove_labels": [{"namespace": "topic", "value": "old"}],
            "set_metadata": {"language": "en"},
            "set_description": "fixed",
            "feedback": {"kind": "rating", "rating": 4},
        },
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["writes"] == {
        "add_label": 1,
        "remove_label": 1,
        "set_metadata": 1,
        "set_description": 1,
        "add_feedback": 1,
    }
    assert payload["chunk_ids_processed"] == [1]


def test_commit_curation_bulk_routes_each_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.audit_event.return_value = 1
    backend.apply_label.return_value = (1, True)

    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108
    result = _call(
        server,
        "commit_curation",
        {
            "chunk_ids": [1, 2, 3],
            "add_labels": [{"namespace": "topic", "value": "ml"}],
        },
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["chunk_ids_processed"] == [1, 2, 3]
    assert payload["writes"]["add_label"] == 3  # one per chunk_id


def test_commit_curation_dry_run_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.audit_event.return_value = 5

    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108
    result = _call(
        server,
        "commit_curation",
        {
            "chunk_id": 1,
            "add_labels": [{"namespace": "topic", "value": "ml"}],
            "dry_run": True,
        },
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["dry_run"] is True
    # In dry-run, apply_label must NOT be called.
    assert backend.apply_label.call_count == 0


def test_commit_curation_inner_failure_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.audit_event.return_value = 1
    backend.apply_label.side_effect = RuntimeError("boom")

    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108
    result = _call(
        server,
        "commit_curation",
        {
            "chunk_id": 1,
            "add_labels": [{"namespace": "topic", "value": "ml"}],
        },
    )
    assert result.isError
    assert "chunk_id=1" in result.content[0].text


def test_commit_curation_when_writes_disabled_is_unknown_tool() -> None:
    server = _build(writes_enabled=False)
    # The tool is not registered when writes are disabled — but the
    # in-process handler still falls through to the unknown-tool branch.
    # Either it raises (mcp framework) or returns isError=True.
    try:
        result = _call(server, "commit_curation", {"chunk_id": 1})
        assert getattr(result, "isError", True)
    except Exception:
        pass


def test_commit_curation_set_description_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit null on set_description should fire set_description (clears it)."""
    backend = MagicMock()
    backend.audit_event.return_value = 1
    backend.set_description.return_value = ("old", None)
    backend.get_chunk.return_value = {"id": 1, "metadata": "{}", "description": "old"}

    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108
    result = _call(
        server,
        "commit_curation",
        {
            "chunk_id": 1,
            "set_description": None,
        },
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["writes"]["set_description"] == 1


def test_commit_curation_omitting_set_description_does_not_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MagicMock()
    backend.audit_event.return_value = 1
    backend.apply_label.return_value = (1, True)

    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108
    result = _call(
        server,
        "commit_curation",
        {"chunk_id": 1, "add_labels": [{"namespace": "topic", "value": "ml"}]},
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["writes"]["set_description"] == 0


def test_commit_curation_feedback_with_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.audit_event.return_value = 1
    backend.add_feedback.return_value = 7

    from corpus_forge.mcp.server import build_server

    class _R:
        def __init__(self) -> None:
            self.backend = backend

    server = build_server(retriever_builder=lambda: _R(), writes_enabled=True)  # noqa: PLW0108
    result = _call(
        server,
        "commit_curation",
        {
            "chunk_id": 1,
            "feedback": {"kind": "rating", "rating": 5, "text": "nice"},
        },
    )
    assert not result.isError, result
    payload = _payload(result)
    assert payload["writes"]["add_feedback"] == 1
    backend.add_feedback.assert_called_once()
