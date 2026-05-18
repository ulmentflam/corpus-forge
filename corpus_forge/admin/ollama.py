"""``corpus-forge ollama ...`` admin verbs (Phase L Wave 7).

Thin HTTP client over the Ollama daemon's REST surface (``/api/tags``,
``/api/show``, ``/api/pull``) plus a Typer sub-app that the CLI mounts
at ``app.add_typer(ollama_app, name="ollama")``.

Why ``urllib.request`` instead of ``httpx`` / ``requests``: it's stdlib
(no new dependency), the streaming ``/api/pull`` endpoint is a simple
NDJSON-over-HTTP shape that ``urllib.request.urlopen`` handles line-by-
line, and tests can patch ``urllib.request.urlopen`` for hermetic
coverage.

Foreground / background convention: ``pull`` (long-running, downloads a
model that can be GB) honors the CLI-level ``--background`` flag.  All
other verbs (list / get / set-url / test) are short and stay
foreground.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Annotated

import typer
from rich.table import Table

from corpus_forge.admin.config import _set_config_value_atomic
from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok
from corpus_forge.ui.console import warn as ui_warn

logger = logging.getLogger(__name__)

ollama_app = typer.Typer(
    help="Manage the local Ollama daemon (list models, pull, test, point at a URL).",
    add_completion=False,
)

_LIST_TIMEOUT: float = 5.0
_PULL_TIMEOUT: float = 600.0


# ── HTTP helpers ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OllamaModel:
    """One model entry from ``/api/tags``."""

    name: str
    size: int  # bytes
    modified_at: str
    family: str


def _base_url() -> str:
    """Resolve the Ollama base URL from ``Config.load().ollama.base_url``.

    Imports are deferred so module import doesn't open the config file —
    keeps this module test-friendly under ``CF_CONFIG`` overrides.
    """

    from corpus_forge.config import Config

    cfg = Config.load()
    return str(cfg.ollama.base_url).rstrip("/")


def _request_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float):
    """One-shot JSON HTTP call; return the decoded body.

    Raises :class:`urllib.error.URLError` on network failures and
    :class:`json.JSONDecodeError` on malformed responses — both are
    caught by the verb wrappers and rendered via ``ui_error``.
    """

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def _stream_ndjson(url: str, *, body: dict, timeout: float):
    """Yield one decoded JSON dict per newline-delimited line from ``url``.

    Ollama's ``/api/pull`` streams NDJSON: each line is a self-contained
    JSON object reporting progress.  We consume line-by-line so the
    ``ollama pull`` verb can render a Rich progress bar without
    buffering the whole download.
    """

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.debug("ollama pull: skipped malformed line %r", line)


def fetch_tags(*, base_url: str | None = None, timeout: float = _LIST_TIMEOUT) -> list[OllamaModel]:
    """Return the list of installed Ollama models.

    Mirrors ``ollama list`` on the command line.  Models with no
    ``details.family`` fall back to ``"?"`` so the rendered table never
    has an empty cell.
    """

    base = (base_url or _base_url()).rstrip("/")
    payload = _request_json(f"{base}/api/tags", timeout=timeout)
    raw_models = payload.get("models", []) if isinstance(payload, dict) else []
    out: list[OllamaModel] = []
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        out.append(
            OllamaModel(
                name=str(entry.get("name", "")),
                size=int(entry.get("size", 0) or 0),
                modified_at=str(entry.get("modified_at", "")),
                family=str(details.get("family", "?") or "?"),
            )
        )
    return out


def show_model(model: str, *, base_url: str | None = None, timeout: float = _LIST_TIMEOUT) -> dict:
    """Return the ``/api/show`` payload for ``model``."""

    base = (base_url or _base_url()).rstrip("/")
    return _request_json(
        f"{base}/api/show",
        method="POST",
        body={"name": model},
        timeout=timeout,
    )


# ── Rendering helpers ───────────────────────────────────────────────────


_KIB = 1024
_KIB_F = 1024.0


def _human_bytes(n: int) -> str:
    """Render ``n`` bytes in a compact human-readable form (KB/MB/GB)."""

    if n < _KIB:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        size /= _KIB_F
        if size < _KIB_F:
            return f"{size:,.1f} {unit}"
    return f"{size:,.1f} PB"


def _render_models_table(models: list[OllamaModel]) -> Table:
    table = Table(title="Ollama models", title_style="h1", show_header=True)
    table.add_column("Name", style="accent.path")
    table.add_column("Size", justify="right", style="accent.number")
    table.add_column("Modified", style="muted")
    table.add_column("Family", style="muted")
    for m in models:
        table.add_row(m.name, _human_bytes(m.size), m.modified_at, m.family)
    return table


# ── Verbs ───────────────────────────────────────────────────────────────


@ollama_app.command("list")
def cmd_list(
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP timeout (seconds)."),
    ] = _LIST_TIMEOUT,
) -> None:
    """List installed Ollama models (``GET /api/tags``)."""

    try:
        models = fetch_tags(timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        ui_error(f"Could not reach Ollama: {exc}")
        raise typer.Exit(code=1) from exc

    if not models:
        ui_warn("No models installed on this Ollama daemon.")
        return

    ui_console.print(_render_models_table(models))


@ollama_app.command("get")
def cmd_get(
    model: Annotated[str, typer.Argument(help="Model tag (e.g. 'qwen2.5vl:7b').")],
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP timeout (seconds)."),
    ] = _LIST_TIMEOUT,
) -> None:
    """Show a model's details (``POST /api/show``)."""

    try:
        payload = show_model(model, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        ui_error(f"Could not reach Ollama: {exc}")
        raise typer.Exit(code=1) from exc

    # Data line — keep on stdout for piping into jq.
    print(json.dumps(payload, indent=2, sort_keys=True))


@ollama_app.command("pull")
def cmd_pull(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="Model tag to pull.")],
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP timeout (seconds)."),
    ] = _PULL_TIMEOUT,
) -> None:
    """Pull a model (``POST /api/pull``) with a live progress bar.

    Stays foreground by default; the global ``--background`` flag flips
    to a detached pull (see :mod:`corpus_forge.admin.foreground`).
    """

    background = bool(getattr(getattr(ctx, "obj", None), "background", False))
    if background:
        import sys

        from corpus_forge.admin.foreground import run_attached

        rc = run_attached(
            [sys.executable, "-m", "corpus_forge", "ollama", "pull", model],
            component="ollama-pull",
            background=True,
        )
        raise typer.Exit(code=rc)

    base = _base_url()
    url = f"{base}/api/pull"

    from corpus_forge.ui.progress import make_progress

    try:
        with make_progress(f"Pulling {model}", total=None) as progress:
            task = progress.add_task("download", total=None)
            for event in _stream_ndjson(url, body={"name": model}, timeout=timeout):
                _update_pull_progress(progress, task, event)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        ui_error(f"ollama pull failed: {exc}")
        raise typer.Exit(code=1) from exc

    ui_ok(f"Pulled {model}")


def _update_pull_progress(progress, task, event: dict) -> None:
    """Apply one ``/api/pull`` stream event to the Rich progress bar."""

    total = event.get("total")
    completed = event.get("completed")
    status = event.get("status", "")

    if isinstance(total, int) and total > 0:
        progress.update(task, total=int(total))
        if isinstance(completed, int):
            progress.update(task, completed=int(completed))
    if status:
        progress.update(task, description=str(status))


@ollama_app.command("set-url")
def cmd_set_url(
    url: Annotated[str, typer.Argument(help="New Ollama base URL.")],
    skip_probe: Annotated[
        bool,
        typer.Option(
            "--skip-probe",
            help="Skip the post-write reachability probe.",
        ),
    ] = False,
) -> None:
    """Persist ``ollama.base_url`` in the config and re-probe the daemon."""

    try:
        _set_config_value_atomic("ollama.base_url", url)
    except Exception as exc:  # config validation, IO, etc.
        ui_error(f"config set ollama.base_url failed: {exc}")
        raise typer.Exit(code=1) from exc

    ui_ok(f"ollama.base_url = {url}")

    if skip_probe:
        return

    try:
        models = fetch_tags(base_url=url, timeout=_LIST_TIMEOUT)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        ui_warn(f"Saved, but probe failed: {exc}")
        return
    ui_info(f"Probe ok — {len(models)} model(s) installed.")


@ollama_app.command("test")
def cmd_test(
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP timeout (seconds)."),
    ] = _LIST_TIMEOUT,
) -> None:
    """Embed ``hello world`` via the configured Ollama embedder + report timing.

    This is a lightweight smoke — when an Ollama embedder is configured,
    we call the embedder registry's ``encode`` path (same as
    ``embedder test``).  When no Ollama embedder is configured, we fall
    back to a tag probe so the verb still tells the user "the daemon
    answered".
    """

    # Try to find an Ollama-backed embedder via the config.
    try:
        from corpus_forge.config import Config

        cfg = Config.load()
    except Exception as exc:
        ui_error(f"Could not load config: {exc}")
        raise typer.Exit(code=1) from exc

    ollama_embedder = next(
        (
            e
            for e in cfg.embedders
            if e.provider == "openai" and e.base_url and "11434" in str(e.base_url)
        ),
        None,
    )

    if ollama_embedder is None:
        # No Ollama embedder wired — fall back to a tag probe so the user
        # still gets a smoke signal.
        ui_info("No Ollama-backed embedder configured — running a tag probe instead.")
        try:
            models = fetch_tags(timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ui_error(f"Probe failed: {exc}")
            raise typer.Exit(code=1) from exc
        ui_ok(f"Daemon answered — {len(models)} model(s) installed.")
        return

    # Round-trip via the embedder admin's ``test`` helper for parity.
    from corpus_forge.admin.embedder import run_embedder_smoke

    start = time.perf_counter()
    try:
        outcome = run_embedder_smoke(ollama_embedder.name)
    except Exception as exc:
        ui_error(f"Embedder test failed: {exc}")
        raise typer.Exit(code=1) from exc
    elapsed = time.perf_counter() - start
    ui_ok(f"Embedder {ollama_embedder.name}: dim={outcome.dim} in {elapsed * 1000:.0f} ms")


__all__ = [
    "OllamaModel",
    "fetch_tags",
    "ollama_app",
    "show_model",
]
