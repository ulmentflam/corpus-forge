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
from datetime import UTC, datetime
from typing import Annotated, Any

import typer
from rich.table import Table

from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error

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


def _fmt_cold_start(value: Any, *, digits: int = 2) -> str:
    """Render an optional ``cold_start_s`` (model load + warmup) as ``"1.23s"``.

    ``None`` (old rows, or the passive ``embed-run`` path that never times
    a discrete cold start) → dash, mirroring :func:`_fmt_rate`.
    """
    if value is None:
        return _DASH
    try:
        return f"{float(value):.{digits}f}s"
    except (TypeError, ValueError):
        return _DASH


def _cell(value: Any) -> str:
    """Render an optional string cell; ``None`` / empty → dash."""
    if value is None or value == "":
        return _DASH
    return str(value)


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
    table.add_column("Cold start", justify="right")
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
            _fmt_cold_start(row.get("cold_start_s")),
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
                "cold_start_s": row.get("cold_start_s"),
                "transport": row.get("transport"),
                "device": row.get("device"),
                "source": row.get("source"),
                "measured_at": row.get("measured_at"),
                "age": format_age(row.get("measured_at"), now=now),
            }
        )
    return {"models": out_rows, "count": len(out_rows)}


# ── hosts list ─────────────────────────────────────────────────────────────


# ── Tailscale online markers (RFC fleet-4 item 5) ───────────────────────────

#: Marker glyphs for the optional "Tailscale" column. A host whose stored
#: ``tailscale_name`` matches a live, online peer renders :data:`_ONLINE`;
#: a host that is a known-but-offline peer OR has no matching peer renders
#: :data:`_OFFLINE`. The column is omitted entirely when Tailscale is
#: unavailable (``peer_status is None``), so the table is byte-identical to
#: the pre-fleet-4 layout on a non-tailnet box.
_ONLINE: str = "●"
_OFFLINE: str = "○"


def _host_online(row: dict, peer_status: dict[str, bool]) -> bool:
    """Whether this host's ``tailscale_name`` matches a live online peer.

    ``peer_status`` maps short MagicDNS name → online bool. A host with no
    ``tailscale_name``, or one whose name isn't in the map, is offline
    (``○``): it's either a known-but-offline peer or not a peer at all —
    both render the same "not reachable right now" glyph.
    """
    name = row.get("tailscale_name")
    if not isinstance(name, str) or not name:
        return False
    return peer_status.get(name, False)


def render_hosts_table(
    rows: list[dict],
    *,
    now: datetime | None = None,
    peer_status: dict[str, bool] | None = None,
) -> Table:
    """Build the Rich table for ``hosts list`` (one row per host).

    When ``peer_status`` is provided (Tailscale reachable — name → online
    map), a leading "Tailscale" column marks each row ●/○ by matching its
    ``tailscale_name``. When ``None`` (Tailscale unavailable / not
    configured), the column is omitted and the table is identical to the
    pre-fleet-4 layout.
    """
    show_ts = peer_status is not None
    table = Table(title="Fleet hosts", show_header=True)
    table.add_column("Host", style="accent.path")
    table.add_column("Hostname", style="muted")
    table.add_column("OS", style="muted")
    table.add_column("Accelerator")
    table.add_column("Models", justify="right")
    table.add_column("chunks/s", justify="right", style="accent.number")
    table.add_column("Last seen")
    if show_ts:
        table.add_column("Tailscale", justify="center")
    for row in rows:
        cells = [
            _cell(row.get("host_id")),
            _cell(row.get("hostname")),
            _cell(row.get("os")),
            accelerator_summary(row.get("accelerator")),
            _cell(row.get("models")),
            _fmt_rate(row.get("latest_chunks_per_s")),
            format_age(row.get("last_seen"), now=now),
        ]
        if peer_status is not None:
            cells.append(_ONLINE if _host_online(row, peer_status) else _OFFLINE)
        table.add_row(*cells)
    return table


def hosts_to_dict(
    rows: list[dict],
    *,
    now: datetime | None = None,
    peer_status: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Serialise ``hosts list`` rows to one JSON-able object (agent mode).

    Each host carries an ``online`` field: ``True``/``False`` when
    ``peer_status`` is provided (Tailscale reachable — matched against the
    host's ``tailscale_name``), or ``None`` when Tailscale is unavailable
    (no peer probe this run).
    """
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        online: bool | None
        online = _host_online(row, peer_status) if peer_status is not None else None
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
                "tailscale_name": row.get("tailscale_name"),
                "online": online,
            }
        )
    return {"hosts": out_rows, "count": len(out_rows)}


def _probe_peer_status() -> dict[str, bool] | None:
    """Probe live tailnet peers once → ``{name: online}``, or ``None``.

    Returns ``None`` (degrade silently — omit the marker column) when
    Tailscale is unavailable (:class:`TailscaleUnavailable`) or the
    tailnet reports no peers. Otherwise the short-name → online map the
    ``hosts list`` markers cross-reference against
    ``corpus.hosts.tailscale_name``.
    """
    from corpus_forge.net.tailscale import TailscaleUnavailable, peers

    try:
        live = peers()
    except TailscaleUnavailable:
        return None
    if not live:
        return None
    return {p.name: p.online for p in live}


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

    # RFC fleet-4 item 5 — probe live tailnet peers ONCE so each row can be
    # marked ●/○ by matching ``tailscale_name``. ``None`` (Tailscale
    # absent) omits the marker column / nulls the JSON ``online`` field.
    peer_status = _probe_peer_status()

    if agent_mode:
        print(json.dumps(hosts_to_dict(rows, peer_status=peer_status), indent=2, default=str))
    else:
        ui_console.print(render_hosts_table(rows, peer_status=peer_status))


__all__ = [
    "accelerator_summary",
    "cmd_hosts_list",
    "cmd_models_list",
    "format_age",
    "hosts_app",
    "hosts_to_dict",
    "models_app",
    "models_to_dict",
    "render_hosts_table",
    "render_models_table",
]
