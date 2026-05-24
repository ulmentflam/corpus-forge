"""G-05 — End-to-end integration smoke: append → register → render → export → load.

Three integration tests that pin the full Phase-G round-trip end-to-end using
an in-process SQLite backend (no Docker required):

1. ``test_full_round_trip_append_register_render_export_load_via_datasets``
   — Full round-trip:
     a. Build an in-process MCP server (writes_enabled=True) over SQLite.
     b. Append a 4-message conversation via ``append_conversation`` MCP tool.
     c. Register a custom Jinja template via ``register_template`` MCP tool.
     d. Render the conversation via ``render_conversation`` MCP tool using the
        custom template.
     e. Export the dataset to JSONL via ``corpus_forge.export.export_chat``.
     f. Load the JSONL via ``datasets.load_dataset`` (skipped when not installed).
     g. Assert each row has the templated text the render call produced.

2. ``test_append_then_render_with_builtin``
   — Append 3 messages, render with ``template="chatml"``.  Response text
     must contain the ChatML ``<|im_start|>`` sentinel.

3. ``test_register_then_export_uses_custom_template``
   — Register ``{"name": "tiny", "jinja": "{{ messages|length }}", ...}`` via
     MCP. Export with ``template="tiny"``.  Each JSONL row's ``text`` field
     must equal the string of the message count.

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.export import export_chat
from corpus_forge.mcp.server import build_server
from corpus_forge.retrieval.types import Hit, SearchOptions

if TYPE_CHECKING:
    from mcp.server import Server

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Backend + server helpers (SQLite in-memory — no Docker)
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


class _LexicalRetriever:
    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: SearchOptions) -> list[Hit]:
        return self.backend.search_lexical(query, k=options.k)


def _make_server(backend: SQLiteBackend) -> Server[object]:
    retriever = _LexicalRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=True)


# ---------------------------------------------------------------------------
# MCP call helper (mirrors test_render_conversation_mcp.py pattern)
# ---------------------------------------------------------------------------


def _call_tool(server: Server[object], name: str, arguments: dict) -> dict:
    """Invoke a named MCP tool in-process; returns the structured result dict."""

    async def _run() -> dict:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers.get(CallToolRequest)
        assert handler is not None, "No CallToolRequest handler on server"
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        root = wrapper.root
        if getattr(root, "isError", False):
            text = "".join(getattr(b, "text", "") for b in getattr(root, "content", []))
            raise AssertionError(f"MCP tool {name!r} returned isError=True: {text}")
        structured = getattr(root, "structuredContent", None)
        if structured is not None:
            return dict(structured)
        text_blocks = [getattr(b, "text", "") for b in getattr(root, "content", [])]
        return json.loads("".join(text_blocks))

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Dataset creation helper
# ---------------------------------------------------------------------------


def _create_dataset(backend: SQLiteBackend, name: str, kind: str = "chat") -> int:
    """Insert a dataset row and return its id."""
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (name, kind, "G-05 e2e test dataset"),
        ).fetchone()[0]
        conn.commit()
    return ds_id


# ---------------------------------------------------------------------------
# JSONL reader
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# ---------------------------------------------------------------------------
# Test 1 — Full round-trip
# ---------------------------------------------------------------------------


def test_full_round_trip_append_register_render_export_load_via_datasets(
    tmp_path: Path,
) -> None:
    """Full G-05 round-trip: append → register → render → export → (optional) datasets load.

    Steps:
    1. Build in-process MCP server (writes_enabled=True) over an SQLite backend.
    2. Append a 4-message conversation via append_conversation MCP tool.
    3. Register a custom Jinja template via register_template MCP tool.
    4. Render the conversation via render_conversation MCP tool using the custom template.
    5. Export the dataset to JSONL via the export.export_chat() function.
    6. Load the JSONL via `datasets.load_dataset` (skip if datasets not installed).
    7. Assert: each row has the templated text the render call produced.
    """
    # ── Step 1: Build backend + server ──────────────────────────────────────
    backend = _make_backend()
    server = _make_server(backend)

    # ── Step 2: Append a 4-message conversation via MCP ─────────────────────
    ds_name = "g05-e2e-round-trip"
    _create_dataset(backend, ds_name)

    messages_4 = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
        {"role": "user", "content": "And what is the capital of Germany?"},
        {"role": "assistant", "content": "The capital of Germany is Berlin."},
    ]
    append_result = _call_tool(
        server,
        "append_conversation",
        {
            "dataset": ds_name,
            "title": "G-05 capitals conversation",
            "messages": messages_4,
        },
    )
    conv_id = append_result.get("conversation_id")
    assert isinstance(conv_id, int), (
        f"append_conversation must return int conv_id; got {append_result}"
    )
    assert append_result.get("message_count") == 4, (
        f"Expected message_count=4; got {append_result.get('message_count')}"
    )

    # ── Step 3: Register a custom Jinja template via MCP ────────────────────
    custom_jinja = "ROLES:{% for m in messages %}[{{ m.role }}]{% endfor %}"
    reg_result = _call_tool(
        server,
        "register_template",
        {
            "name": "g05-roles-tmpl",
            "jinja": custom_jinja,
            "description": "G-05 e2e roles template",
        },
    )
    template_id = reg_result.get("template_id")
    assert isinstance(template_id, int), (
        f"register_template must return int template_id; got {reg_result}"
    )
    assert isinstance(reg_result.get("audit_id"), int), (
        f"register_template must return int audit_id; got {reg_result}"
    )

    # ── Step 4: Render the conversation via MCP using the custom template ────
    render_result = _call_tool(
        server,
        "render_conversation",
        {
            "conversation_id": conv_id,
            "template": "g05-roles-tmpl",
        },
    )
    rendered_text = render_result.get("text")
    assert rendered_text is not None, "render_conversation must return a 'text' field"
    assert rendered_text.startswith("ROLES:"), (
        f"Expected rendered text to start with 'ROLES:'; got {rendered_text!r}"
    )
    assert render_result.get("message_count") == 4, (
        f"Expected message_count=4 in render result; got {render_result.get('message_count')}"
    )
    assert render_result.get("truncated") is False, (
        "Expected truncated=False for 4-message conversation"
    )
    # The 4 roles should appear in the output
    assert "[user]" in rendered_text, f"Expected [user] in rendered text; got {rendered_text!r}"
    assert "[assistant]" in rendered_text, (
        f"Expected [assistant] in rendered text; got {rendered_text!r}"
    )

    # ── Step 5: Export the dataset to JSONL ──────────────────────────────────
    out_path = tmp_path / "g05_round_trip.jsonl"
    export_chat(
        dataset=ds_name,
        template="g05-roles-tmpl",
        out_path=out_path,
        format="jsonl",
        backend=backend,
    )
    assert out_path.exists(), "export_chat must create the JSONL output file"

    rows = _read_jsonl(out_path)
    assert len(rows) == 1, f"Expected 1 row (1 conversation); got {len(rows)}"
    row = rows[0]

    # ── Step 6 + 7: Assert row content matches the rendered text ─────────────
    assert row["conversation_id"] == conv_id, (
        f"Row conversation_id={row['conversation_id']} does not match conv_id={conv_id}"
    )
    assert row["text"] == rendered_text, (
        f"JSONL row 'text' must equal the MCP render result.\n"
        f"  export text   : {row['text']!r}\n"
        f"  MCP render    : {rendered_text!r}"
    )
    assert row["message_count"] == 4, (
        f"Expected message_count=4 in JSONL row; got {row['message_count']}"
    )
    assert row["template"] == "g05-roles-tmpl", (
        f"Expected template='g05-roles-tmpl' in JSONL row; got {row['template']!r}"
    )

    # ── Optional: load via datasets ──────────────────────────────────────────
    try:
        import datasets as _ds  # type: ignore[import]
    except ImportError:
        pytest.skip("datasets library not installed — skipping HF load stage")

    hf_dataset = _ds.load_dataset("json", data_files=str(out_path), split="train")
    assert len(hf_dataset) == 1, f"datasets.load_dataset must load 1 row; got {len(hf_dataset)}"
    loaded_row = hf_dataset[0]
    assert loaded_row["text"] == rendered_text, (
        f"datasets-loaded row 'text' must equal the rendered text.\n"
        f"  loaded: {loaded_row['text']!r}\n"
        f"  expect: {rendered_text!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Append then render with builtin template
# ---------------------------------------------------------------------------


def test_append_then_render_with_builtin(tmp_path: Path) -> None:
    """Append 3 messages, render with template='chatml'; text contains <|im_start|>."""
    backend = _make_backend()
    server = _make_server(backend)

    ds_name = "g05-builtin-render"
    _create_dataset(backend, ds_name)

    messages_3 = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there, how can I help?"},
    ]
    append_result = _call_tool(
        server,
        "append_conversation",
        {
            "dataset": ds_name,
            "title": "G-05 chatml smoke conversation",
            "messages": messages_3,
        },
    )
    conv_id = append_result.get("conversation_id")
    assert isinstance(conv_id, int), f"Expected int conv_id; got {append_result}"
    assert append_result.get("message_count") == 3

    render_result = _call_tool(
        server,
        "render_conversation",
        {
            "conversation_id": conv_id,
            "template": "chatml",
        },
    )

    rendered_text = render_result.get("text", "")
    # ChatML builtin must emit the sentinel token
    assert "<|im_start|>" in rendered_text, (
        f"Expected ChatML <|im_start|> sentinel in rendered text; got {rendered_text!r}"
    )
    assert render_result.get("message_count") == 3, (
        f"Expected message_count=3; got {render_result.get('message_count')}"
    )
    assert render_result.get("conversation_id") == conv_id


# ---------------------------------------------------------------------------
# Test 3 — Register custom template, export, verify JSONL text field
# ---------------------------------------------------------------------------


def test_register_then_export_uses_custom_template(tmp_path: Path) -> None:
    """Register 'tiny' template via MCP; export with it; each row's text equals str(msg_count)."""
    backend = _make_backend()
    server = _make_server(backend)

    ds_name = "g05-custom-tmpl-export"
    _create_dataset(backend, ds_name)

    # Register the template via MCP before seeding conversations
    reg_result = _call_tool(
        server,
        "register_template",
        {
            "name": "tiny",
            "jinja": "{{ messages|length }}",
            "description": "G-05 message-count template",
        },
    )
    assert isinstance(reg_result.get("template_id"), int), (
        f"Expected int template_id; got {reg_result}"
    )

    # Seed 2 conversations: one with 2 messages, one with 4 messages
    messages_2 = [
        {"role": "user", "content": "Short conv message 1"},
        {"role": "assistant", "content": "Short conv reply 1"},
    ]
    messages_4 = [
        {"role": "user", "content": "Longer conv message 1"},
        {"role": "assistant", "content": "Longer conv reply 1"},
        {"role": "user", "content": "Longer conv message 2"},
        {"role": "assistant", "content": "Longer conv reply 2"},
    ]

    res2 = _call_tool(
        server,
        "append_conversation",
        {"dataset": ds_name, "title": "two-msg-conv", "messages": messages_2},
    )
    res4 = _call_tool(
        server,
        "append_conversation",
        {"dataset": ds_name, "title": "four-msg-conv", "messages": messages_4},
    )
    assert res2.get("message_count") == 2
    assert res4.get("message_count") == 4

    # Export using the custom "tiny" template
    out_path = tmp_path / "g05_custom_tmpl.jsonl"
    export_chat(
        dataset=ds_name,
        template="tiny",
        out_path=out_path,
        format="jsonl",
        backend=backend,
    )
    assert out_path.exists(), "export_chat must produce an output file"

    rows = _read_jsonl(out_path)
    assert len(rows) == 2, f"Expected 2 rows (2 conversations); got {len(rows)}"

    # Sort by message_count to make assertions deterministic
    rows_sorted = sorted(rows, key=lambda r: r["message_count"])

    assert rows_sorted[0]["message_count"] == 2, (
        f"First row should be 2-message conv; got {rows_sorted[0]['message_count']}"
    )
    assert rows_sorted[1]["message_count"] == 4, (
        f"Second row should be 4-message conv; got {rows_sorted[1]['message_count']}"
    )

    # The 'tiny' template renders messages|length — so text == str(message_count)
    assert rows_sorted[0]["text"] == "2", (
        f"Expected text='2' for 2-message conv (tiny template); got {rows_sorted[0]['text']!r}"
    )
    assert rows_sorted[1]["text"] == "4", (
        f"Expected text='4' for 4-message conv (tiny template); got {rows_sorted[1]['text']!r}"
    )
