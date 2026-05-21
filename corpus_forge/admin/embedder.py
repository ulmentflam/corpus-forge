"""``corpus-forge embedder ...`` admin verbs (Phase L Wave 7).

Six verbs covering the full CRUD + lifecycle:

- ``list``     — Rich table of every embedder, with active flag,
  fingerprint short-hash, and current chunk-coverage from the backend.
- ``get``      — full record + DB fingerprint match + last-used
  timestamp parsed from ``embed-worker.log``.
- ``add``      — wizard (reuses the ``setup --quick`` prompt patterns).
- ``remove``   — confirm + optional ``--drop-vectors`` cascade.
- ``set-active`` — flip the active flag; trigger the Wave 5 drift flow.
- ``test``     — round-trip ``"hello world"`` through the embedder;
  report latency + dim.

The public helper :func:`run_embedder_smoke` is exposed so the
``ollama test`` verb (sibling module) can call into the same code path
without re-implementing the registry dance.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any

import typer
from rich.table import Table

from corpus_forge.admin.config import (
    load_toml_document,
    resolve_config_path,
    write_toml_atomic,
)
from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok
from corpus_forge.ui.console import warn as ui_warn
from corpus_forge.ui.prompts import Confirm, Prompt

logger = logging.getLogger(__name__)

embedder_app = typer.Typer(
    help="Manage configured embedders (list / add / remove / set-active / test).",
    add_completion=False,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _load_config():
    """Load and return the live ``Config`` instance.

    Imports are deferred so the module imports cleanly even when the
    config file is absent (which the verbs handle individually).
    """

    from corpus_forge.config import Config

    return Config.load()


def _get_backend(config):
    """Return a backend instance suitable for fingerprint + count lookups."""

    kind = getattr(config.backend, "kind", "postgres")
    if kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        return SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    from corpus_forge.backends.postgres import PostgresBackend

    return PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)


@dataclass(frozen=True)
class EmbedderSmokeOutcome:
    """Result of a ``embedder test`` round-trip."""

    name: str
    provider: str
    model_id: str
    dim: int
    elapsed_s: float


def run_embedder_smoke(name: str) -> EmbedderSmokeOutcome:
    """Encode ``"hello world"`` through ``<name>``; return shape + timing.

    Raises ``ValueError`` if the embedder isn't configured, or surfaces
    the underlying registry error if the encode call fails.
    """

    config = _load_config()
    cfg = next((e for e in config.embedders if e.name == name), None)
    if cfg is None:
        raise ValueError(f"embedder {name!r} not found in config")

    # Route through ``register_from_config`` so the provider-specific
    # kwarg policy (device for sentence_transformers; api_key_env +
    # base_url for openai; nothing extra for model2vec) matches the
    # ingest and search call sites — pre-refactor we passed
    # ``device=auto`` and ``api_key_env`` unconditionally, which
    # crashed OpenAI embedders with ``TypeError: unexpected keyword
    # argument 'device'`` AND dropped ``base_url`` so the OpenAI
    # client fell back to api.openai.com.
    from corpus_forge.embedders.registry import register_from_config, registry

    embedder = register_from_config(registry, cfg)

    t0 = time.perf_counter()
    vector = embedder.encode(["hello world"])
    elapsed = time.perf_counter() - t0

    # ``vector`` is numpy-shaped ``(1, dim)``; cope with list-of-list too.
    dim = int(vector.shape[1]) if hasattr(vector, "shape") else len(vector[0])

    return EmbedderSmokeOutcome(
        name=cfg.name,
        provider=cfg.provider,
        model_id=cfg.model_id,
        dim=dim,
        elapsed_s=elapsed,
    )


def _last_used_timestamp(name: str) -> str | None:
    """Best-effort parse of the last embed-worker log line mentioning ``name``."""

    from corpus_forge.logging_config import get_log_dir

    path = get_log_dir() / "embed-worker.log"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    last_ts: str | None = None
    for line in text.splitlines():
        if name in line:
            # Expected format: "2026-05-17 12:34:56.123 [LEVEL  ] logger: msg"
            ts = line.split(" [", 1)[0].strip()
            if ts:
                last_ts = ts
    return last_ts


def _count_coverage(backend, embedder_name: str) -> int | str:
    """Return the embedded chunk count via the Wave-6 backend helper.

    Returns ``"?"`` when the backend isn't reachable or the embedder
    row hasn't been registered yet (legacy / pre-ingest state).
    """

    try:
        row = backend.find_embedder_row_by_name(embedder_name)
    except Exception:
        return "?"
    if row is None:
        return 0
    try:
        return int(backend.count_existing_embeddings(row["id"]))
    except Exception:
        return "?"


# ── Verbs ───────────────────────────────────────────────────────────────


@embedder_app.command("list")
def cmd_list() -> None:
    """Render every configured embedder as a Rich table."""

    try:
        config = _load_config()
    except Exception as exc:
        ui_error(f"Could not load config: {exc}")
        raise typer.Exit(code=1) from exc

    backend = None
    try:
        backend = _get_backend(config)
    except Exception:
        # Backend unreachable; coverage column degrades to "?".
        backend = None

    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    table = Table(title="Embedders", title_style="h1", show_header=True)
    table.add_column("Name", style="accent.path")
    table.add_column("Provider", style="muted")
    table.add_column("Model", style="muted")
    table.add_column("Dim", justify="right", style="accent.number")
    table.add_column("Active", justify="center")
    table.add_column("Fingerprint", style="muted")
    table.add_column("Coverage", justify="right", style="accent.number")
    for cfg in config.embedders:
        fp = embedder_fingerprint(cfg).short[:8]
        coverage = _count_coverage(backend, cfg.name) if backend is not None else "?"
        table.add_row(
            cfg.name,
            cfg.provider,
            cfg.model_id,
            str(cfg.dimension),
            "yes" if cfg.active else "no",
            fp,
            str(coverage),
        )
    ui_console.print(table)


@embedder_app.command("get")
def cmd_get(
    name: Annotated[str, typer.Argument(help="Embedder name.")],
) -> None:
    """Print the full record + DB fingerprint match + last-used timestamp."""

    try:
        config = _load_config()
    except Exception as exc:
        ui_error(f"Could not load config: {exc}")
        raise typer.Exit(code=1) from exc

    cfg = next((e for e in config.embedders if e.name == name), None)
    if cfg is None:
        ui_error(f"Embedder {name!r} not found.")
        raise typer.Exit(code=1)

    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    payload: dict[str, Any] = {
        "name": cfg.name,
        "provider": cfg.provider,
        "model_id": cfg.model_id,
        "dimension": cfg.dimension,
        "normalize": cfg.normalize,
        "distance": cfg.distance,
        "active": cfg.active,
        "batch_size": cfg.batch_size,
        "device": cfg.device,
        "api_key_env": cfg.api_key_env,
        "base_url": str(cfg.base_url) if cfg.base_url else None,
        "fingerprint": embedder_fingerprint(cfg).short,
        "last_used": _last_used_timestamp(cfg.name),
    }

    # Try to surface the DB fingerprint match too.
    try:
        backend = _get_backend(config)
        row = backend.find_embedder_row_by_name(cfg.name)
        payload["registered"] = row is not None
        if row is not None:
            from corpus_forge.embedders.fingerprint import _stored_fingerprint  # local

            payload["db_fingerprint"] = _stored_fingerprint(row).short
            payload["fingerprint_match"] = (
                _stored_fingerprint(row).full == embedder_fingerprint(cfg).full
            )
    except Exception:
        payload["registered"] = "?"

    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


@embedder_app.command("add")
def cmd_add(
    name: Annotated[str, typer.Argument(help="New embedder name (unique).")],
) -> None:
    """Add a new embedder via a small wizard.

    The wizard mirrors the ``setup --quick`` embedder block — a few
    questions, sensible defaults, and a write-back into ``[[embedders]]``.
    """

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)

    existing = doc.get("embedders") or []
    if any(e.get("name") == name for e in existing):
        ui_error(f"Embedder {name!r} already exists.")
        raise typer.Exit(code=1)

    provider = Prompt.ask(
        "Provider",
        choices=["sentence_transformers", "openai"],
        default="sentence_transformers",
    )
    model_id = Prompt.ask("Model id", default="Qwen/Qwen3-Embedding-8B")
    dimension = int(Prompt.ask("Dimension", default="4096"))
    normalize = Confirm.ask("Normalize vectors?", default=True)
    distance = Prompt.ask("Distance metric", choices=["cosine", "l2", "ip"], default="cosine")

    entry = {
        "name": name,
        "provider": provider,
        "model_id": model_id,
        "dimension": dimension,
        "normalize": normalize,
        "distance": distance,
        "active": True,
    }
    if provider == "openai":
        entry["api_key_env"] = Prompt.ask("API-key env var", default="OPENAI_API_KEY")
        base_url = Prompt.ask("Base URL (blank for OpenAI default)", default="")
        if base_url:
            entry["base_url"] = base_url

    # Append to the AOT.  tomlkit handles plain dict → table-array.
    if "embedders" not in doc:
        doc["embedders"] = []
    doc["embedders"].append(entry)

    # Validate via Config.load round-trip.
    write_toml_atomic(config_path, doc)
    try:
        _load_config()
    except Exception as exc:
        ui_error(f"New embedder invalid: {exc}")
        raise typer.Exit(code=1) from exc
    ui_ok(f"Added embedder {name!r}")


@embedder_app.command("remove")
def cmd_remove(
    name: Annotated[str, typer.Argument(help="Embedder name to remove.")],
    drop_vectors: Annotated[
        bool,
        typer.Option(
            "--drop-vectors",
            help="Also truncate the per-embedder vector table in the backend.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove ``name`` from the config (optionally drop its vectors)."""

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    embedders = doc.get("embedders") or []
    idx = next((i for i, e in enumerate(embedders) if e.get("name") == name), None)
    if idx is None:
        ui_error(f"Embedder {name!r} not found.")
        raise typer.Exit(code=1)

    if not yes and not Confirm.ask(f"Remove embedder {name!r}?", default=False):
        ui_info("Aborted.")
        raise typer.Exit(code=0)

    embedders.pop(idx)
    write_toml_atomic(config_path, doc)
    ui_ok(f"Removed embedder {name!r} from config.")

    if drop_vectors:
        try:
            config = _load_config()
            backend = _get_backend(config)
            row = backend.find_embedder_row_by_name(name)
            if row is None:
                ui_info("No backend rows to drop — embedder was never registered.")
                return
            table_name = row.get("table_name") or f"embeddings_{name}"
            # Use the same _execute pattern existing helpers use.
            if config.backend.kind == "sqlite":
                backend._execute(f"DELETE FROM {table_name}")
            else:
                backend._execute(f"DELETE FROM corpus.{table_name}")
            ui_ok(f"Dropped vectors from {table_name}.")
        except Exception as exc:
            ui_warn(f"Vector drop failed: {exc}")


@embedder_app.command("set-active")
def cmd_set_active(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Embedder name to mark active.")],
) -> None:
    """Mark ``name`` as the only active embedder; others are flipped off.

    Triggers the Wave 5 fingerprint-drift detection afterwards so the
    user is informed if existing vectors need a re-encode.
    """

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    embedders = doc.get("embedders") or []
    if not any(e.get("name") == name for e in embedders):
        ui_error(f"Embedder {name!r} not found.")
        raise typer.Exit(code=1)

    for entry in embedders:
        entry["active"] = entry.get("name") == name

    write_toml_atomic(config_path, doc)
    ui_ok(f"Active embedder = {name}")

    # Re-validate + run drift detection.
    try:
        config = _load_config()
        backend = _get_backend(config)
        from corpus_forge.embedders.fingerprint import compare_active

        drifts = compare_active(config, backend)
    except Exception as exc:
        ui_warn(f"Drift check skipped: {exc}")
        return

    if not drifts:
        ui_info("No fingerprint drift detected.")
        return

    # Hand off to the existing drift-prompt + dispatcher.
    background = bool(getattr(getattr(ctx, "obj", None), "background", False))
    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    choice = prompt_for_drift(drifts, background=background, non_interactive=False)
    if choice == "now":
        ui_info(f"Re-encoding {len(drifts)} embedder(s)...")
        from corpus_forge.embed import backfill_embedder

        for d in drifts:
            backfill_embedder(d.name)
        ui_ok("Re-encode complete.")
    elif choice == "later":
        ui_info("Re-encode deferred — run `corpus-forge embed -e <name>` when ready.")
    else:
        ui_info("Skipped.")


@embedder_app.command("test")
def cmd_test(
    name: Annotated[str, typer.Argument(help="Embedder name to smoke-test.")],
) -> None:
    """Round-trip ``"hello world"`` through ``name`` and report timing."""

    try:
        outcome = run_embedder_smoke(name)
    except ValueError as exc:
        ui_error(str(exc))
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        ui_error(f"Embedder load / encode failed: {exc}")
        raise typer.Exit(code=1) from exc

    ui_ok(
        f"{outcome.name} ({outcome.provider}/{outcome.model_id}) "
        f"dim={outcome.dim} in {outcome.elapsed_s * 1000:.0f} ms"
    )


# ── repair-indexes ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class IndexAuditRow:
    """Per-embedder summary returned by :func:`audit_embedder_indexes`."""

    name: str
    dimension: int
    table_name: str
    current_indexdef: str | None
    target_indexdef: str
    status: str  # "OK" | "DRIFT" | "MISSING" | "TABLE_MISSING"


def _target_indexdef(table_name: str, dimension: int) -> str:
    """Canonical ``CREATE INDEX`` SQL for a given embedder dim.

    Mirrors :func:`corpus_forge.backends.postgres._dense_index_strategy`.
    The output is compared (substring-wise) against
    ``pg_get_indexdef`` so it doesn't have to match formatting exactly.
    """
    from corpus_forge.backends.postgres import _dense_index_strategy

    index_expr, _search_expr, ops = _dense_index_strategy(dimension)
    return f"CREATE INDEX {table_name}_hnsw ON corpus.{table_name} USING hnsw ({index_expr} {ops})"


def audit_embedder_indexes(backend) -> list[IndexAuditRow]:
    """Inspect every per-embedder table on a PostgresBackend.

    Returns one ``IndexAuditRow`` per row in ``corpus.embedders``,
    flagging drift between the dim recorded for that embedder and
    the actual HNSW index strategy on its data table. SQLite
    backends short-circuit to an empty list because their vector
    index lives in a ``sqlite-vec`` virtual table that has no
    pgvector concept of "index strategy."
    """

    rows: list[IndexAuditRow] = []
    if backend.__class__.__name__ == "SQLiteBackend":
        return rows

    embedders = backend._execute(
        "SELECT name, dimension, table_name FROM corpus.embedders ORDER BY id"
    )

    for r in embedders:
        name = r["name"]
        dim = int(r["dimension"])
        table_name = r["table_name"]

        if not table_name:
            rows.append(
                IndexAuditRow(
                    name=name,
                    dimension=dim,
                    table_name=table_name or "",
                    current_indexdef=None,
                    target_indexdef="",
                    status="TABLE_MISSING",
                )
            )
            continue

        exists = backend._execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'corpus' AND table_name = %s",
            (table_name,),
        )
        if not exists:
            rows.append(
                IndexAuditRow(
                    name=name,
                    dimension=dim,
                    table_name=table_name,
                    current_indexdef=None,
                    target_indexdef=_target_indexdef(table_name, dim),
                    status="TABLE_MISSING",
                )
            )
            continue

        idx_rows = backend._execute(
            "SELECT pg_get_indexdef(c.oid) AS def "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'corpus' AND c.relname = %s AND c.relkind = 'i'",
            (f"{table_name}_hnsw",),
        )
        current = idx_rows[0]["def"] if idx_rows else None

        from corpus_forge.backends.postgres import (
            _HALFVEC_INDEX_LIMIT,
            _PGVECTOR_INDEX_LIMIT,
        )

        target = _target_indexdef(table_name, dim)

        # ``pg_get_indexdef`` re-renders index expressions with its own
        # parenthesisation/whitespace conventions (e.g. an extra outer
        # paren around the ``::`` cast: ``((subvector(...))::halfvec(N))``
        # vs our canonical ``(subvector(...)::halfvec(N))``). A naive
        # ``target_fragment in current`` substring match flags these
        # semantically-identical indexes as DRIFT. Compare on the
        # key tokens that actually distinguish the strategies instead:
        #
        # - ``ops`` class (vector_cosine_ops vs halfvec_cosine_ops) —
        #   the only thing the planner cares about for operator-class
        #   match.
        # - For halfvec, the projection width ``halfvec(N)`` AND the
        #   ``subvector(`` marker (so an old broken
        #   ``embedding::halfvec(N)`` index — without ``subvector`` —
        #   is still flagged as DRIFT and gets rebuilt).
        def _norm(s: str) -> str:
            return s.replace(" ", "").lower()

        if current is None:
            status = "MISSING"
        elif dim <= _PGVECTOR_INDEX_LIMIT:
            # dim <= 2000: just check the ops class.
            status = "OK" if "vector_cosine_ops" in current else "DRIFT"
        else:
            index_dim = min(dim, _HALFVEC_INDEX_LIMIT)
            current_n = _norm(current)
            has_ops = "halfvec_cosine_ops" in current_n
            has_dim = f"halfvec({index_dim})" in current_n
            has_subvector = "subvector(" in current_n
            status = "OK" if (has_ops and has_dim and has_subvector) else "DRIFT"
        rows.append(
            IndexAuditRow(
                name=name,
                dimension=dim,
                table_name=table_name,
                current_indexdef=current,
                target_indexdef=target,
                status=status,
            )
        )

    return rows


def repair_embedder_index(backend, row: IndexAuditRow) -> None:
    """Drop + recreate the HNSW index to match the target strategy.

    The two statements run in a single backend transaction so a
    failure leaves the table without an index rather than wedged
    between two competing definitions. Callers are expected to
    short-circuit on ``row.status == "OK"``.
    """

    from corpus_forge.backends.postgres import _dense_index_strategy

    index_expr, _search_expr, ops = _dense_index_strategy(row.dimension)
    index_name = f"{row.table_name}_hnsw"

    backend._execute(f"DROP INDEX IF EXISTS corpus.{index_name}")
    backend._execute(
        f"CREATE INDEX IF NOT EXISTS {index_name} "
        f"ON corpus.{row.table_name} "
        f"USING hnsw ({index_expr} {ops})"
    )


@embedder_app.command("repair-indexes")
def cmd_repair_indexes(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Actually drop + recreate drifted indexes. Without "
                "this flag the command prints a per-embedder diff "
                "and exits without touching the database."
            ),
        ),
    ] = False,
) -> None:
    """Audit per-embedder HNSW indexes against the configured dim.

    Detects the case where the recorded ``dimension`` outgrew the
    pgvector ``vector`` index ceiling (2000 dims) — common after a
    user re-registers an embedder at the model's native Matryoshka
    width. Reports drift; ``--apply`` rebuilds drifted indexes
    using the matching ``halfvec`` projection strategy.
    """

    config = _load_config()
    backend = _get_backend(config)

    rows = audit_embedder_indexes(backend)
    if not rows:
        ui_info("No embedders found (or running on SQLite — no HNSW indexes).")
        return

    table = Table(title="Per-embedder HNSW index audit", show_lines=False)
    table.add_column("Name")
    table.add_column("Dim", justify="right")
    table.add_column("Status")
    table.add_column("Current → Target")

    drifted: list[IndexAuditRow] = []
    for r in rows:
        if r.status in ("DRIFT", "MISSING"):
            drifted.append(r)
        delta = ""
        if r.status == "OK":
            delta = "(matches)"
        elif r.status == "TABLE_MISSING":
            delta = "(table not yet created)"
        else:
            short_current = r.current_indexdef.split("USING ")[-1] if r.current_indexdef else "—"
            short_target = r.target_indexdef.split("USING ")[-1]
            delta = f"{short_current}  →  {short_target}"
        table.add_row(r.name, str(r.dimension), r.status, delta)

    ui_console.print(table)

    if not drifted:
        ui_ok("All per-embedder indexes match their configured dimension.")
        return

    if not apply:
        ui_warn(
            f"{len(drifted)} embedder(s) need a rebuild. "
            "Re-run with --apply to drop + recreate the drifted indexes."
        )
        raise typer.Exit(code=1)

    for r in drifted:
        ui_info(f"Rebuilding {r.name} ({r.dimension}d)…")
        try:
            repair_embedder_index(backend, r)
        except Exception as exc:
            ui_error(f"  failed: {exc}")
            raise typer.Exit(code=1) from exc
        ui_ok(f"  done: {r.name}")

    ui_ok(f"Rebuilt {len(drifted)} index(es).")


__all__ = [
    "EmbedderSmokeOutcome",
    "IndexAuditRow",
    "audit_embedder_indexes",
    "embedder_app",
    "repair_embedder_index",
    "run_embedder_smoke",
]
