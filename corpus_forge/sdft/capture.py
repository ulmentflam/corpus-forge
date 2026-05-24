"""Phase Q Wave 1 — SDFT capture helpers.

Low-level routines for recording SDFT demonstration pairs into the
``sdft_demonstrations`` table.  All functions accept a raw DB connection
object (sqlite3.Connection or psycopg connection) so they can be called
from MCP dispatcher closures that already hold an open connection.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(value: object) -> str:
    """Produce a canonical, deterministic JSON string for hashing."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _content_hash(
    query: str,
    student_messages: list[dict],
    teacher_messages: list[dict],
    target: str,
) -> str:
    """Compute sha256(canonical_json([query, student_messages, teacher_messages, target]))."""
    payload = _canonical_json([query, student_messages, teacher_messages, target])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_demonstration(
    conn: Any,
    *,
    query: str,
    student_messages: list[dict],
    teacher_messages: list[dict],
    target: str,
    source: str,
    dataset_id: int,
    trace_id: str | None = None,
) -> dict:
    """Insert one SDFT demonstration row, deduplicating on content_hash.

    Dialect-aware: uses ``INSERT OR IGNORE`` (SQLite) or
    ``INSERT ... ON CONFLICT DO NOTHING`` (Postgres) so duplicate
    demonstrations are silently skipped.

    Returns:
        ``{"demonstration_id": int, "deduped": bool}``
        where ``deduped=True`` means the row already existed.
    """
    chash = _content_hash(query, student_messages, teacher_messages, target)
    student_json = json.dumps(student_messages)
    teacher_json = json.dumps(teacher_messages)

    # Detect dialect by inspecting the connection type name.
    conn_type = type(conn).__module__
    is_postgres = "psycopg" in conn_type or "asyncpg" in conn_type

    if is_postgres:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO corpus.sdft_demonstrations
                  (dataset_id, query, student_messages, teacher_messages,
                   target, source, trace_id, content_hash)
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (content_hash) DO NOTHING
                RETURNING id
                """,
                (
                    dataset_id,
                    query,
                    student_json,
                    teacher_json,
                    target,
                    source,
                    trace_id,
                    chash,
                ),
            )
            row = cur.fetchone()
        # Commit so the INSERT is durable AND visible to subsequent
        # connections. Without this the row is rolled back when
        # backend._get_connection's context manager closes the conn,
        # so callers see a phantom demonstration_id that doesn't exist.
        # Regression coverage: tests/integration/test_sdft_capture_pg_commit_regression.py
        conn.commit()
        if row is not None:
            return {"demonstration_id": int(row[0]), "deduped": False}
        # Conflict — fetch the existing id.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM corpus.sdft_demonstrations WHERE content_hash = %s",
                (chash,),
            )
            existing = cur.fetchone()
        return {"demonstration_id": int(existing[0]), "deduped": True}
    else:
        # SQLite path
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO sdft_demonstrations
              (dataset_id, query, student_messages, teacher_messages,
               target, source, trace_id, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                query,
                student_json,
                teacher_json,
                target,
                source,
                trace_id,
                chash,
            ),
        )
        conn.commit()
        if cursor.rowcount == 1:
            return {"demonstration_id": int(cursor.lastrowid), "deduped": False}
        # Conflict — fetch the existing id.
        existing_row = conn.execute(
            "SELECT id FROM sdft_demonstrations WHERE content_hash = ?",
            (chash,),
        ).fetchone()
        return {"demonstration_id": int(existing_row[0]), "deduped": True}


def _should_capture_curation(prior_desc: str | None, new_desc: str | None) -> bool:
    """Return True if descriptions differ meaningfully (not just whitespace, both non-None)."""
    if prior_desc is None or new_desc is None:
        return False
    return prior_desc.strip() != new_desc.strip()
