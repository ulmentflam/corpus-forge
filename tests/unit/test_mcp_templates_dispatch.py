"""G-03 RED — MCP template tools dispatch unit tests.

Tests the ``corpus_forge.mcp.templates`` module (does not exist yet).

Each dispatch function is called in-process with:
  - a real ``SQLiteBackend(":memory:")`` (migrated),
  - a small ``_MCPContext`` dataclass carrying ``host``, ``client``,
    ``session_id``.

Pinned dispatch signatures (Coder must match exactly):

    render_conversation(
        backend, ctx,
        conversation_id: int,
        template: str = "chatml",
        *,
        model_id: str | None = None,
        custom_jinja: str | None = None,
        include_tool_calls: bool = True,
    ) -> dict
    # {
    #   "conversation_id": int,
    #   "template": str,
    #   "model_id": str | None,
    #   "text": str,
    #   "message_count": int,
    #   "truncated": bool,
    # }

    list_chat_templates(
        backend, ctx,
    ) -> dict
    # {"templates": [{name, source, model_id, description}, ...]}

    register_template(
        backend, ctx,
        name: str,
        jinja: str,
        *,
        description: str | None = None,
        dry_run: bool = False,
    ) -> dict
    # {"template_id": int, "audit_id": int}

    get_chunk_with_template(
        backend, ctx,
        chunk_id: int,
        template: str,
    ) -> dict
    # Extends the standard get_chunk result with "templated_text": str | None
    # (None for non-message chunks)

``_MCPContext`` carries:
    host: str
    client: str | None
    session_id: str | None

Resolution-order note for the Coder:
    custom_jinja > model_id (HF fetch) > template name (DB lookup → builtin fallback)

For HF-sourced rows in chat_templates:
    source='huggingface', jinja IS NULL, model_id IS NOT NULL

Truncation threshold: implementation-defined, but MUST set truncated=True when
message_count is *very* large (> 1000 is the test boundary). The coder may
choose a lower threshold; flag the chosen value in code-status.md.

Run command:
    .venv/bin/python -m pytest tests/unit/test_mcp_templates_dispatch.py -v

pytestmark: pytest.mark.unit
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Import target — all tests fail here until templates.py exists
# ---------------------------------------------------------------------------
from corpus_forge.mcp import templates as tmpl

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# _MCPContext — minimal identity object (mirrors writes.WriteContext shape)
# ---------------------------------------------------------------------------


@dataclass
class _MCPContext:
    """Minimal context object carrying MCP caller identity."""

    host: str
    client: str | None
    session_id: str | None


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend for each test."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


@pytest.fixture
def ctx() -> _MCPContext:
    return _MCPContext(host="test-host", client="test-client", session_id="sess-g03")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _seed_conversation(
    backend: SQLiteBackend,
    *,
    n_messages: int = 3,
    dataset_name: str = "g03-dataset",
) -> dict[str, int]:
    """Seed one dataset + conversation with *n_messages* messages.

    Returns {dataset_id, conversation_id}.
    """
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (dataset_name, "chat", "G-03 test dataset"),
        ).fetchone()[0]
        conn.commit()

    roles = ["user", "assistant", "user"]
    messages = [
        {"role": roles[i % len(roles)], "content": f"Message {i}"} for i in range(n_messages)
    ]
    conv_id, _ = backend.append_conversation(
        dataset_id=ds_id,
        title="G-03 test conversation",
        started_at=None,
        messages=messages,
    )
    return {"dataset_id": ds_id, "conversation_id": conv_id}


def _seed_document_chunk(backend: SQLiteBackend) -> dict[str, int]:
    """Seed a dataset + document + chunk that is NOT a message chunk.

    Returns {dataset_id, document_id, chunk_id}.
    """
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("g03-doc-ds", "text", "document chunk dataset"),
        ).fetchone()[0]
        doc_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (ds_id, "test://doc/g03.md", _content_hash("doc text"), "Doc G03", "doc text", "{}"),
        ).fetchone()[0]
        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (doc_id, 0, "doc text", _content_hash("doc text"), "{}"),
        ).fetchone()[0]
        conn.commit()
    return {"dataset_id": ds_id, "document_id": doc_id, "chunk_id": chunk_id}


def _get_audit_rows(backend: SQLiteBackend) -> list[Any]:
    with backend._get_connection() as conn:
        return conn.execute("SELECT * FROM mcp_audit ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# render_conversation — happy path
# ---------------------------------------------------------------------------


class TestRenderConversationBuiltinTemplate:
    def test_returns_expected_keys(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert set(result.keys()) >= {
            "conversation_id",
            "template",
            "model_id",
            "text",
            "message_count",
            "truncated",
        }

    def test_text_contains_chatml_markers(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert "<|im_start|>" in result["text"]

    def test_message_count_matches_seeded(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["message_count"] == 3

    def test_conversation_id_echoed(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["conversation_id"] == seeded["conversation_id"]

    def test_template_echoed(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["template"] == "chatml"

    def test_model_id_none_when_not_provided(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["model_id"] is None

    def test_not_truncated_for_small_count(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["truncated"] is False


# ---------------------------------------------------------------------------
# render_conversation — custom_jinja
# ---------------------------------------------------------------------------


class TestRenderConversationCustomJinja:
    def test_custom_jinja_renders_message_count(self, backend, ctx):
        """custom_jinja takes priority; template name is ignored."""
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(
            backend,
            ctx,
            seeded["conversation_id"],
            "chatml",
            custom_jinja="{{ messages | length }}",
        )
        assert result["text"] == "3"

    def test_custom_jinja_overrides_template_name(self, backend, ctx):
        """Even an invalid template name is accepted when custom_jinja is given."""
        seeded = _seed_conversation(backend, n_messages=2)
        result = tmpl.render_conversation(
            backend,
            ctx,
            seeded["conversation_id"],
            "nonexistent-template",
            custom_jinja="{{ messages | length }}",
        )
        assert result["text"] == "2"

    def test_custom_jinja_accesses_role_and_content(self, backend, ctx):
        """Jinja template can iterate messages and access role/content."""
        seeded = _seed_conversation(backend, n_messages=1)
        result = tmpl.render_conversation(
            backend,
            ctx,
            seeded["conversation_id"],
            "chatml",
            custom_jinja="{% for m in messages %}{{ m.role }}:{{ m.content }}{% endfor %}",
        )
        assert "user:" in result["text"]
        assert "Message 0" in result["text"]


# ---------------------------------------------------------------------------
# render_conversation — model_id / HF path
# ---------------------------------------------------------------------------


class TestRenderConversationModelId:
    def test_model_id_calls_hf_template(self, backend, ctx):
        """When model_id is given, hf_template() is called with that model_id."""
        seeded = _seed_conversation(backend, n_messages=2)
        stub_jinja = "{% for m in messages %}[{{ m.role }}]{{ m.content }}{% endfor %}"

        with patch("corpus_forge.templates.hf.hf_template", return_value=stub_jinja) as mock_hf:
            result = tmpl.render_conversation(
                backend,
                ctx,
                seeded["conversation_id"],
                "chatml",
                model_id="meta-llama/Llama-3.1-8B-Instruct",
            )
            mock_hf.assert_called_once_with("meta-llama/Llama-3.1-8B-Instruct")

        assert "[user]Message 0" in result["text"]
        assert result["model_id"] == "meta-llama/Llama-3.1-8B-Instruct"

    def test_model_id_takes_priority_over_template_name(self, backend, ctx):
        """model_id wins over template name lookup (priority: custom_jinja > model_id > name)."""
        seeded = _seed_conversation(backend, n_messages=1)
        stub_jinja = "HF:{% for m in messages %}{{ m.content }}{% endfor %}"

        with patch("corpus_forge.templates.hf.hf_template", return_value=stub_jinja):
            result = tmpl.render_conversation(
                backend,
                ctx,
                seeded["conversation_id"],
                "chatml",  # this should be ignored since model_id is given
                model_id="meta-llama/Llama-3.1-8B-Instruct",
            )
        assert result["text"].startswith("HF:")
        # Should NOT contain the chatml marker from the builtin
        assert "<|im_start|>" not in result["text"]


# ---------------------------------------------------------------------------
# render_conversation — HuggingFace-registered template in chat_templates table
# ---------------------------------------------------------------------------


class TestRenderConversationHFRegisteredTemplate:
    def test_hf_source_row_dispatches_to_hf_template(self, backend, ctx):
        """A chat_templates row with source='huggingface' triggers hf_template(model_id)."""
        # Insert a 'huggingface' source row directly into the table.
        backend.register_chat_template(
            name="llama3-hub",
            source="huggingface",
            jinja=None,
            model_id="meta-llama/Llama-3.1-8B-Instruct",
            description="llama3 from hub",
            host="test-host",
        )

        seeded = _seed_conversation(backend, n_messages=2)
        stub_jinja = "HF-REGISTERED:{% for m in messages %}{{ m.role }}{% endfor %}"

        with patch("corpus_forge.templates.hf.hf_template", return_value=stub_jinja) as mock_hf:
            result = tmpl.render_conversation(
                backend,
                ctx,
                seeded["conversation_id"],
                "llama3-hub",
            )
            mock_hf.assert_called_once_with("meta-llama/Llama-3.1-8B-Instruct")

        assert result["text"].startswith("HF-REGISTERED:")

    def test_custom_source_row_uses_stored_jinja(self, backend, ctx):
        """A chat_templates row with source='custom' renders using the stored jinja."""
        custom_jinja = "CUSTOM:{% for m in messages %}{{ m.content }}|{% endfor %}"
        backend.register_chat_template(
            name="my-custom-tmpl",
            source="custom",
            jinja=custom_jinja,
            model_id=None,
            description="my custom template",
            host="test-host",
        )

        seeded = _seed_conversation(backend, n_messages=2)
        result = tmpl.render_conversation(
            backend,
            ctx,
            seeded["conversation_id"],
            "my-custom-tmpl",
        )
        assert result["text"].startswith("CUSTOM:")
        assert "Message 0|" in result["text"]


# ---------------------------------------------------------------------------
# render_conversation — truncation flag
# ---------------------------------------------------------------------------


class TestRenderConversationTruncation:
    def test_not_truncated_for_normal_count(self, backend, ctx):
        seeded = _seed_conversation(backend, n_messages=3)
        result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["truncated"] is False

    def test_truncated_flag_set_when_many_messages(self, backend, ctx):
        """When message_count exceeds the threshold (>1000), truncated must be True.

        NOTE for the Coder: the threshold is test-defined as > 1000.  You may
        choose any threshold <= 1000; document it in code-status.md.
        We inject a very large message_count by directly patching the backend
        fetch to return a synthetic conversation with a huge message list.
        """
        seeded = _seed_conversation(backend, n_messages=3)

        # We monkeypatch the messages list returned from the backend call inside
        # render_conversation.  The coder must call some method to fetch messages;
        # we patch the backend's count or fetch to simulate overflow.
        # Strategy: directly patch a very large synthetic message list at the
        # render_conversation boundary.  The coder is free to fetch via any
        # internal method; the patch target is the backend.count_messages method.
        with patch.object(backend, "count_messages", return_value=1001):
            result = tmpl.render_conversation(backend, ctx, seeded["conversation_id"], "chatml")
        assert result["truncated"] is True


# ---------------------------------------------------------------------------
# render_conversation — invalid conversation_id
# ---------------------------------------------------------------------------


class TestRenderConversationInvalidId:
    def test_nonexistent_conversation_id_raises(self, backend, ctx):
        """A non-existent conversation_id must raise an error (ValueError / KeyError / McpError)."""
        with pytest.raises((ValueError, KeyError, LookupError, RuntimeError)):
            tmpl.render_conversation(backend, ctx, 99999, "chatml")


# ---------------------------------------------------------------------------
# list_chat_templates
# ---------------------------------------------------------------------------


class TestListChatTemplates:
    def test_empty_corpus_returns_empty_list(self, backend, ctx):
        """Fresh DB with no registered templates returns {templates: []}."""
        result = tmpl.list_chat_templates(backend, ctx)
        assert "templates" in result
        assert result["templates"] == []

    def test_returns_registered_templates(self, backend, ctx):
        """After registering 2 custom templates, list returns both."""
        backend.register_chat_template(
            name="tmpl-a", source="custom", jinja="{{ messages }}", host="host"
        )
        backend.register_chat_template(
            name="tmpl-b", source="custom", jinja="{{ messages | length }}", host="host"
        )
        result = tmpl.list_chat_templates(backend, ctx)
        names = [t["name"] for t in result["templates"]]
        assert "tmpl-a" in names
        assert "tmpl-b" in names

    def test_each_entry_has_required_fields(self, backend, ctx):
        """Each template entry has name, source, model_id, description."""
        backend.register_chat_template(
            name="tmpl-fields",
            source="huggingface",
            jinja=None,
            model_id="org/model",
            description="A test template",
            host="host",
        )
        result = tmpl.list_chat_templates(backend, ctx)
        entry = next(t for t in result["templates"] if t["name"] == "tmpl-fields")
        assert "name" in entry
        assert "source" in entry
        assert "model_id" in entry
        assert "description" in entry

    def test_builtins_not_auto_inserted_to_table(self, backend, ctx):
        """Built-in templates are reachable by name but NOT auto-inserted into the table."""
        result = tmpl.list_chat_templates(backend, ctx)
        names = [t["name"] for t in result["templates"]]
        # Builtins are NOT in the DB table by default
        assert "chatml" not in names

    def test_list_is_read_only_no_audit(self, backend, ctx):
        """list_chat_templates is a read tool — must NOT emit an audit row."""
        tmpl.list_chat_templates(backend, ctx)
        rows = _get_audit_rows(backend)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# register_template
# ---------------------------------------------------------------------------


class TestRegisterTemplate:
    def test_happy_path_returns_template_id_and_audit_id(self, backend, ctx):
        """register_template returns {template_id: int, audit_id: int}."""
        result = tmpl.register_template(backend, ctx, "my-jinja", "{{ messages }}")
        assert isinstance(result["template_id"], int)
        assert isinstance(result["audit_id"], int)

    def test_template_persisted_to_db(self, backend, ctx):
        """After register_template, the template row exists in chat_templates."""
        tmpl.register_template(backend, ctx, "persist-test", "{{ messages | length }}")
        row = backend.get_chat_template_by_name("persist-test")
        assert row is not None
        assert row["source"] == "custom"
        assert row["jinja"] == "{{ messages | length }}"

    def test_creates_audit_row(self, backend, ctx):
        """register_template emits an audit row."""
        tmpl.register_template(backend, ctx, "audit-test", "{{ messages }}")
        rows = _get_audit_rows(backend)
        assert len(rows) == 1
        assert rows[0]["tool"] == "register_template"

    def test_dry_run_does_not_persist(self, backend, ctx):
        """dry_run=True: chat_templates table unchanged after call."""
        with backend._get_connection() as conn:
            count_before = conn.execute("SELECT COUNT(*) FROM chat_templates").fetchone()[0]

        tmpl.register_template(backend, ctx, "dry-run-tmpl", "{{ messages }}", dry_run=True)

        with backend._get_connection() as conn:
            count_after = conn.execute("SELECT COUNT(*) FROM chat_templates").fetchone()[0]

        assert count_after == count_before, "dry_run must not insert a chat_templates row"

    def test_dry_run_still_emits_audit(self, backend, ctx):
        """dry_run=True still emits an audit row with dry_run=True."""
        tmpl.register_template(backend, ctx, "dry-run-audit", "{{ messages }}", dry_run=True)
        rows = _get_audit_rows(backend)
        assert len(rows) == 1
        assert rows[0]["dry_run"] in (1, True)

    def test_duplicate_name_returns_existing_template_id(self, backend, ctx):
        """Registering the same name twice returns the same template_id."""
        result1 = tmpl.register_template(backend, ctx, "dup-name", "{{ messages }}")
        result2 = tmpl.register_template(backend, ctx, "dup-name", "{{ messages }}")
        assert result1["template_id"] == result2["template_id"]

    def test_optional_description_stored(self, backend, ctx):
        """description kwarg is persisted on the row."""
        tmpl.register_template(
            backend,
            ctx,
            "desc-tmpl",
            "{{ messages }}",
            description="A descriptive template",
        )
        row = backend.get_chat_template_by_name("desc-tmpl")
        assert row["description"] == "A descriptive template"


# ---------------------------------------------------------------------------
# get_chunk_with_template — message chunks
# ---------------------------------------------------------------------------


class TestGetChunkWithTemplateMessageChunk:
    def test_message_chunk_gains_templated_text(self, backend, ctx):
        """get_chunk on a message chunk with template='chatml' adds templated_text."""
        seeded = _seed_conversation(backend, n_messages=1)

        # Find the chunk that was created for this conversation's message.
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM chunks WHERE conversation_id = ? LIMIT 1",
                (seeded["conversation_id"],),
            ).fetchone()
        chunk_id = row["id"]

        result = tmpl.get_chunk_with_template(backend, ctx, chunk_id, "chatml")
        assert "templated_text" in result
        assert result["templated_text"] is not None
        assert "<|im_start|>" in result["templated_text"]

    def test_message_chunk_templated_text_not_empty(self, backend, ctx):
        """templated_text is a non-empty string for message chunks."""
        seeded = _seed_conversation(backend, n_messages=2)

        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM chunks WHERE conversation_id = ? LIMIT 1",
                (seeded["conversation_id"],),
            ).fetchone()
        chunk_id = row["id"]

        result = tmpl.get_chunk_with_template(backend, ctx, chunk_id, "chatml")
        assert isinstance(result["templated_text"], str)
        assert len(result["templated_text"]) > 0


# ---------------------------------------------------------------------------
# get_chunk_with_template — non-message (document) chunks
# ---------------------------------------------------------------------------


class TestGetChunkWithTemplateDocChunk:
    def test_document_chunk_has_no_templated_text(self, backend, ctx):
        """Document chunks do NOT get templated_text (or it is None)."""
        seeded = _seed_document_chunk(backend)
        result = tmpl.get_chunk_with_template(backend, ctx, seeded["chunk_id"], "chatml")
        # Either key absent OR key present with None value.
        templated = result.get("templated_text")
        assert templated is None, (
            f"Expected templated_text=None for document chunk, got {templated!r}"
        )

    def test_document_chunk_still_returns_chunk_fields(self, backend, ctx):
        """The standard chunk fields are present even when templated_text is None."""
        seeded = _seed_document_chunk(backend)
        result = tmpl.get_chunk_with_template(backend, ctx, seeded["chunk_id"], "chatml")
        assert "text" in result
        assert result["text"] == "doc text"
