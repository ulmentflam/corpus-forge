"""Export PG/SQLite conversations as templated HuggingFace-format rows."""

from __future__ import annotations

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

    rows: list[dict] = []
    for conv in conversations:
        messages = backend.list_conversation_messages(conv["id"])
        if not messages:
            continue  # skip empty conversations
        text = _tpl.render(
            template,
            [{"role": m["role"], "content": m["content"]} for m in messages],
            model_id=model_id,
            custom_jinja=custom_jinja,
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
