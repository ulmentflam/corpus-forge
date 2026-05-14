"""G-03 — MCP template tools dispatch layer.

Four dispatch functions that sit between the MCP server and the templates +
backend helpers introduced in G-02.

Truncation threshold
--------------------
``render_conversation`` caps messages at 1000.  When ``count_messages`` returns
> 1000, ``truncated=True`` is set in the response and only the first 1000
messages are passed to the renderer.  The count is obtained via
``backend.count_messages`` FIRST so tests can patch that boundary.

Resolution order for render_conversation
-----------------------------------------
1. ``custom_jinja`` — render the Jinja string directly (bypasses all DB/HF).
2. ``model_id`` — fetch the HF tokenizer template; DB lookup is skipped.
3. Template name — look up ``chat_templates`` table by name:
   - ``source='custom'`` rows: use the stored ``jinja``.
   - ``source='huggingface'`` rows: dispatch to ``hf_template(row["model_id"])``
     (``jinja`` column is NULL for HF rows).
   - Row absent or ``source='builtin'``: fall through to the bundled builtin.

``list_chat_templates`` is a pure read tool — no audit row is emitted, and
built-in templates are NOT auto-inserted into the table.

``register_template`` is a WRITE tool gated by ``writes_enabled`` in the
server layer.  ``dry_run=True`` skips the DB insert but still emits an audit
row (matching the F-03 convention).

``get_chunk_with_template`` adds ``templated_text: str | None`` to the
standard ``get_chunk`` result dict.  Document chunks (no ``message_id``) get
``templated_text=None``; message chunks get a single-message render.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Truncation threshold
# ---------------------------------------------------------------------------

_TRUNCATION_THRESHOLD: int = 1000
"""Conversations with more than this many messages are truncated before render."""


# ---------------------------------------------------------------------------
# render_conversation
# ---------------------------------------------------------------------------


def render_conversation(
    backend: Any,
    ctx: Any,  # noqa: ARG001 — accepted for API symmetry; not used (no audit for reads)
    conversation_id: int,
    template: str = "chatml",
    *,
    model_id: str | None = None,
    custom_jinja: str | None = None,
    include_tool_calls: bool = True,  # noqa: ARG001 — reserved for future tool-call filtering
) -> dict:
    """Render a conversation's messages under a chat template.

    Returns::

        {
            "conversation_id": int,
            "template": str,
            "model_id": str | None,
            "text": str,
            "message_count": int,
            "truncated": bool,
        }

    The ``model_id`` field in the response reflects the *resolved* model_id
    (set when ``model_id`` kwarg is given or when a huggingface-source row is
    looked up); ``None`` otherwise.
    """
    from corpus_forge import templates as _tpl

    # 1. Verify the conversation exists.
    conv = backend.get_conversation(conversation_id)
    if conv is None:
        raise ValueError(f"conversation {conversation_id!r} not found")

    # 2. Determine message count (patchable boundary for truncation tests).
    message_count = backend.count_messages(conversation_id)
    truncated = message_count > _TRUNCATION_THRESHOLD

    # 3. Fetch messages (only up to threshold if truncated).
    messages_raw = backend.list_conversation_messages(conversation_id)
    if truncated:
        messages_raw = messages_raw[:_TRUNCATION_THRESHOLD]

    # Normalise to plain dicts with role/content keys.
    messages: list[dict[str, Any]] = [
        {"role": str(m["role"]), "content": str(m["content"])} for m in messages_raw
    ]

    # 4. Resolve template source.
    #    Priority: custom_jinja > model_id > DB name lookup > builtin name.
    resolved_model_id = model_id
    resolved_custom_jinja = custom_jinja

    if resolved_custom_jinja is None and resolved_model_id is None:
        # DB lookup by name.
        row = backend.get_chat_template_by_name(template)
        if row is not None:
            source = row["source"]
            if source == "huggingface":
                # HF rows store jinja=NULL; the model_id column holds the repo id.
                resolved_model_id = row["model_id"]
            elif source == "custom":
                # Stored Jinja string.
                resolved_custom_jinja = row["jinja"]
            # source='builtin' falls through to the bundled template lookup.

    # 5. Render.
    text = _tpl.render(
        template,
        messages,
        model_id=resolved_model_id,
        custom_jinja=resolved_custom_jinja,
    )

    return {
        "conversation_id": conversation_id,
        "template": template,
        "model_id": resolved_model_id,
        "text": text,
        "message_count": message_count,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# list_chat_templates  (read-only — no audit row)
# ---------------------------------------------------------------------------


def list_chat_templates(
    backend: Any,
    ctx: Any,  # noqa: ARG001 — accepted for API symmetry; not used (no audit)
) -> dict:
    """Return all registered chat templates.

    Returns ``{"templates": [{name, source, model_id, description}, ...]}``.
    Built-in templates are NOT auto-inserted into the table; a fresh DB returns
    ``{"templates": []}``.  No audit row is emitted (pure read tool).
    """
    rows = backend.list_chat_templates()
    templates = [
        {
            "name": r["name"],
            "source": r["source"],
            "model_id": r.get("model_id"),
            "description": r.get("description"),
        }
        for r in rows
    ]
    return {"templates": templates}


# ---------------------------------------------------------------------------
# register_template  (WRITE tool — gated by writes_enabled in server.py)
# ---------------------------------------------------------------------------


def register_template(
    backend: Any,
    ctx: Any,
    name: str,
    jinja: str,
    *,
    description: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Register a custom Jinja template in the chat_templates table.

    Returns ``{"template_id": int | None, "audit_id": int}``.
    When ``dry_run=True``, the row is NOT inserted but an audit row IS emitted
    (matching the F-03 convention for write tools).
    """
    before: dict | None = None
    after: dict = {"name": name, "source": "custom", "dry_run": dry_run}

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "register_template",
            "chat_template",
            0,
            before,
            after,
            True,
        )
        return {"template_id": None, "audit_id": audit_id}

    template_id, _created = backend.register_chat_template(
        name=name,
        source="custom",
        jinja=jinja,
        description=description,
        host=ctx.host,
    )
    after["template_id"] = template_id

    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "register_template",
        "chat_template",
        template_id,
        before,
        after,
        False,
    )
    return {"template_id": template_id, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# get_chunk_with_template
# ---------------------------------------------------------------------------


def get_chunk_with_template(
    backend: Any,
    ctx: Any,  # noqa: ARG001 — accepted for API symmetry; not used
    chunk_id: int,
    template: str,
) -> dict:
    """Fetch a chunk and add ``templated_text`` to the result dict.

    - **Message chunks** (``message_id`` is set): renders the single message
      under *template* and sets ``templated_text`` to the rendered string.
    - **Document chunks** (``message_id`` is ``None`` / absent): sets
      ``templated_text=None`` (key is present, value is null).

    Returns the standard ``get_chunk`` dict extended with ``templated_text``.
    """
    from corpus_forge import templates as _tpl

    chunk = backend.get_chunk(chunk_id)
    if chunk is None:
        raise ValueError(f"chunk_id={chunk_id!r} not found")

    result: dict[str, Any] = dict(chunk)

    message_id = result.get("message_id")
    if message_id is not None:
        # Message chunk: render single message using the chunk's own role/content.
        role = result.get("role") or "user"
        content = result.get("text") or ""
        messages = [{"role": str(role), "content": str(content)}]
        result["templated_text"] = _tpl.render(template, messages)
    else:
        # Document chunk: no template rendering applicable.
        result["templated_text"] = None

    return result


__all__ = [
    "get_chunk_with_template",
    "list_chat_templates",
    "register_template",
    "render_conversation",
]
