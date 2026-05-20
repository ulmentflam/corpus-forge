"""Export PG/SQLite conversations as templated HuggingFace-format rows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from corpus_forge import templates as _tpl


def export_chat(
    dataset: str,
    template: str,
    out_path: Path,
    format: str = "jsonl",
    *,
    backend=None,
    model_id: str | None = None,
    custom_jinja: str | None = None,
    push: str | None = None,
) -> None:
    """Export a dataset's conversations as templated HF-compatible rows.

    Row schema:
        {
            "conversation_id": int,
            "title": str,
            "source_uri": str,
            "description": str | None,
            "template": str,
            "model_id": str | None,
            "text": <rendered templated string>,
            "message_count": int,
            "messages": [{"role": str, "content": str}, ...],
        }
    """
    if backend is None:
        backend = _build_default_backend()

    # 1. Find dataset_id by name.
    dataset_id = backend.find_dataset_id_by_name(dataset)
    if dataset_id is None:
        raise ValueError(f"dataset {dataset!r} not found")

    # 2. Fetch all conversations for the dataset.
    conversations = backend.list_conversations_for_dataset(dataset_id)

    # Resolve template name to (model_id, custom_jinja) once before the loop.
    resolved_model_id, resolved_custom_jinja = _tpl.resolve_template(
        template,
        backend=backend,
        model_id=model_id,
        custom_jinja=custom_jinja,
    )

    rows: list[dict] = []
    for conv in conversations:
        messages = backend.list_conversation_messages(conv["id"])
        if not messages:
            continue  # skip empty conversations
        text = _tpl.render(
            template,
            [{"role": m["role"], "content": m["content"]} for m in messages],
            model_id=resolved_model_id,
            custom_jinja=resolved_custom_jinja,
        )
        rows.append(
            {
                "conversation_id": conv["id"],
                "title": conv.get("title"),
                "source_uri": conv.get("source_uri"),
                "description": conv.get("description"),
                "template": template,
                "model_id": model_id,
                "text": text,
                "message_count": len(messages),
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            }
        )

    # 3. Write to out_path.
    if format == "jsonl":
        with out_path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif format == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(
                "parquet export requires pyarrow; install with `pip install 'corpus-forge[hf]'`"
            ) from exc
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, str(out_path))
    else:
        raise ValueError(f"unsupported format: {format!r}; expected 'jsonl' or 'parquet'")

    # 4. Optional Hub push.
    if push is not None:
        _push_to_hub(out_path, push)


def export_feedback_pairs(
    dataset: str,
    template: str,
    out_path: Path,
    format: str = "jsonl",
    *,
    backend=None,
    model_id: str | None = None,
    custom_jinja: str | None = None,
) -> None:
    """Export feedback events as templated training rows.

    One row is emitted per feedback_event whose session is linked to a
    conversation that belongs to *dataset*.  Events from unlinked sessions
    (conversation_id IS NULL) are silently skipped.

    Row schema::

        {
            "feedback_event_id": int,
            "feedback_session_id": int,
            "client": str,
            "session_id": str,
            "host": str,
            "prompt": <conversation-up-to-event-ts, templated>,
            "response": <write payload>,
            "after": <entity state after>,
            "kind": "audit" | "feedback",
            "ts": <ISO timestamp>,
        }

    Tie-break: when an event has *both* audit_id and feedback_id, kind is
    ``"feedback"`` (the user-facing judgment takes precedence over the audit
    diff).
    """
    if backend is None:
        backend = _build_default_backend()

    dataset_id = backend.find_dataset_id_by_name(dataset)
    if dataset_id is None:
        raise ValueError(f"dataset {dataset!r} not found")

    # Resolve template once before the loop.
    resolved_model_id, resolved_custom_jinja = _tpl.resolve_template(
        template,
        backend=backend,
        model_id=model_id,
        custom_jinja=custom_jinja,
    )

    events = backend.list_feedback_events_for_dataset(dataset_id)
    rows: list[dict] = []
    for event in events:
        conv_msgs = backend.get_conversation_messages_up_to_ts(
            event["conversation_id"], event.get("ts")
        )
        prompt = _tpl.render(
            template,
            [{"role": m["role"], "content": m["content"]} for m in conv_msgs],
            model_id=resolved_model_id,
            custom_jinja=resolved_custom_jinja,
        )

        response: dict = {}
        kind: str | None = None
        after = None

        # Tie-break: prefer feedback over audit when both are set.
        if event.get("feedback_id"):
            fb = backend.get_feedback(event["feedback_id"])
            if fb is not None:
                response = {
                    "kind": fb["kind"],
                    "rating": fb["rating"],
                    "text": fb["text"],
                }
                kind = "feedback"
                after = fb.get("text")

        if kind is None and event.get("audit_id"):
            audit = backend.get_audit_event(event["audit_id"])
            if audit is not None:
                response = {
                    "tool": audit["tool"],
                    "args": audit.get("before"),
                }
                kind = "audit"
                after = audit.get("after")

        rows.append(
            {
                "feedback_event_id": event["id"],
                "feedback_session_id": event["feedback_session_id"],
                "client": event["client"],
                "session_id": event["session_id"],
                "host": event["host"],
                "prompt": prompt,
                "response": response,
                "after": after,
                "kind": kind,
                "ts": event["ts"],
            }
        )

    if format == "jsonl":
        with out_path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    elif format == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(
                "parquet export requires pyarrow; install with `pip install 'corpus-forge[hf]'`"
            ) from exc
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, str(out_path))
    else:
        raise ValueError(f"unsupported format: {format!r}; expected 'jsonl' or 'parquet'")


def export_sdft(
    dataset: str,
    template: str,
    out_path: Path,
    format: str = "jsonl",
    *,
    backend=None,
    held_out_fraction: float = 0.0,
    include_sources: list[str] | None = None,
    custom_jinja: str | None = None,
    model_id: str | None = None,
    push: str | None = None,
) -> dict:
    """Export a dataset's SDFT demonstrations as HF-compatible rows.

    Row schema (exactly these keys):
        {
            "query": str,
            "student_messages": list,
            "teacher_messages": list,
            "target": str,
            "source": str,
            "dataset_id": int,
            "template": str,
        }

    When ``held_out_fraction > 0`` the rows are split deterministically
    (via ``sha256(content_hash) % 100``) into two files:
      - ``<out>.train.<ext>``
      - ``<out>.held_out.<ext>``

    Returns:
        {
            "row_count": int,
            "train_count": int,
            "held_out_count": int,
            "out_paths": list[str],
        }
    """
    if backend is None:
        backend = _build_default_backend()

    # 1. Find dataset_id by name.
    dataset_id = backend.find_dataset_id_by_name(dataset)
    if dataset_id is None:
        raise ValueError(f"dataset {dataset!r} not found")

    # Resolve template once before the loop (same path as export_chat).
    resolved_model_id, resolved_custom_jinja = _tpl.resolve_template(
        template,
        backend=backend,
        model_id=model_id,
        custom_jinja=custom_jinja,
    )

    # 2. Fetch rows from the backend.
    raw_rows = backend.list_sdft_demonstrations(dataset_id, include_sources=include_sources)

    # 3. Build output rows.
    rows: list[dict] = []
    for raw in raw_rows:
        # student_messages / teacher_messages may arrive as lists (Postgres JSONB)
        # or as already-deserialized lists from the SQLite helper.
        student_msgs = raw["student_messages"]
        teacher_msgs = raw["teacher_messages"]
        if isinstance(student_msgs, str):
            student_msgs = json.loads(student_msgs)
        if isinstance(teacher_msgs, str):
            teacher_msgs = json.loads(teacher_msgs)

        # Render template for completeness (result not stored in row schema,
        # but resolve_template must not raise for known template names).
        _tpl.render(
            template,
            student_msgs,
            model_id=resolved_model_id,
            custom_jinja=resolved_custom_jinja,
        )

        rows.append(
            {
                "query": raw["query"],
                "student_messages": student_msgs,
                "teacher_messages": teacher_msgs,
                "target": raw["target"],
                "source": raw["source"],
                "dataset_id": raw["dataset_id"],
                "template": template,
            }
        )

    # 4. Split or write directly.
    row_count = len(rows)

    def _write_rows(dest: Path, row_list: list[dict]) -> None:
        if format == "jsonl":
            with dest.open("w", encoding="utf-8") as fh:
                for row in row_list:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        elif format == "parquet":
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise RuntimeError(
                    "parquet export requires pyarrow; install with `pip install 'corpus-forge[hf]'`"
                ) from exc
            table = pa.Table.from_pylist(row_list)
            pq.write_table(table, str(dest))
        else:
            raise ValueError(f"unsupported format: {format!r}; expected 'jsonl' or 'parquet'")

    # Derive file extension from out_path suffix.
    suffix = out_path.suffix  # e.g. ".jsonl" or ".parquet"
    stem = out_path.stem  # e.g. "sdft"
    parent = out_path.parent

    if held_out_fraction > 0.0 and row_count > 0:
        # Deterministic split using sha256(content_hash) % 100.
        threshold = int(held_out_fraction * 100)
        train_rows: list[dict] = []
        held_rows: list[dict] = []
        for row, raw in zip(rows, raw_rows, strict=True):
            content_hash = raw.get("content_hash") or ""
            bucket = int(hashlib.sha256(content_hash.encode("utf-8")).hexdigest(), 16) % 100
            if bucket < threshold:
                held_rows.append(row)
            else:
                train_rows.append(row)

        train_path = parent / f"{stem}.train{suffix}"
        held_path = parent / f"{stem}.held_out{suffix}"
        _write_rows(train_path, train_rows)
        _write_rows(held_path, held_rows)

        if push is not None:
            _push_to_hub(train_path, push)
            _push_to_hub(held_path, push)

        return {
            "row_count": row_count,
            "train_count": len(train_rows),
            "held_out_count": len(held_rows),
            "out_paths": [str(train_path), str(held_path)],
        }

    # No split — write all rows to out_path directly.
    _write_rows(out_path, rows)

    if push is not None:
        _push_to_hub(out_path, push)

    return {
        "row_count": row_count,
        "train_count": row_count,
        "held_out_count": 0,
        "out_paths": [str(out_path)],
    }


def _build_default_backend():
    """Build a backend from ~/.config/corpus-forge/config.toml."""
    from corpus_forge.backends.postgres import PostgresBackend
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.config import Config

    cfg = Config.load()
    if cfg.backend.kind == "sqlite":
        return SQLiteBackend(path=cfg.backend.dsn)
    return PostgresBackend(dsn=cfg.backend.dsn, schema=cfg.backend.schema)


def _push_to_hub(out_path: Path, repo_id: str) -> None:
    """Push to a HuggingFace dataset repo via huggingface_hub.upload_file."""
    try:
        from huggingface_hub import upload_file
    except ImportError as exc:
        raise RuntimeError(
            "--push requires huggingface_hub; install with `pip install 'corpus-forge[hf]'`"
        ) from exc
    upload_file(
        path_or_fileobj=str(out_path),
        path_in_repo=out_path.name,
        repo_id=repo_id,
        repo_type="dataset",
    )
