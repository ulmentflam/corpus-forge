"""CLI subgroup ``corpus-forge feedback`` — interactive feedback TUI and scripted mode.

Phase Q Wave 3 (Q3-G1).

Four subcommands:
    start          — begin a new feedback session (TUI or scripted --no-tui mode).
    resume         — resume a saved session.
    list-sessions  — enumerate persisted session JSON files.
    export-session — write session pending_writes to a JSONL file.

The scripted ``--no-tui`` path (used exclusively by tests) is pure-stdlib
and does not load prompt_toolkit.  The TUI stub prints a message and exits
0 — full TUI implementation is deferred to a follow-up task.

IO contract (Phase L Wave 2):
- Data lines use ``print()``.
- Error messages go to ``sys.stderr`` via ``print(..., file=sys.stderr)``.
- The Typer IO helpers are not used here; status output uses ``corpus_forge.ui``.

Session file format:
    $CORPUS_FORGE_FEEDBACK_DIR/session-<session_id>.json
    {
        "session_id": str,
        "dataset": str,
        "started_at": str (ISO-8601),
        "queue_strategy": str,
        "position": int,
        "processed_chunk_ids": list[int],
        "pending_writes": list[dict],
    }

Cross-reference: ``.planning/tdd/tasks.md`` § Q3-T1.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol

import typer

if TYPE_CHECKING:
    from corpus_forge.config import Config


# ---------------------------------------------------------------------------
# Duck-typed Protocols
#
# DB-API 2.0 connections (both sqlite3.Connection and psycopg.Connection
# satisfy this).  We only use the small slice of the surface area we
# actually call here — ``cursor()`` (for psycopg) plus ``execute()`` (for
# sqlite3) plus ``close()``.  Typing the conn this loosely lets pyrefly
# accept either backend without us having to import psycopg at type time.
# ---------------------------------------------------------------------------


class _DBConnection(Protocol):
    """Minimal DB-API 2.0 ``Connection`` surface used by this module.

    Both ``sqlite3.Connection`` and ``psycopg.Connection`` satisfy this
    structurally.  ``cursor()`` return is left wide because sqlite3 hands
    back a plain ``Cursor`` while psycopg returns a context-managed one,
    and we duck-type the difference at the call sites.
    """

    def cursor(self) -> Any: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Sub-app
# ---------------------------------------------------------------------------

feedback_app = typer.Typer(
    name="feedback",
    help="Interactive feedback TUI and scripted-mode session management.",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FEEDBACK_ENV_VAR = "CORPUS_FORGE_FEEDBACK_DIR"
_DEFAULT_FEEDBACK_BASE = Path.home() / ".cache" / "corpus-forge" / "feedback"
_DEMO_PARTS = 4

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_backend_conn(cfg: Config) -> _DBConnection:
    """Open and return a DB-API 2.0 connection for the given Config object.

    This thin wrapper exists so tests can monkeypatch it without touching
    the Config loading machinery.
    """
    backend = cfg.backend
    kind: str = getattr(backend, "kind", "sqlite")
    dsn: str = str(backend.dsn)

    if kind == "sqlite":
        return sqlite3.connect(dsn)

    import psycopg

    return psycopg.connect(dsn)


def _feedback_dir() -> Path:
    """Return the feedback session directory (env-override or default)."""
    env_val = os.environ.get(_DEFAULT_FEEDBACK_ENV_VAR)
    if env_val:
        return Path(env_val)
    return _DEFAULT_FEEDBACK_BASE


def _session_file(feedback_dir: Path, session_id: str) -> Path:
    return feedback_dir / f"session-{session_id}.json"


def _new_session_id() -> str:
    """Generate a session ID: feedback-<iso_ts>-<short_uuid>."""
    iso_ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"feedback-{iso_ts}-{short}"


def _load_session(feedback_dir: Path, session_id: str) -> dict[str, object]:
    """Load session JSON; raises SystemExit on missing file."""
    path = _session_file(feedback_dir, session_id)
    if not path.exists():
        print(
            f"Session not found: {session_id!r} (looked in {path})",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_session(feedback_dir: Path, session: dict[str, object]) -> None:
    """Write session dict to disk."""
    feedback_dir.mkdir(parents=True, exist_ok=True)
    path = _session_file(feedback_dir, str(session["session_id"]))
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_chunks(conn: _DBConnection, dataset: str) -> list[dict[str, object]]:
    """Fetch all chunks for *dataset* in deterministic order (chunk_id ASC).

    Returns an empty list if the chunks table does not exist (e.g. :memory: DB
    used by tests that only need action-loop smoke coverage).
    """
    # Chunks are owned by documents (or conversations); join through documents
    # and filter by the dataset *name* on datasets.name.
    try:
        if isinstance(conn, sqlite3.Connection):
            rows = conn.execute(
                "SELECT c.id, c.text, c.token_count "
                "FROM chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "JOIN datasets ds ON ds.id = d.dataset_id "
                "WHERE ds.name = ? "
                "ORDER BY c.id ASC",
                (dataset,),
            ).fetchall()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT c.id, c.text, c.token_count "
                    "FROM corpus.chunks c "
                    "JOIN corpus.documents d ON d.id = c.document_id "
                    "JOIN corpus.datasets ds ON ds.id = d.dataset_id "
                    "WHERE ds.name = %s "
                    "ORDER BY c.id ASC",
                    (dataset,),
                )
                rows = cur.fetchall()
    except Exception:
        return []

    return [{"id": r[0], "text": r[1], "token_count": r[2]} for r in rows]


def _get_dataset_id(conn: _DBConnection, dataset: str) -> int | None:
    """Look up the integer dataset_id for *dataset* name.

    Returns None if the datasets table doesn't exist or the name is absent.
    """
    try:
        if isinstance(conn, sqlite3.Connection):
            row = conn.execute(
                "SELECT id FROM datasets WHERE name = ? LIMIT 1", (dataset,)
            ).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM corpus.datasets WHERE name = %s LIMIT 1", (dataset,))
                row = cur.fetchone()
    except Exception:
        return None
    return int(row[0]) if row else None


def _do_record_demo(
    conn: _DBConnection | None,
    demo_str: str,
    dataset: str,
    dry_run: bool,
    pending_writes: list[dict[str, object]],
) -> None:
    """Parse a pipe-separated demo string and record a demonstration (or preview it)."""
    parts = demo_str.split("|", _DEMO_PARTS - 1)
    if len(parts) < _DEMO_PARTS:
        print(
            f"[feedback] --record-demo value must be 'query|student|teacher|target', "
            f"got: {demo_str!r}",
            file=sys.stderr,
        )
        return

    query, student_raw, teacher_raw, target = parts

    student_messages = [{"role": "user", "content": student_raw}]
    teacher_messages = [{"role": "assistant", "content": teacher_raw}]

    payload: dict[str, object] = {
        "query": query,
        "student_messages": student_messages,
        "teacher_messages": teacher_messages,
        "target": target,
        "source": "cli_feedback",
    }

    if dry_run:
        print(f"[dry-run] would record demonstration: query={query!r} target={target!r}")
        return

    if conn is None:
        # Defensive — non-dry-run paths always supply a real connection.
        return
    dataset_id = _get_dataset_id(conn, dataset)
    if dataset_id is None:
        # Fail loudly rather than persist a row under a sentinel id — a
        # missing dataset is almost always a config / typo issue the
        # operator wants to see.
        print(
            f"[feedback] dataset {dataset!r} not found in datasets table; "
            "skipping --record-demo (no rows written)",
            file=sys.stderr,
        )
        return

    from corpus_forge.sdft.capture import record_demonstration

    record_demonstration(
        conn,
        query=query,
        student_messages=student_messages,
        teacher_messages=teacher_messages,
        target=target,
        source="cli_feedback",
        dataset_id=dataset_id,
    )

    pending_writes.append(payload)


def _run_scripted_session(
    *,
    conn: _DBConnection,
    dataset: str,
    actions: list[str],
    record_demos: list[str],
    dry_run: bool,
    session: dict[str, object],
    feedback_dir: Path,
) -> None:
    """Execute the scripted (--no-tui) action loop.

    Actions: approve, skip, next, prev, quit.
    After all actions are consumed (or 'quit' reached), the session ends.
    """
    chunks = _fetch_chunks(conn, dataset)
    position_raw = session["position"]
    position: int = int(position_raw) if isinstance(position_raw, int) else 0

    # Process --record-demo flags first (before action loop)
    existing = session.get("pending_writes", [])
    pending_writes: list[dict[str, object]] = list(existing) if isinstance(existing, list) else []

    for demo_str in record_demos:
        _do_record_demo(conn, demo_str, dataset, dry_run, pending_writes)

    processed = session.setdefault("processed_chunk_ids", [])
    if not isinstance(processed, list):
        processed = []
        session["processed_chunk_ids"] = processed

    # Action loop
    for raw_action in actions:
        act = raw_action.strip().lower()
        if act == "quit":
            break
        elif act in ("skip", "next"):
            if chunks:
                position = min(position + 1, len(chunks))
        elif act == "prev":
            position = max(position - 1, 0)
        elif act == "approve" and chunks and position < len(chunks):
            chunk_id = chunks[position]["id"]
            if chunk_id not in processed:
                processed.append(chunk_id)
            position = min(position + 1, len(chunks))
        # Unknown actions are silently ignored (tolerant scripted mode)

    session["position"] = position
    session["pending_writes"] = pending_writes

    if not dry_run:
        _save_session(feedback_dir, session)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@feedback_app.command("start")
def start(
    dataset: Annotated[str, typer.Option("--dataset", help="Corpus dataset to iterate.")],
    no_tui: Annotated[
        bool,
        typer.Option(
            "--no-tui",
            help="Run in non-interactive scripted mode (required for tests).",
        ),
    ] = False,
    action: Annotated[
        list[str] | None,
        typer.Option(
            "--action",
            help=(
                "Scripted keystroke for --no-tui mode. "
                "Values: approve, skip, next, prev, quit. Repeatable."
            ),
        ),
    ] = None,
    record_demo: Annotated[
        list[str] | None,
        typer.Option(
            "--record-demo",
            help=(
                "Record a demonstration in 'query|student|teacher|target' format. "
                "Repeatable. Calls corpus_forge.sdft.capture.record_demonstration "
                "with source='cli_feedback'."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Preview mode: no DB writes, no session JSON. Prints what would be done.",
        ),
    ] = False,
) -> None:
    """Begin a new feedback session."""
    from corpus_forge.config import Config

    cfg = Config.load()

    feedback_dir = _feedback_dir()

    session_id = _new_session_id()
    session: dict[str, object] = {
        "session_id": session_id,
        "dataset": dataset,
        "started_at": datetime.now(tz=UTC).isoformat(),
        "queue_strategy": "chunk_id_asc",
        "position": 0,
        "processed_chunk_ids": [],
        "pending_writes": [],
    }

    if dry_run:
        print(
            f"[dry-run] feedback start --dataset={dataset!r}: "
            "preview mode — no DB writes, no session JSON."
        )
        # Still process record-demo flags so their dry-run messages are printed
        dummy_pending: list[dict[str, object]] = []
        for demo_str in record_demo or []:
            _do_record_demo(None, demo_str, dataset, dry_run=True, pending_writes=dummy_pending)
        return

    if no_tui:
        conn = _get_backend_conn(cfg)
        try:
            _run_scripted_session(
                conn=conn,
                dataset=dataset,
                actions=list(action or []),
                record_demos=list(record_demo or []),
                dry_run=False,
                session=session,
                feedback_dir=feedback_dir,
            )
        finally:
            with contextlib.suppress(Exception):
                conn.close()
    else:
        # TUI stub — prompt_toolkit loaded lazily only in this branch
        print("TUI mode not implemented; use --no-tui", file=sys.stderr)


@feedback_app.command("resume")
def resume(
    session_id: Annotated[str, typer.Option("--session", help="Session ID to resume.")],
    no_tui: Annotated[
        bool,
        typer.Option(
            "--no-tui",
            help="Run in non-interactive scripted mode.",
        ),
    ] = False,
    action: Annotated[
        list[str] | None,
        typer.Option(
            "--action",
            help="Scripted keystroke for --no-tui mode. Repeatable.",
        ),
    ] = None,
) -> None:
    """Resume a saved feedback session."""
    from corpus_forge.config import Config

    cfg = Config.load()
    feedback_dir = _feedback_dir()

    session = _load_session(feedback_dir, session_id)

    dataset: str = str(session["dataset"])

    if no_tui:
        conn = _get_backend_conn(cfg)
        try:
            _run_scripted_session(
                conn=conn,
                dataset=dataset,
                actions=list(action or []),
                record_demos=[],
                dry_run=False,
                session=session,
                feedback_dir=feedback_dir,
            )
        finally:
            with contextlib.suppress(Exception):
                conn.close()
    else:
        print("TUI mode not implemented; use --no-tui", file=sys.stderr)


@feedback_app.command("list-sessions")
def list_sessions() -> None:
    """List all saved feedback sessions."""
    feedback_dir = _feedback_dir()

    if not feedback_dir.exists():
        print("No sessions found (feedback dir does not exist).")
        return

    session_files = sorted(feedback_dir.glob("session-*.json"))

    if not session_files:
        print("No sessions found.")
        return

    for sf in session_files:
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            sid = data.get("session_id", sf.stem[len("session-") :])
            ds = data.get("dataset", "?")
            started = data.get("started_at", "?")
            print(f"{sid}  dataset={ds}  started={started}")
        except Exception:
            print(f"{sf.name}  (unreadable)")


@feedback_app.command("export-session")
def export_session(
    session_id: Annotated[str, typer.Option("--session", help="Session ID to export.")],
    fmt: Annotated[str, typer.Option("--format", help="Output format (jsonl).")] = "jsonl",
    out: Annotated[Path, typer.Option("--out", help="Output file path.")] = ...,  # type: ignore[assignment]
) -> None:
    """Export a session's pending_writes to a JSONL file."""
    feedback_dir = _feedback_dir()

    session = _load_session(feedback_dir, session_id)
    pending_raw = session.get("pending_writes", [])
    pending_writes: list[dict[str, object]] = (
        list(pending_raw) if isinstance(pending_raw, list) else []
    )

    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as fh:
        for row in pending_writes:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Exported {len(pending_writes)} row(s) to {out}")
