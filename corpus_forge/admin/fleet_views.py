"""``corpus-forge models list`` / ``hosts list`` — fleet read verbs (rfc-fleet-1).

Items 6 of ``rfc-fleet-1-model-telemetry-and-bench``: the read side of the
telemetry tables that :mod:`corpus_forge.admin.bench` and the passive
``embed-run`` telemetry in :mod:`corpus_forge.embed` populate.  Two verbs,
both Rich-table-by-default with an agent-friendly ``--json`` carve-out:

* ``models list`` — the ``models`` registry joined to the *latest*
  benchmark per ``(host_id, model_key)``.  One row per registered model
  *per host that has benchmarked it*; never-benchmarked models still
  appear (one row, no host).  Each row shows the per-host latest
  ``chunks_per_s`` / ``transport`` / ``device`` / ``source`` plus a
  human staleness hint rendered from the ``measured_at`` age
  (``"2h ago"`` / ``"3d ago"``).
* ``hosts list`` — the ``hosts`` registry with a short accelerator
  summary (pulled out of the stored probe blob — name/kind, not the
  whole JSON), ``last_seen`` + its age, the count of distinct models the
  host has benchmarked, and the host's freshest aggregate
  ``chunks_per_s``.

Both verbs are thin: the "latest per (host, model)" window query lives in
the backends (:meth:`StorageBackend.list_models_with_latest_benchmark` /
:meth:`list_hosts_with_latest_rate`), portable across Postgres and SQLite
and served by the 0018 ``(host_id, model_key, measured_at DESC)`` index.
This module only shapes the rows for the table / JSON and renders the
staleness + accelerator strings.

Both share :func:`_build_backend` with the bench verb's pattern (build the
configured backend, ``migrate()`` to be safe, read).  ``--json`` (or
ambient agent detection) emits a single clean object; the wrapper in
``cli.py`` skips its auto-emission for these self-emitting verbs.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Annotated, Any

import typer
from rich.table import Table

from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info

logger = logging.getLogger(__name__)

models_app = typer.Typer(
    help="Inspect the model registry + latest throughput per host (rfc-fleet-1).",
    add_completion=False,
)
hosts_app = typer.Typer(
    help="Inspect registered fleet hosts + their throughput (rfc-fleet-1).",
    add_completion=False,
)


# ── Staleness rendering ────────────────────────────────────────────────────

#: Boundaries (in seconds) for the coarse "Ns / Nm / Nh / Nd ago" buckets.
#: A measured_at within the minute renders in seconds, within the hour in
#: minutes, within the day in hours, else in whole days.
_SECONDS_PER_MINUTE: int = 60
_SECONDS_PER_HOUR: int = 60 * 60
_SECONDS_PER_DAY: int = 24 * 60 * 60

#: Rendered when a row has no benchmark at all (``measured_at is None``).
_NEVER: str = "never"
#: Rendered for a present-but-unparseable timestamp (defensive — both
#: backends store a parseable value, so this only guards corruption).
_UNKNOWN_AGE: str = "?"
#: Rendered for any optional cell whose value is ``None``.
_DASH: str = "—"


def _coerce_datetime(value: Any) -> datetime | None:
    """Best-effort parse of a ``measured_at`` cell into an aware ``datetime``.

    Postgres returns a ``datetime`` (tz-aware) directly; SQLite stores an
    ISO-8601 ``str``.  Naive datetimes / strings are assumed UTC (the
    backends stamp ``now()`` in UTC).  Returns ``None`` for ``None`` or an
    unparseable value so the caller can render :data:`_UNKNOWN_AGE` rather
    than raise on a corrupt row.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def format_age(measured_at: Any, *, now: datetime | None = None) -> str:
    """Render a ``measured_at`` cell as a coarse human staleness hint.

    Buckets: ``"<n>s ago"`` under a minute, ``"<n>m ago"`` under an hour,
    ``"<n>h ago"`` under a day, else ``"<n>d ago"``.  ``None`` renders
    :data:`_NEVER` (no benchmark yet); an unparseable value renders
    :data:`_UNKNOWN_AGE`.  A future timestamp (clock skew across the
    tailnet) clamps to ``"0s ago"`` rather than printing a negative.
    """
    if measured_at is None:
        return _NEVER
    parsed = _coerce_datetime(measured_at)
    if parsed is None:
        return _UNKNOWN_AGE
    reference = now or datetime.now(tz=UTC)
    # Clamp clock skew across the tailnet to 0 rather than print a
    # negative age.
    delta_s = max(int((reference - parsed).total_seconds()), 0)
    if delta_s < _SECONDS_PER_MINUTE:
        return f"{delta_s}s ago"
    if delta_s < _SECONDS_PER_HOUR:
        return f"{delta_s // _SECONDS_PER_MINUTE}m ago"
    if delta_s < _SECONDS_PER_DAY:
        return f"{delta_s // _SECONDS_PER_HOUR}h ago"
    return f"{delta_s // _SECONDS_PER_DAY}d ago"


# ── Accelerator summary ────────────────────────────────────────────────────


def accelerator_summary(accelerator: Any) -> str:
    """Collapse a stored accelerator probe blob into a short one-liner.

    The probe (:func:`corpus_forge.acceleration.detect_accelerator`) is
    stored as JSONB on Postgres (read back as a ``dict``) and as a JSON
    ``str`` on SQLite — both are accepted.  We surface the most useful
    fields only: ``device_name`` when present (CUDA cards report it), else
    the ``kind`` lane (``cuda`` / ``mps`` / ``cpu``); VRAM is appended
    when the probe captured it.  Anything missing / unparseable degrades
    to :data:`_DASH` rather than dumping the whole blob into a table cell.
    """
    if accelerator is None:
        return _DASH
    blob: Any = accelerator
    if isinstance(accelerator, str):
        try:
            blob = json.loads(accelerator)
        except ValueError:
            return _DASH
    if not isinstance(blob, dict):
        return _DASH

    name = blob.get("device_name") or blob.get("kind")
    if not name:
        return _DASH
    label = str(name)
    vram_mb = blob.get("vram_mb")
    if isinstance(vram_mb, (int, float)) and vram_mb > 0:
        label = f"{label} ({int(vram_mb) // 1024} GB)"
    return label


# ── Number formatting ──────────────────────────────────────────────────────


def _fmt_rate(value: Any, *, digits: int = 1) -> str:
    """Render an optional ``chunks_per_s`` for a table cell; ``None`` → dash."""
    if value is None:
        return _DASH
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return _DASH


def _cell(value: Any) -> str:
    """Render an optional string cell; ``None`` / empty → dash."""
    if value is None or value == "":
        return _DASH
    return str(value)


# ── Duration formatting (hosts plan) ────────────────────────────────────────


def format_duration(seconds: float | None) -> str:
    """Render a projected-drain duration as a coarse ``~2h13m`` string.

    The drain estimate is ``backlog / rate``; this collapses it to the two
    most-significant non-zero units so the table cell stays short:

    * under a minute → ``"~Ns"``
    * under an hour → ``"~Nm"`` (seconds dropped — the estimate is coarse)
    * under a day → ``"~NhMm"``
    * else → ``"~NdMh"``

    ``None`` or a non-finite / non-positive input renders :data:`_DASH`
    (no benchmark → no rate → no estimate).  Rounds *up* to the nearest
    whole second so a tiny residual backlog never shows ``~0s``.
    """
    if seconds is None:
        return _DASH
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return _DASH
    if not math.isfinite(value) or value <= 0:
        # NaN / inf / non-positive → no estimate.
        return _DASH
    total = max(int(value + 0.999), 1)  # round up; never drop to ~0s.
    if total < _SECONDS_PER_MINUTE:
        return f"~{total}s"
    if total < _SECONDS_PER_HOUR:
        return f"~{total // _SECONDS_PER_MINUTE}m"
    if total < _SECONDS_PER_DAY:
        hours = total // _SECONDS_PER_HOUR
        minutes = (total % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE
        return f"~{hours}h{minutes}m"
    days = total // _SECONDS_PER_DAY
    hours = (total % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR
    return f"~{days}d{hours}h"


# ── models list ────────────────────────────────────────────────────────────


def render_models_table(rows: list[dict], *, now: datetime | None = None) -> Table:
    """Build the Rich table for ``models list`` (one row per model+host)."""
    table = Table(title="Model registry — latest benchmark per host", show_header=True)
    table.add_column("Model key", style="accent.path")
    table.add_column("Kind")
    table.add_column("Provider", style="muted")
    table.add_column("Dim", justify="right")
    table.add_column("Host", style="muted")
    table.add_column("chunks/s", justify="right", style="accent.number")
    table.add_column("Transport")
    table.add_column("Device")
    table.add_column("Last benchmark")
    for row in rows:
        table.add_row(
            _cell(row.get("model_key")),
            _cell(row.get("kind")),
            _cell(row.get("provider")),
            _cell(row.get("dimension")),
            _cell(row.get("host_id")),
            _fmt_rate(row.get("chunks_per_s")),
            _cell(row.get("transport")),
            _cell(row.get("device")),
            format_age(row.get("measured_at"), now=now),
        )
    return table


def models_to_dict(rows: list[dict], *, now: datetime | None = None) -> dict[str, Any]:
    """Serialise ``models list`` rows to one JSON-able object (agent mode).

    ``measured_at`` is emitted both raw (``default=str`` handles the
    datetime) and as a rendered ``age`` string so an agent gets the
    machine value and the human hint without re-deriving the bucket.
    """
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        out_rows.append(
            {
                "model_key": row.get("model_key"),
                "kind": row.get("kind"),
                "provider": row.get("provider"),
                "model_id": row.get("model_id"),
                "dimension": row.get("dimension"),
                "host_id": row.get("host_id"),
                "chunks_per_s": row.get("chunks_per_s"),
                "transport": row.get("transport"),
                "device": row.get("device"),
                "source": row.get("source"),
                "measured_at": row.get("measured_at"),
                "age": format_age(row.get("measured_at"), now=now),
            }
        )
    return {"models": out_rows, "count": len(out_rows)}


# ── hosts list ─────────────────────────────────────────────────────────────


def render_hosts_table(rows: list[dict], *, now: datetime | None = None) -> Table:
    """Build the Rich table for ``hosts list`` (one row per host)."""
    table = Table(title="Fleet hosts", show_header=True)
    table.add_column("Host", style="accent.path")
    table.add_column("Hostname", style="muted")
    table.add_column("OS", style="muted")
    table.add_column("Accelerator")
    table.add_column("Models", justify="right")
    table.add_column("chunks/s", justify="right", style="accent.number")
    table.add_column("Last seen")
    for row in rows:
        table.add_row(
            _cell(row.get("host_id")),
            _cell(row.get("hostname")),
            _cell(row.get("os")),
            accelerator_summary(row.get("accelerator")),
            _cell(row.get("models")),
            _fmt_rate(row.get("latest_chunks_per_s")),
            format_age(row.get("last_seen"), now=now),
        )
    return table


def hosts_to_dict(rows: list[dict], *, now: datetime | None = None) -> dict[str, Any]:
    """Serialise ``hosts list`` rows to one JSON-able object (agent mode)."""
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        out_rows.append(
            {
                "host_id": row.get("host_id"),
                "hostname": row.get("hostname"),
                "os": row.get("os"),
                "accelerator": accelerator_summary(row.get("accelerator")),
                "models": row.get("models"),
                "latest_chunks_per_s": row.get("latest_chunks_per_s"),
                "last_seen": row.get("last_seen"),
                "last_seen_age": format_age(row.get("last_seen"), now=now),
            }
        )
    return {"hosts": out_rows, "count": len(out_rows)}


# ── hosts plan (rfc-fleet-2 item 8 — stretch, print-only) ───────────────────

#: Shown for a lane that has zero benchmarks on any host.
_NO_BENCH_HINT: str = "no benchmark — run `corpus-forge bench embed`"


def _fastest_host_for(
    model_key: str, by_model: dict[str, list[tuple[str | None, float | None]]]
) -> tuple[str, float] | None:
    """Pick the highest-``chunks_per_s`` host for ``model_key`` (deterministic).

    ``by_model`` maps each ``model_key`` to a list of ``(host_id, rate)``
    candidates harvested from :meth:`list_models_with_latest_benchmark`.
    Rows with no ``host_id`` (never-benchmarked registry rows) or a
    ``None`` / non-positive rate are not assignable and are skipped.
    Returns the ``(host_id, rate)`` with the highest rate; ties break on
    ``host_id`` ascending so the recommendation is stable for tests.
    Returns ``None`` when no host has a usable benchmark for the lane.
    """
    candidates: list[tuple[str, float]] = []
    for host_id, rate in by_model.get(model_key, []):
        if host_id is None or rate is None:
            continue
        try:
            rate_f = float(rate)
        except (TypeError, ValueError):
            continue
        if rate_f <= 0:
            continue
        candidates.append((host_id, rate_f))
    if not candidates:
        return None
    # Highest rate wins; stable tie-break on host_id ascending.
    candidates.sort(key=lambda hr: (-hr[1], hr[0]))
    return candidates[0]


def build_plan(
    *,
    embedders: list[Any],
    model_rows: list[dict],
    embedder_id_for: Any,
    backlog_for: Any,
    live_claims_for: Any | None = None,
) -> list[dict[str, Any]]:
    """Compose benchmarks + per-lane backlog into a host→lane recommendation.

    Pure / read-only: takes the active embedder configs, the
    ``list_models_with_latest_benchmark`` rows, and three callables the
    caller wires to the backend —

    * ``embedder_id_for(embedder) -> int | None`` — resolve the lane's
      ``corpus.embedders`` row id (``None`` if the lane was never
      registered, i.e. nothing embedded yet → backlog unknown).
    * ``backlog_for(embedder_id, extensions) -> int`` — chunks still
      missing this embedder's embedding.
    * ``live_claims_for(embedder_id) -> int`` — optional; in-flight
      reservations by the fleet (nice-to-have; ``None`` skips it).

    One result dict per active embedder (lane), each carrying ``lane``
    (the config name), ``model_key``, ``backlog``, ``recommended_host``,
    ``rate``, ``drain_seconds`` (raw float for the caller to format), and
    ``in_flight``.  A lane with no benchmark on any host gets
    ``recommended_host=None`` / ``rate=None`` / ``drain_seconds=None`` and
    a ``note`` of :data:`_NO_BENCH_HINT`.  Deterministic for fixed input.
    """
    by_model: dict[str, list[tuple[str | None, float | None]]] = {}
    for row in model_rows:
        key = row.get("model_key")
        if key is None:
            continue
        by_model.setdefault(key, []).append((row.get("host_id"), row.get("chunks_per_s")))

    lanes: list[dict[str, Any]] = []
    for ec in embedders:
        model_key = f"{ec.provider}:{ec.model_id}"
        extensions = list(ec.extensions) if getattr(ec, "extensions", None) else None
        embedder_id = embedder_id_for(ec)
        backlog = 0 if embedder_id is None else int(backlog_for(embedder_id, extensions))
        in_flight: int | None = None
        if live_claims_for is not None and embedder_id is not None:
            in_flight = int(live_claims_for(embedder_id))

        fastest = _fastest_host_for(model_key, by_model)
        if fastest is None:
            lanes.append(
                {
                    "lane": ec.name,
                    "model_key": model_key,
                    "backlog": backlog,
                    "recommended_host": None,
                    "rate": None,
                    "drain_seconds": None,
                    "in_flight": in_flight,
                    "note": _NO_BENCH_HINT,
                }
            )
            continue
        host_id, rate = fastest
        drain = backlog / rate if rate > 0 else None
        lanes.append(
            {
                "lane": ec.name,
                "model_key": model_key,
                "backlog": backlog,
                "recommended_host": host_id,
                "rate": rate,
                "drain_seconds": drain,
                "in_flight": in_flight,
                "note": None,
            }
        )
    # Deterministic output order: lane name ascending.
    lanes.sort(key=lambda lane: lane["lane"])
    return lanes


def render_plan_table(lanes: list[dict[str, Any]]) -> Table:
    """Build the Rich table for ``hosts plan`` (one row per lane)."""
    table = Table(title="Recommended host → lane assignment", show_header=True)
    table.add_column("Lane", style="accent.path")
    table.add_column("Backlog", justify="right", style="accent.number")
    table.add_column("Recommended host", style="muted")
    table.add_column("chunks/s", justify="right", style="accent.number")
    table.add_column("Projected drain", justify="right")
    for lane in lanes:
        host_cell = lane.get("recommended_host")
        rate_cell = _fmt_rate(lane.get("rate"))
        drain_cell = format_duration(lane.get("drain_seconds"))
        if host_cell is None:
            # No benchmark — surface the hint in the host column.
            host_cell = lane.get("note") or _NO_BENCH_HINT
        table.add_row(
            _cell(lane.get("lane")),
            _cell(lane.get("backlog")),
            _cell(host_cell),
            rate_cell,
            drain_cell,
        )
    return table


def plan_to_dict(lanes: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialise the plan to one JSON-able object (agent mode)."""
    out_rows: list[dict[str, Any]] = []
    for lane in lanes:
        out_rows.append(
            {
                "lane": lane.get("lane"),
                "model_key": lane.get("model_key"),
                "backlog": lane.get("backlog"),
                "recommended_host": lane.get("recommended_host"),
                "rate": lane.get("rate"),
                "drain_seconds": lane.get("drain_seconds"),
                "drain": format_duration(lane.get("drain_seconds")),
                "in_flight": lane.get("in_flight"),
                "note": lane.get("note"),
            }
        )
    return {"lanes": out_rows, "count": len(out_rows)}


# ── Backend builder (mirrors admin.bench) ──────────────────────────────────


def _build_backend(config: Any) -> Any:
    """Return a migrated backend instance for the configured kind.

    Mirrors :func:`corpus_forge.admin.bench._build_backend`; kept local so
    the read verbs don't import the (heavier) bench module just to reach a
    backend.  ``migrate()`` is a safe no-op on an up-to-date schema and
    guarantees the 0018 telemetry tables exist before we read them.
    """
    kind = getattr(config.backend, "kind", "postgres")
    if kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    elif kind == "postgres":
        from corpus_forge.backends.postgres import PostgresBackend

        backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {kind}")
    backend.migrate()
    return backend


def _load_config_or_exit() -> Any:
    """Load the active config or raise ``typer.Exit(2)`` with a hint."""
    from corpus_forge.config import Config

    try:
        return Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run `corpus-forge setup` to create one.")
        raise typer.Exit(code=2) from None


def _close_backend(backend: Any) -> None:
    """Best-effort close of a backend handle (no-op when unclosable)."""
    closer = getattr(backend, "close", None)
    if callable(closer):
        import contextlib

        with contextlib.suppress(Exception):  # pragma: no cover — defensive
            closer()


# ── CLI verbs ──────────────────────────────────────────────────────────────


@models_app.command("list")
def cmd_models_list(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of the Rich table (agent mode)."),
    ] = False,
) -> None:
    """List registered models + the latest benchmark per host.

    Joins the ``models`` registry to the freshest ``model_benchmarks``
    row per ``(host_id, model_key)`` and renders a staleness hint from
    each row's ``measured_at`` age.  Models that have never been
    benchmarked still appear (no host, no metrics) so the registry is
    complete.
    """
    from corpus_forge.ui import agent as ui_agent

    agent_mode = json_out or ui_agent.is_agent_mode()
    config = _load_config_or_exit()

    try:
        backend = _build_backend(config)
    except Exception as exc:
        ui_error(f"Could not reach backend: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        rows = list(backend.list_models_with_latest_benchmark())
    finally:
        _close_backend(backend)

    if agent_mode:
        print(json.dumps(models_to_dict(rows), indent=2, default=str))
    else:
        ui_console.print(render_models_table(rows))


@hosts_app.command("list")
def cmd_hosts_list(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of the Rich table (agent mode)."),
    ] = False,
) -> None:
    """List registered fleet hosts + their freshest throughput.

    Each row shows the host's accelerator summary, ``last_seen`` age, the
    count of distinct models it has benchmarked, and its single freshest
    aggregate ``chunks_per_s``.  Hosts with no benchmarks still appear.
    """
    from corpus_forge.ui import agent as ui_agent

    agent_mode = json_out or ui_agent.is_agent_mode()
    config = _load_config_or_exit()

    try:
        backend = _build_backend(config)
    except Exception as exc:
        ui_error(f"Could not reach backend: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        rows = list(backend.list_hosts_with_latest_rate())
    finally:
        _close_backend(backend)

    if agent_mode:
        print(json.dumps(hosts_to_dict(rows), indent=2, default=str))
    else:
        ui_console.print(render_hosts_table(rows))


def _emit_plan_status(status: str, message: str, *, agent_mode: bool) -> None:
    """Print a degraded-shape status (empty / drained / sqlite); exit 0."""
    if agent_mode:
        print(json.dumps({"status": status, "message": message}))
    else:
        ui_info(message)


@hosts_app.command("plan")
def cmd_hosts_plan(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of the Rich table (agent mode)."),
    ] = False,
) -> None:
    """Recommend a host → lane assignment from fleet benchmarks (print-only).

    For each ACTIVE configured embedder (a "lane") this picks the host with
    the highest measured ``chunks_per_s`` for that model and projects a
    drain time from the lane's current backlog (``backlog / rate``).  A
    lane with no benchmark on any host is surfaced with a "run
    ``corpus-forge bench embed``" hint.  Greedy + deterministic — the
    RFC's stated non-solver approach (no automatic assignment; the
    operator writes the config).  **Read-only:** never writes, never
    mutates config.  Postgres-only (fleet federation).
    """
    from corpus_forge.ui import agent as ui_agent

    agent_mode = json_out or ui_agent.is_agent_mode()
    config = _load_config_or_exit()

    # Fleet planning is a Postgres-only federation feature; mirror the
    # sibling federation verbs' clean message on the single-machine backend.
    if getattr(config.backend, "kind", "postgres") == "sqlite":
        _emit_plan_status(
            "unsupported", "federation requires the postgres backend", agent_mode=agent_mode
        )
        raise typer.Exit(code=0)

    active = [ec for ec in config.embedders if ec.active]

    try:
        backend = _build_backend(config)
    except Exception as exc:
        ui_error(f"Could not reach backend: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        model_rows = list(backend.list_models_with_latest_benchmark())

        def _embedder_id_for(ec: Any) -> int | None:
            row = backend.find_embedder_row_by_name(ec.name)
            return None if row is None else int(row["id"])

        def _backlog_for(embedder_id: int, extensions: list[str] | None) -> int:
            return int(backend.count_chunks_missing_embedding(embedder_id, extensions=extensions))

        def _live_claims_for(embedder_id: int) -> int:
            return int(backend.count_live_claims(embedder_id))

        lanes = build_plan(
            embedders=active,
            model_rows=model_rows,
            embedder_id_for=_embedder_id_for,
            backlog_for=_backlog_for,
            live_claims_for=_live_claims_for,
        )
    finally:
        _close_backend(backend)

    # Degraded shapes (exit 0): no benchmarks at all / nothing left to plan.
    # A never-benchmarked registry row still carries a ``model_key`` but no
    # ``host_id``; "no benchmarks" means no row was ever *measured* on a host.
    if not any(row.get("host_id") is not None for row in model_rows):
        _emit_plan_status(
            "no_benchmarks",
            "no benchmarks yet — run `corpus-forge bench embed --all` on your hosts first",
            agent_mode=agent_mode,
        )
        raise typer.Exit(code=0)
    if all(int(lane.get("backlog") or 0) == 0 for lane in lanes):
        _emit_plan_status(
            "all_drained", "all lanes drained — nothing to plan", agent_mode=agent_mode
        )
        raise typer.Exit(code=0)

    if agent_mode:
        print(json.dumps(plan_to_dict(lanes), indent=2, default=str))
    else:
        ui_console.print(render_plan_table(lanes))


__all__ = [
    "accelerator_summary",
    "build_plan",
    "cmd_hosts_list",
    "cmd_hosts_plan",
    "cmd_models_list",
    "format_age",
    "format_duration",
    "hosts_app",
    "hosts_to_dict",
    "models_app",
    "models_to_dict",
    "plan_to_dict",
    "render_hosts_table",
    "render_models_table",
    "render_plan_table",
]
