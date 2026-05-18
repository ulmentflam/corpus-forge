"""Command-line interface for corpus-forge."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .logging_config import init_logging
from .ui import error as ui_error
from .ui import info as ui_info
from .ui import ok as ui_ok
from .ui import warn as ui_warn
from .ui.console import console as ui_console

app = typer.Typer(
    name="corpus-forge",
    help="HF-format corpus + multi-embedder ingestion daemon for personal text and chat data.",
    add_completion=False,
)


@dataclass
class GlobalState:
    """State stashed in ``ctx.obj`` by the root callback.

    Wave 1 wires the flag values so downstream commands can read them
    without re-parsing argv.  Wave 7 reuses ``background``; Wave 9
    consumes ``agent`` once the detector ships.
    """

    verbose: int = 0
    quiet: bool = False
    no_color: bool = False
    light: bool = False
    background: bool = False
    agent: str = "auto"
    extras: dict[str, object] = field(default_factory=dict)


def _version_callback(value: bool) -> None:
    """Print the package version (+ optional newer-version notice) and exit."""
    if not value:
        return
    # Version output is a data line — keep it on stdout for piping.
    print(f"corpus-forge version {__version__}")
    # Phase I-11: daily PyPI ping. Strictly anonymous, 24h cached,
    # silent on offline / DNS / 5xx. Opt out via CF_NO_VERSION_CHECK=1.
    try:
        from .update import check_for_update
    except ImportError:
        raise typer.Exit() from None
    result = check_for_update()
    if result and result.notice():
        ui_info(result.notice())
    raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[  # noqa: ARG001 — typer callback signature
        bool,
        typer.Option(
            "--version",
            help="Print the package version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            min=0,
            max=2,
            help="Increase log verbosity (-v for INFO, -vv for DEBUG on stderr).",
        ),
    ] = 0,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress all but WARNING+ on stderr.",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color",
            help="Disable ANSI colors and use ASCII glyphs ([OK]/[WARN]/[ERR]).",
        ),
    ] = False,
    light: Annotated[
        bool,
        typer.Option(
            "--light",
            help="Swap the brand palette for light-themed terminals.",
        ),
    ] = False,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            "-b",
            help="Detach long-running side-effects from the terminal (Wave 7).",
        ),
    ] = False,
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Agent-mode hint: 'auto', 'off', 'claude-code', 'opencode', etc.",
        ),
    ] = "auto",
) -> None:
    """Root callback that wires ``--version`` + global flags onto the app entry point."""

    # Bootstrap logging before any subcommand runs so every command site
    # gets a configured ``corpus_forge`` logger.
    init_logging("cli", verbose=verbose >= 1, quiet=quiet)

    ctx.obj = GlobalState(
        verbose=verbose,
        quiet=quiet,
        no_color=no_color,
        light=light,
        background=background,
        agent=agent,
    )


migrate_app = typer.Typer(
    help="Database migration commands.",
    add_completion=False,
    invoke_without_command=True,
)
app.add_typer(migrate_app, name="migrate")


@migrate_app.callback()
def migrate_default(ctx: typer.Context) -> None:
    """Apply schema migrations (upgrade to head)."""
    if ctx.invoked_subcommand is None:
        from .schema.migrate import main

        main()


@migrate_app.command("revision")
def migrate_revision(
    message: Annotated[
        str,
        typer.Option("-m", "--message", help="Revision description."),
    ],
) -> None:
    """Create a new empty Alembic revision file."""
    import alembic.command as alembic_command

    from .schema.migrate import _build_alembic_config

    config = _build_alembic_config()
    alembic_command.revision(config, message=message, autogenerate=False)


@migrate_app.command("history")
def migrate_history() -> None:
    """Show revision history with current head indicator."""
    import alembic.command as alembic_command

    from .schema.migrate import _build_alembic_config

    config = _build_alembic_config()
    alembic_command.history(config, verbose=False, indicate_current=True)


@app.command()
def ingest(
    ctx: typer.Context,
    once: bool = typer.Option(False, "--once", help="Run one-shot ingestion pass"),
):
    """Discover, extract, chunk, and persist every document the configured sources expose.

    Walks every ``[[datasets.sources]]`` entry, dispatches each file through
    the extractor registry, runs the per-document chunker (selected by
    ``ExtractedDocument.metadata.chunker_hint``), and upserts the result via
    the configured backend. Embedding generation is decoupled — run
    ``corpus-forge embed`` afterwards to backfill vectors.

    With ``--once`` the pass exits after one full scan; without it the
    process stays resident and watches for filesystem changes (debounced
    by ``daemon.debounce_seconds``).
    """
    from .ingest import main

    _maybe_handle_drift(ctx)
    main(once=once)


@app.command()
def embed(
    ctx: typer.Context,
    embedder: str = typer.Option(..., "-e", help="Embedder name"),
    dataset: str | None = typer.Option(None, "-d", help="Dataset name"),
    limit: int | None = typer.Option(None, "-l", help="Max chunks to process"),
    image: bool = typer.Option(
        False,
        "--image",
        help="Embed images (uses the multi-modal embedder; Phase G P1).",
    ),
):
    """Backfill embeddings for chunks.

    By default, embed text chunks via the configured text embedder.
    With ``--image``, embed image-labeled chunks via a multi-modal
    embedder (CLIP family) and write to ``image_embeddings_<name>``.
    """
    from .embed import main

    _maybe_handle_drift(ctx)
    main(embedder=embedder, dataset=dataset, limit=limit, image=image)


@app.command()
def daemon():
    """[DEPRECATED] Use ``corpus-forge service start`` instead.

    Kept for one release as an alias for ``service start`` so existing
    CI and ops scripts don't break.  Internally forwards to
    :func:`corpus_forge.admin.service.start_daemon_foreground`, which
    runs the daemon main loop in-process with the same pid-file +
    SIGINT semantics as ``service start``.
    """
    from .admin.service import start_daemon_foreground

    ui_warn("`corpus-forge daemon` is deprecated; use `corpus-forge service start`.")
    rc = start_daemon_foreground()
    if rc != 0:
        raise typer.Exit(code=rc)


@app.command()
def version():
    """Print version and exit."""
    # Data line — stdout for piping.
    print(f"corpus-forge version {__version__}")


@app.command()
def setup(
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Read answers from CF_* env vars instead of prompting. Use for CI.",
    ),
    quick: bool = typer.Option(
        False,
        "--quick",
        envvar="CF_QUICK",
        help="Run the abbreviated 6-question setup with safe defaults.",
    ),
    config_dir: Path = typer.Option(
        Path.home() / ".config" / "corpus-forge",
        "--config-dir",
        help="Where to write config.toml + secrets.env.",
    ),
) -> None:
    """Post-install setup wizard.

    Walks the same question tree the shell installers use
    (``packaging/install/questions.toml``), validates endpoint reachability
    where possible, and renders ``config.toml`` + ``secrets.env`` under
    ``--config-dir`` (defaults to ``~/.config/corpus-forge/``).

    ``--quick`` runs an abbreviated 6-question subset (backend, optional
    Postgres DSN, Ollama URL, embedder model, dataset name, scan root)
    with a one-shot probe of Ollama's ``/api/tags`` to auto-suggest an
    embedder model. Useful for first-run setups; the full wizard
    remains the supported path for tuning OCR / Whisper / classifier
    chains.

    Re-running the wizard overwrites ``config.toml`` — back up local edits
    first. ``secrets.env`` is preserved if it already exists.
    """
    from .setup import run_non_interactive, run_quick, run_wizard

    # Banner: show in every interactive entry (full or quick). The
    # `--non-interactive` path stays silent — it's the machine-driven
    # surface and the banner would just pollute CI logs.
    if not non_interactive:
        from .ui import render_banner

        render_banner("corpus-forge", subtitle="Chat with your data.")

    # Use ASCII glyphs for status markers. Windows consoles default to
    # cp1252 / cp437 and choke on ✓ / ⚠ / ✗ at write time. The shell
    # installers use the fancy glyphs (their output goes to a POSIX
    # terminal); the Python CLI stays cross-platform safe.
    if non_interactive:
        if quick:
            config_path, secrets_path, answers = run_quick(
                config_dir=config_dir,
                interactive=False,
            )
            ui_ok(f"Wrote {config_path} (--quick, non-interactive)")
        else:
            config_path, secrets_path, answers = run_non_interactive(config_dir=config_dir)
            ui_ok(f"Wrote {config_path} (non-interactive)")
    elif quick:
        config_path, secrets_path, answers = run_quick(
            config_dir=config_dir,
            interactive=True,
        )
        ui_ok(f"Wrote {config_path} (--quick)")
    else:
        config_path, secrets_path, answers = run_wizard(config_dir=config_dir)
        ui_ok(f"Wrote {config_path}")

    if secrets_path.exists() and secrets_path.stat().st_size > 0:
        ui_ok(f"Secrets template at {secrets_path} — fill in real values before first use.")

    # Echo a short selection summary so the user can sanity-check.
    backend = answers.get("backend", "sqlite")
    embedder_summary = answers.get("embedder_model_id") or answers.get("embedder", "st")
    ui_info(f"backend={backend!r}  embedder={embedder_summary!r}")

    # Quick path: nudge the user if they skipped the scan root.
    if quick and not (answers.get("scan_root") or "").strip():
        ui_info(
            "No source root configured — add one later by editing "
            f"{config_path} (set datasets[0].sources)."
        )

    # Phase L Wave 5 — post-wizard drift check.  Best-effort; failure
    # silently no-ops so a fresh setup never blocks on telemetry.
    _maybe_handle_post_setup_drift(
        config_path=config_path,
        non_interactive=non_interactive,
    )


def _maybe_handle_post_setup_drift(
    *,
    config_path: Path,
    non_interactive: bool,
) -> None:
    """Run :func:`_handle_drift` against the freshly-written config.

    Under ``--non-interactive`` we only proceed if ``CF_BACKGROUND=1``
    is set (the documented "rerun in the background" knob for CI / quick
    setups). Otherwise the check would block waiting for input.
    """

    import os
    from contextlib import suppress

    from corpus_forge.config import Config

    background = os.environ.get("CF_BACKGROUND") in ("1", "true", "True")
    if non_interactive and not background:
        return
    try:
        config = Config.load(config_path=config_path)
    except (FileNotFoundError, ValueError):
        return
    backend = None
    with suppress(Exception):
        backend = _get_any_backend(config)
    if backend is None:
        return
    with suppress(Exception):
        _handle_drift(
            config,
            backend,
            background=background,
            non_interactive=non_interactive,
        )


@app.command()
def update(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the command that would run; don't execute."
    ),
    channel: str | None = typer.Option(
        None,
        "--channel",
        help="Force a specific install channel (uv-tool / pipx / brew / docker / source / pip).",
    ),
    skip_migrate: bool = typer.Option(
        False, "--skip-migrate", help="Skip the migrate step after upgrade."
    ),
    skip_doctor: bool = typer.Option(
        False, "--skip-doctor", help="Skip the doctor sanity check after upgrade."
    ),
) -> None:
    """Self-update corpus-forge via the detected install channel.

    Detects how the package was installed (uv tool / pipx / brew /
    docker / source / pip) by inspecting ``sys.executable`` + env
    hints, then runs the matching upgrade command. After a successful
    upgrade, runs ``corpus-forge migrate`` + ``corpus-forge doctor``
    automatically (skip with ``--skip-migrate`` / ``--skip-doctor``).
    """
    from .update import Channel, run_update

    ch: Channel | None = None
    if channel is not None:
        ch = channel  # type: ignore[assignment] — validated inside run_update
    result = run_update(channel=ch, dry_run=dry_run)
    ui_info(f"channel: {result.channel}")
    ui_info(f"command: {' '.join(result.command)}")
    if result.stdout:
        # Subprocess stdout is data; print raw to stdout for visibility.
        print(result.stdout)
    if result.stderr:
        # Subprocess stderr passes through as an info breadcrumb.
        ui_info(result.stderr)
    if not result.succeeded:
        raise typer.Exit(code=result.returncode or 1)
    if dry_run:
        return
    if not skip_migrate:
        ui_ok("Running migrations...")
        try:
            from .schema.migrate import main as migrate_main

            migrate_main()
        except Exception as exc:
            ui_warn(f"migrate failed (continue manually): {exc}")
    if not skip_doctor:
        from .doctor import run_doctor

        report = run_doctor()
        report.render_styled(ui_console)


@app.command()
def doctor(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a single JSON document (suppresses banner + human render).",
    ),
) -> None:
    """Run a post-install health check (Python, config, system deps).

    Default (human) render shows the corpus-forge banner + a colored
    status table on stderr. Exit code 0 iff every check passes
    (``OK`` or ``SKIP``).

    With ``--json``, the banner + human render are suppressed and a
    single JSON document is printed on stdout. Exit code maps from the
    aggregate summary:

    - ``"ok"``   → 0
    - ``"warn"`` → 2
    - ``"fail"`` → 1

    Read-only — never writes config, never changes state.
    """
    from .doctor import run_doctor

    report = run_doctor()

    if json_output:
        # Data line on stdout, no banner, no styled render. Use bare
        # print() — the same idiom every other JSON-emitting command
        # uses (see classify --json, rechunk --json, etc.).
        import json as _json

        print(_json.dumps(report.to_json(), default=str))
        summary = report._summary()
        if summary == "ok":
            return
        raise typer.Exit(code=1 if summary == "fail" else 2)

    # Human mode: banner + styled render.
    from .ui import render_banner

    render_banner("corpus-forge", subtitle="Chat with your data.")
    report.render_styled(ui_console)
    if not report.healthy:
        raise typer.Exit(code=1)


# ── Phase L Wave 6 — bug-report + logs subcommands ───────────────────────


@app.command("bug-report")
def bug_report_cmd(
    out: Path = typer.Option(
        None,
        "--out",
        help=(
            "Destination path for the bug-report bundle. Defaults to "
            "./corpus-forge-bugreport-<date>-<short-hash>.zip in the CWD."
        ),
    ),
    no_logs: bool = typer.Option(
        False,
        "--no-logs",
        help="Skip the logs/ section in the bundle.",
    ),
    no_db: bool = typer.Option(
        False,
        "--no-db",
        help="Skip the db_summary.json (no DB introspection).",
    ),
    no_zip: bool = typer.Option(
        False,
        "--no-zip",
        help="Write the staging directory uncompressed instead of a zip.",
    ),
) -> None:
    """Bundle a redacted diagnostic zip for an issue report.

    Produces a self-contained snapshot of corpus-forge's runtime state
    (doctor output, logs, config, DB counts, env) with secrets stripped.
    Default destination is ``./corpus-forge-bugreport-<date>-<hash>.zip``
    in the current directory; the prefilled GitHub issue URL is printed
    after the file is written.
    """

    from corpus_forge.diagnostics.bug_report import collect

    collect(
        out=out,
        include_logs=not no_logs,
        include_db=not no_db,
        zip_bundle=not no_zip,
    )


# Mount the ``logs`` sub-app (path / tail / clear).
from corpus_forge.diagnostics.logs import logs_app as _logs_app  # noqa: E402

app.add_typer(_logs_app, name="logs")


# ── Phase L Wave 7 — admin CRUD groups ───────────────────────────────────
#
# Five sub-apps for inspecting and editing the deployment without
# hand-editing ``config.toml``.  Each module owns its verb wiring;
# we just register the Typer apps onto the root.
from corpus_forge.admin.config import config_app as _config_app  # noqa: E402
from corpus_forge.admin.dataset import dataset_app as _dataset_app  # noqa: E402
from corpus_forge.admin.embedder import embedder_app as _embedder_app  # noqa: E402
from corpus_forge.admin.ollama import ollama_app as _ollama_app  # noqa: E402
from corpus_forge.admin.service import service_app as _service_app  # noqa: E402
from corpus_forge.admin.source import source_app as _source_app  # noqa: E402

app.add_typer(_config_app, name="config")
app.add_typer(_embedder_app, name="embedder")
app.add_typer(_ollama_app, name="ollama")
app.add_typer(_dataset_app, name="dataset")
app.add_typer(_source_app, name="source")
# Phase L Wave 8 — daemon lifecycle group.
app.add_typer(_service_app, name="service")


# ── export subcommand group ──────────────────────────────────────────────

export_app = typer.Typer(
    name="export",
    help="Export corpus data in various formats.",
    add_completion=False,
)
app.add_typer(export_app, name="export")


@export_app.command("chat")
def export_chat_cmd(
    dataset: Annotated[str, typer.Option("--dataset", "-d", help="Dataset name to export.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output file path.")],
    template: Annotated[
        str, typer.Option("--template", "-t", help="Chat template name.")
    ] = "chatml",
    format: Annotated[
        str, typer.Option("--format", "-f", help="Output format: jsonl or parquet.")
    ] = "jsonl",
    model_id: Annotated[
        str | None, typer.Option("--model-id", help="HF model_id (overrides --template).")
    ] = None,
    custom_jinja: Annotated[
        str | None, typer.Option("--custom-jinja", help="Inline Jinja override.")
    ] = None,
    push: Annotated[
        str | None, typer.Option("--push", help="HF dataset repo to push to after writing.")
    ] = None,
) -> None:
    """Export a dataset's chat conversations as templated HF-format rows."""
    from corpus_forge.export import export_chat

    export_chat(
        dataset=dataset,
        template=template,
        out_path=out,
        format=format,
        model_id=model_id,
        custom_jinja=custom_jinja,
        push=push,
    )
    ui_info(f"exported to {out}")


@export_app.command("feedback-pairs")
def export_feedback_pairs_cmd(
    dataset: Annotated[str, typer.Option("--dataset", "-d", help="Dataset name to export.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output file path.")],
    template: Annotated[
        str, typer.Option("--template", "-t", help="Chat template name.")
    ] = "chatml",
    format: Annotated[
        str, typer.Option("--format", "-f", help="Output format: jsonl or parquet.")
    ] = "jsonl",
    model_id: Annotated[str | None, typer.Option("--model-id", help="HF model_id.")] = None,
    custom_jinja: Annotated[
        str | None, typer.Option("--custom-jinja", help="Inline Jinja override.")
    ] = None,
) -> None:
    """Export feedback events as templated training rows."""
    from corpus_forge.export import export_feedback_pairs

    export_feedback_pairs(
        dataset=dataset,
        template=template,
        out_path=out,
        format=format,
        model_id=model_id,
        custom_jinja=custom_jinja,
    )
    ui_info(f"exported to {out}")


# ── sync subcommand group ────────────────────────────────────────────────


sync_app = typer.Typer(
    name="sync",
    help="Synchronise datasets with remote backends.",
    add_completion=False,
)
app.add_typer(sync_app, name="sync")


def _get_backend(config):
    from corpus_forge.backends.postgres import PostgresBackend

    return PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)


def _get_any_backend(config):
    """Return a backend instance for either backend kind.

    Phase L Wave 5 — the drift-detection hook needs to consult the
    backend regardless of ``backend.kind``.  Postgres uses
    :func:`_get_backend`; SQLite hits :class:`SQLiteBackend` directly.
    """

    if getattr(config, "backend", None) is None:
        return None
    kind = getattr(config.backend, "kind", "postgres")
    if kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        return SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    return _get_backend(config)


def _get_dataset_id(backend, name):
    rows = backend._execute("SELECT id FROM corpus.datasets WHERE name = %s", (name,))
    return rows[0]["id"] if rows else None


# ── Embedder-fingerprint drift dispatcher (Phase L Wave 5) ──────────────


def _is_non_interactive_runtime() -> bool:
    """Return True under CI / non-TTY where prompting would block forever."""

    import os
    import sys

    if os.environ.get("CF_NON_INTERACTIVE") in ("1", "true", "True"):
        return True
    return not sys.stdin.isatty()


def _state_dir_path():
    """Return ``<platformdirs cache>/corpus-forge/state`` (Wave 5)."""

    from pathlib import Path

    import platformdirs

    p = Path(platformdirs.user_cache_dir("corpus-forge")) / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _spawn_background_embed(drifts) -> None:
    """Detach a re-embed worker per drifting embedder."""

    import subprocess
    import sys

    state_dir = _state_dir_path()
    pid_file = state_dir / "embed-worker.pid"
    last_pid: int | None = None
    for d in drifts:
        proc = subprocess.Popen(
            [sys.executable, "-m", "corpus_forge", "embed", "-e", d.name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        last_pid = proc.pid
    if last_pid is not None:
        pid_file.write_text(str(last_pid), encoding="utf-8")
    ui_info(
        "Running in background — watch with: "
        "corpus-forge logs tail --component embed-worker --follow"
    )


def _run_foreground_embed(drifts) -> None:
    """Run the re-embed loop in-process for each drifting embedder."""

    from corpus_forge.embed import backfill_embedder

    for d in drifts:
        backfill_embedder(d.name)


def _handle_drift(config, backend, *, background: bool, non_interactive: bool) -> None:
    """End-to-end drift detection + prompt + action dispatch."""

    from corpus_forge.embedders._marker import (
        check_pending_or_skipped,
        clear_marker,
        mark_pending,
        mark_skipped,
    )
    from corpus_forge.embedders.drift_prompt import prompt_for_drift
    from corpus_forge.embedders.fingerprint import (
        compare_active,
        save_active_fingerprint,
    )

    drifts = compare_active(config, backend)
    if not drifts:
        return

    actionable = []
    for d in drifts:
        state = check_pending_or_skipped(d.name, d.fingerprint_now)
        if state == "skipped":
            continue
        actionable.append(d)
    if not actionable:
        return

    decision = prompt_for_drift(
        actionable,
        background=background,
        non_interactive=non_interactive,
    )

    if decision == "now":
        if background:
            _spawn_background_embed(actionable)
        else:
            _run_foreground_embed(actionable)
            try:
                save_active_fingerprint(config, backend)
            except Exception as exc:
                import logging as _logging

                _logging.getLogger("corpus_forge.embedders.fingerprint").debug(
                    "save_active_fingerprint failed: %s", exc
                )
            for d in actionable:
                clear_marker(d.name)
    elif decision == "later":
        for d in actionable:
            mark_pending(d.name, fp_was=d.fingerprint_was, fp_now=d.fingerprint_now)
    elif decision == "skip":
        for d in actionable:
            mark_skipped(d.name, fp_was=d.fingerprint_was, fp_now=d.fingerprint_now)


def _maybe_handle_drift(ctx: typer.Context) -> None:
    """Best-effort drift detection at the start of foreground commands.

    Loads the config + opens the backend; on any failure (config not
    written yet, backend unreachable, etc.) silently returns so we
    never block a working command on this telemetry layer.
    """

    from contextlib import suppress

    from corpus_forge.config import Config

    state = ctx.obj if isinstance(getattr(ctx, "obj", None), GlobalState) else GlobalState()
    try:
        config = Config.load()
    except FileNotFoundError:
        return

    backend = None
    with suppress(Exception):
        backend = _get_any_backend(config)
    if backend is None:
        return
    with suppress(Exception):
        _handle_drift(
            config,
            backend,
            background=bool(state.background),
            non_interactive=_is_non_interactive_runtime(),
        )


@sync_app.command()
def status(
    dataset: str = typer.Option(None, "-d", "--dataset", help="Dataset name"),
):
    """Show sync status per dataset."""
    from contextlib import suppress

    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_warn("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit() from None
    backend = None
    with suppress(Exception):
        backend = _get_backend(config)
    if backend is not None:
        try:
            for ds in config.datasets:
                if dataset and ds.name != dataset:
                    continue
                ds_id = _get_dataset_id(backend, ds.name)
                if not ds_id:
                    ui_warn(f"Dataset {ds.name}: not found")
                    continue
                pending = backend.pending_remote_revisions(ds_id, None, config.host_id(), limit=1)
                # Per-dataset sync status — data line on stdout so callers can pipe.
                print(
                    f"Dataset {ds.name}: sync={'enabled' if ds.sync_enabled else 'disabled'},"
                    f" pending={len(pending)}"
                )
        except Exception as exc:
            ui_error(f"{exc}")

    # Phase L Wave 5 — background embed-worker pid row.
    print(f"Background embed-worker: {_describe_embed_worker()}")


def _describe_embed_worker() -> str:
    """Return a one-line human description of the background embed worker."""

    import os
    from pathlib import Path

    import platformdirs

    base = Path(platformdirs.user_cache_dir("corpus-forge"))
    pid_path = base / "state" / "embed-worker.pid"
    log_path = base / "logs" / "embed-worker.log"

    if not pid_path.exists():
        return "none"
    try:
        pid_text = pid_path.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except (OSError, ValueError):
        return "none"
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return "none"
    except PermissionError:
        # Process exists but we can't signal — still alive.
        pass
    except OSError:
        return "none"
    return f"pid={pid}, log={log_path}"


@sync_app.command()
def pull(
    once: bool = typer.Option(False, "--once", help="Single pull cycle"),
    _continuous: bool = typer.Option(False, "--continuous", help="Continuous polling"),
    dataset: str = typer.Option(..., "-d", "--dataset", help="Dataset name"),
):
    """Pull remote changes for a dataset."""
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_warn("No configuration found.")
        raise typer.Exit() from None
    try:
        backend = _get_backend(config)
        from corpus_forge.ingest import _instantiate_source
        from corpus_forge.sync.echo import EchoSuppressor
        from corpus_forge.sync.pull import PullPipeline

        echo = EchoSuppressor()
        dataset_id = _get_dataset_id(backend, dataset)
        if not dataset_id:
            ui_warn(f"Dataset {dataset}: not found")
            raise typer.Exit()
        for ds in config.datasets:
            if ds.name != dataset:
                continue
            for src_cfg in ds.sources:
                source = _instantiate_source(src_cfg)
                pl = PullPipeline(backend, dataset_id, source.root, echo, config.host_id())
                if once:
                    count = pl.tick()
                    ui_ok(f"Pulled {count} revision(s)")
                else:
                    pl.start(
                        source.root,
                        poll_interval_s=config.daemon.sync_poll_interval_s,
                    )
                    ui_ok(f"Continuous pull started for {ds.name}/{src_cfg.plugin}")
    except Exception as exc:
        ui_error(f"{exc}")
        raise typer.Exit() from None


@sync_app.command()
def push(
    dataset: str = typer.Option(..., "-d", "--dataset", help="Dataset name"),
):
    """Force push pending changes."""
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_warn("No configuration found.")
        raise typer.Exit() from None
    try:
        backend = _get_backend(config)
        from corpus_forge.ingest import _instantiate_source
        from corpus_forge.sync.echo import EchoSuppressor
        from corpus_forge.sync.push import PushPipeline

        echo = EchoSuppressor()
        dataset_id = _get_dataset_id(backend, dataset)
        if not dataset_id:
            ui_warn(f"Dataset {dataset}: not found")
            raise typer.Exit()
        for ds in config.datasets:
            if ds.name != dataset:
                continue
            for src_cfg in ds.sources:
                source = _instantiate_source(src_cfg)
                pl = PushPipeline(backend, dataset_id, echo, config.host_id())
                pl.start(
                    source.root,
                    exclude_globs=src_cfg.exclude_globs or [],
                    debounce_seconds=0.1,
                )
                ui_ok(f"Push started for {ds.name}/{src_cfg.plugin}")
    except Exception as exc:
        ui_error(f"{exc}")
        raise typer.Exit() from None


@sync_app.command()
def resolve(
    conflict_file: str = typer.Argument(..., help="Path to conflict file"),
    strategy: str = typer.Option("ours", "--strategy", help="keep-local|keep-remote"),
):
    """Resolve a sync conflict."""
    if strategy == "merge":
        ui_warn(
            "Merge-based conflict resolution is not yet implemented. "
            "Use --strategy ours or --strategy theirs as a workaround.",
        )
        raise typer.Exit(code=1)

    path = Path(conflict_file)
    if strategy == "theirs":
        if path.exists():
            path.unlink()
            ui_ok(f"Removed conflict file {conflict_file} (keeping remote)")
        else:
            ui_warn(f"Nothing to resolve: {conflict_file} not found")
    elif strategy == "ours":
        if path.exists():
            dest = Path(str(path).split(".conflict-")[0])
            path.rename(dest)
            ui_ok(f"Restored local version to {dest}")
        else:
            ui_warn(f"Nothing to resolve: {conflict_file} not found")
    else:
        ui_error(f"Unknown resolution strategy: {strategy}")
        raise typer.Exit(code=1)


@sync_app.command()
def history(
    source_uri: str = typer.Argument(..., help="Source URI to show history for"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max revisions"),
):
    """Show revision history for a source."""
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_warn("No configuration found.")
        raise typer.Exit() from None
    try:
        backend = _get_backend(config)
        rows = backend._execute(
            """
            SELECT r.id, r.revision_number, r.content_hash,
                   r.author_host, r.is_tombstone, r.created_at
            FROM corpus.document_revisions r
            JOIN corpus.documents d ON d.id = r.document_id
            WHERE d.source_uri = %s
            ORDER BY r.revision_number DESC
            LIMIT %s
            """,
            (source_uri, limit),
        )
        if not rows:
            ui_info(f"No revisions for {source_uri}")
            return
        for row in rows:
            status = "deleted" if row["is_tombstone"] else "alive"
            # Per-revision history is data — keep on stdout for piping.
            print(
                f"#{row['revision_number']} id={row['id']} "
                f"hash={row['content_hash'][:12]}... "
                f"host={row['author_host']} {status} {row['created_at']}"
            )
    except Exception as exc:
        ui_error(f"{exc}")
        raise typer.Exit() from None


# ── eval subcommand group ────────────────────────────────────────────────
#
# The retrieval-eval harness is DUAL-USE.  Its primary value is as a
# corpus-quality signal during training-data prep — `eval corpus-quality`
# runs the same machinery over a user-provided held-out QA set so a low
# recall@20 catches a chunking/embedding regression BEFORE the corpus
# gets exported.  Retrieval correctness validation is the secondary use.
#
# R3 owns the [eval] extra in pyproject.  R4 wires `--rerank` to a
# real cross-encoder (configured via `Config.retrieval.reranker`); the
# default is `--no-rerank` so callers never accidentally trigger a
# 600 MB BAAI/bge-reranker-v2-m3 download.


eval_app = typer.Typer(
    name="eval",
    help=(
        "Retrieval evaluation + corpus-quality checks. "
        "Primary use: validate chunking/embedding quality on user-curated "
        "held-out QA pairs BEFORE exporting your training corpus. "
        "Secondary use: pin retrieval NDCG/MRR/Recall against a gold set."
    ),
    add_completion=False,
)
app.add_typer(eval_app, name="eval")


_BUNDLED_DATASETS = {
    "forge_self": "corpus_forge/eval/datasets/forge_self.jsonl",
}


def _resolve_dataset(dataset: str) -> Path:
    """Resolve a dataset name (bundled) or a filesystem path."""
    if dataset in _BUNDLED_DATASETS:
        bundled = Path(__file__).resolve().parent / "eval" / "datasets" / f"{dataset}.jsonl"
        if not bundled.exists():
            raise FileNotFoundError(
                f"bundled gold set {dataset!r} not found at {bundled}; "
                "did the install ship the datasets folder?"
            )
        return bundled
    p = Path(dataset).expanduser()
    if not p.exists():
        raise FileNotFoundError(
            f"gold set not found: {p} (and no bundled dataset named {dataset!r})"
        )
    return p


def _parse_csv_ints(raw: str) -> list[int]:
    """Parse '10,20,30' -> [10, 20, 30]; reject empty / non-int entries."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise typer.BadParameter(f"empty list: {raw!r}")
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise typer.BadParameter(f"non-int value in {raw!r}: {exc}") from exc


def _build_retriever_for_eval(
    config=None,
    *,
    fusion: str | None = None,
    alpha: float | None = None,
    reranker=None,
):
    """Wire a HybridRetriever to a backend + first-active embedder.

    When ``config`` is None (the CLI path), the config is loaded via
    :func:`_load_eval_config`, which applies the ``fusion`` / ``alpha``
    overrides.  Callers (and tests) may pass a pre-built config object to
    skip the on-disk lookup.

    ``reranker`` (R4): pre-built ``Reranker`` instance.  When non-None,
    threaded into ``HybridRetriever(..., reranker=reranker)``.  The
    rerank toggle stays on the per-call ``SearchOptions`` — wiring the
    reranker here just makes it available for the toggle to dispatch to.

    Reads ``config.backend.kind`` to instantiate the correct backend, then
    picks the first ``embedder.active`` entry (or first entry if none flag
    themselves active).  The retriever is constructed via
    ``HybridRetriever`` — same surface the MCP/search CLI (R5) will use.
    """
    from corpus_forge.embedders.registry import EmbedderRegistry
    from corpus_forge.retrieval import HybridRetriever

    if config is None:
        config = _load_eval_config(fusion=fusion, alpha=alpha)

    if config.backend.kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    else:
        from corpus_forge.backends.postgres import PostgresBackend

        backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)

    # Make sure the schema is up to date (idempotent).
    backend.migrate()

    embedders = list(config.embedders or [])
    if not embedders:
        raise typer.BadParameter(
            "no embedders configured; add at least one [[embedders]] entry to config.toml"
        )
    active = next((e for e in embedders if getattr(e, "active", True)), embedders[0])

    # Build the embedder via a local registry instance so we don't poison
    # the global one (multiple eval calls in the same process would otherwise
    # accumulate instances).
    reg = EmbedderRegistry()
    embedder = reg.register(
        name=active.name,
        provider=active.provider,
        model_id=active.model_id,
        dimension=active.dimension,
        normalized=getattr(active, "normalize", True),
        distance=getattr(active, "distance", "cosine"),
    )
    eid = backend.register_embedder(embedder)
    return HybridRetriever(backend=backend, embedder=embedder, embedder_id=eid, reranker=reranker)


def _build_reranker_from_config(config):
    """Instantiate a reranker from ``Config.retrieval.reranker`` (R4).

    Resolves the configured ``kind`` to a concrete class:

    - ``"cross_encoder"`` -> :class:`CrossEncoderReranker` (default;
      uses ``BAAI/bge-reranker-v2-m3`` unless overridden).
    - ``"ollama"`` -> :class:`OllamaReranker` (score-via-completion;
      requires a chat model ``model_id``).

    The constructor is LAZY — the heavy model load happens on the first
    ``warmup`` / ``rerank`` call, not here.  This keeps `--no-rerank`
    (the default) free of any model-download side-effects.
    """
    rr_cfg = config.retrieval.reranker
    if rr_cfg.kind == "cross_encoder":
        from corpus_forge.retrieval.rerank import CrossEncoderReranker

        return CrossEncoderReranker(
            model_id=rr_cfg.model_id,
            device=rr_cfg.device,
            batch_size=rr_cfg.batch_size,
            max_length=rr_cfg.max_length,
        )
    if rr_cfg.kind == "ollama":
        from corpus_forge.retrieval.rerank import OllamaReranker

        return OllamaReranker(model_id=rr_cfg.model_id)
    raise typer.BadParameter(f"unknown reranker kind: {rr_cfg.kind!r}")


def _build_reranker_for_eval(
    *,
    fusion: str | None = None,
    alpha: float | None = None,
) -> tuple[object, int]:
    """Load the eval config and build a reranker from it (R4).

    Returns ``(reranker, rerank_top_n)`` so callers don't have to
    re-load Config.  Raises ``FileNotFoundError`` from ``Config.load()``
    when no config is present; the caller is responsible for converting
    that to a typer exit.

    Tests patch this function whole when they want to assert "--rerank
    triggers reranker construction" without also stubbing Config.load.
    """
    config = _load_eval_config(fusion=fusion, alpha=alpha)
    return _build_reranker_from_config(config), config.retrieval.rerank_top_n


def _do_eval(
    dataset: str,
    k: str,
    metric: str,
    fusion: str | None,
    alpha: float | None,
    rerank: bool,
    json_out: Path | None,
) -> None:
    """Shared body for `eval retrieval` and `eval corpus-quality`."""
    from corpus_forge.eval.runner import dump_json, evaluate_retriever, report

    try:
        gold_path = _resolve_dataset(dataset)
    except FileNotFoundError as exc:
        ui_error(str(exc))
        raise typer.Exit(code=2) from None

    k_values = _parse_csv_ints(k)
    metrics_wanted = {m.strip().lower() for m in metric.split(",") if m.strip()}
    if not metrics_wanted:
        raise typer.BadParameter("--metric must list at least one of ndcg, mrr, recall")
    unknown = metrics_wanted - {"ndcg", "mrr", "recall"}
    if unknown:
        raise typer.BadParameter(f"unknown metric(s): {sorted(unknown)}")

    # Build the reranker FIRST (if requested) so we can pass it to the
    # retriever builder.  When --no-rerank is the default, we skip this
    # entirely — no surprise 600MB model downloads.
    reranker = None
    rerank_top_n = 50  # SearchOptions / RerankerConfig default
    if rerank:
        try:
            reranker, rerank_top_n = _build_reranker_for_eval(fusion=fusion, alpha=alpha)
        except FileNotFoundError:
            ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
            raise typer.Exit(code=2) from None

    # Build the retriever; Config.load is inside this builder so tests
    # that stub `_build_retriever_for_eval` don't need to also stub
    # `Config.load()`.
    retriever = _build_retriever_for_eval(fusion=fusion, alpha=alpha, reranker=reranker)

    metrics = evaluate_retriever(
        retriever,
        gold_path,
        k_values=k_values,
        rerank=rerank,
        rerank_top_n=rerank_top_n,
    )
    # The retrieval report is data — keep it on stdout so callers can
    # pipe / capture / diff.
    print(report(metrics))
    if json_out is not None:
        dump_json(metrics, json_out)
        ui_info(f"Wrote metrics -> {json_out}")


def _load_eval_config(fusion: str | None = None, alpha: float | None = None):
    """Load Config and apply CLI overrides; raise typer.Exit on missing config."""
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit(code=2) from None
    if fusion is not None:
        config.retrieval.fusion = fusion  # type: ignore[assignment]
    if alpha is not None:
        config.retrieval.alpha = alpha
    return config


@eval_app.command("retrieval")
def eval_retrieval(
    dataset: str = typer.Option(
        "forge_self", help="Bundled gold-set name (e.g. forge_self) or path to a .jsonl file"
    ),
    k: str = typer.Option("10,20", help="Comma-separated k cutoffs (e.g. 10,20)"),
    metric: str = typer.Option("ndcg,mrr,recall", help="Comma-separated metric subset"),
    fusion: str = typer.Option(None, help="Override fusion strategy: rrf|alpha"),
    alpha: float = typer.Option(None, help="Override alpha when fusion=alpha (0.0..1.0)"),
    rerank: bool = typer.Option(
        False,
        "--rerank/--no-rerank",
        help="Apply the configured cross-encoder reranker after fusion (opt-in).",
    ),
    json_out: Path = typer.Option(None, "--json", help="Write metrics as JSON to this path"),
):
    """Evaluate retrieval quality against a gold-labelled dataset.

    Validates that the current backend + embedder + retriever stack
    reaches its pinned NDCG@10 baseline against a curated gold set.
    The bundled `forge_self` set ships with corpus-forge and pins
    retrieval correctness across phases.
    """
    _do_eval(dataset, k, metric, fusion, alpha, rerank, json_out)


@eval_app.command("corpus-quality")
def eval_corpus_quality(
    dataset: str = typer.Option(
        ..., help="Path to a .jsonl of held-out QA pairs covering YOUR training corpus"
    ),
    k: str = typer.Option("10,20", help="Comma-separated k cutoffs"),
    metric: str = typer.Option("ndcg,mrr,recall", help="Comma-separated metric subset"),
    fusion: str = typer.Option(None, help="Override fusion strategy: rrf|alpha"),
    alpha: float = typer.Option(None, help="Override alpha when fusion=alpha (0.0..1.0)"),
    rerank: bool = typer.Option(
        False,
        "--rerank/--no-rerank",
        help="Apply the configured cross-encoder reranker after fusion (opt-in).",
    ),
    json_out: Path = typer.Option(None, "--json", help="Write metrics as JSON to this path"),
):
    """Validate corpus chunking + embedding quality for training-data prep.

    The harness is the same as ``eval retrieval``; the framing is
    different.  Run this AFTER ingesting + embedding and BEFORE exporting
    your training corpus.  Low recall@20 is the canonical chunking-
    regression signal: your model will starve on under-recalled context.
    Catch it here, not at training time.
    """
    _do_eval(dataset, k, metric, fusion, alpha, rerank, json_out)


# ── mcp subcommand group (Phase R5) ───────────────────────────────────────


mcp_app = typer.Typer(
    name="mcp",
    help=(
        "Model Context Protocol server.  Exposes corpus-forge's retrieval "
        "stack to MCP-compatible clients (Claude Desktop, mcp-cli, ...) "
        "over stdio."
    ),
    add_completion=False,
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="Transport for the MCP server.  Only `stdio` is supported in v1.",
    ),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Optional default dataset name to scope tool calls when not specified.",
    ),
) -> None:
    """Launch the corpus-forge MCP server.

    The server registers three tools: ``search`` (hybrid retrieval),
    ``get_chunk`` (chunk lookup by id), and ``list_datasets`` (catalogue
    enumeration).  See ``corpus_forge.mcp.server`` for the schemas.
    """
    from corpus_forge.mcp.transport import Transport

    try:
        chosen = Transport(transport)
    except ValueError as exc:
        valid = ", ".join(t.value for t in Transport)
        raise typer.BadParameter(f"unknown transport {transport!r}; valid: {valid}") from exc

    # Wave 2/3 wires the server through; Wave 1 only pins help + flag
    # validation.  When the server module lands the body below will
    # dispatch to corpus_forge.mcp.server.serve_stdio(...).
    if chosen is Transport.STDIO:
        from corpus_forge.mcp.server import serve_stdio

        serve_stdio(default_dataset=dataset)
    else:  # pragma: no cover — defensive; the enum currently has only STDIO
        raise typer.BadParameter(f"transport {chosen.value!r} not implemented")


# ── top-level `search` command (Phase R5) ────────────────────────────────


def _hit_to_jsonable(hit) -> dict:
    """Serialize a ``Hit`` (frozen dataclass) to a JSON-safe dict."""
    return {
        "chunk_id": int(hit.chunk_id),
        "score": float(hit.score),
        "text": hit.text,
        "document_id": getattr(hit, "document_id", None),
        "source_uri": getattr(hit, "source_uri", None),
        "title": getattr(hit, "title", None),
        "dataset_id": int(hit.dataset_id),
        "metadata": dict(getattr(hit, "metadata", {}) or {}),
        "source": getattr(hit, "source", "fused"),
    }


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Natural-language search query."),
    k: int = typer.Option(10, "--k", help="Number of hits to return."),
    dataset: str = typer.Option(None, "--dataset", "-d", help="Optional dataset name filter."),
    fusion: str = typer.Option(None, "--fusion", help="Fusion strategy override: rrf|alpha."),
    alpha: float = typer.Option(
        None, "--alpha", help="Alpha-fusion weight (0.0..1.0); only used when fusion=alpha."
    ),
    rerank: bool = typer.Option(
        False,
        "--rerank/--no-rerank",
        help="Apply the configured cross-encoder reranker after fusion (opt-in; default off).",
    ),
    json_out: Path = typer.Option(
        None,
        "--json",
        help="Write {'query': ..., 'hits': [...]} JSON to this path.",
    ),
) -> None:
    """Search the corpus over the configured backend.

    Runs a hybrid (dense + lexical) search via the same retriever stack
    that powers ``corpus-forge eval`` and the MCP server (Phase R5).
    Default-off reranker — pass ``--rerank`` to opt in.
    """
    import json as _json

    from corpus_forge.retrieval.types import SearchOptions

    # Build the reranker FIRST (lazy; default-off) so we can pass it to
    # the retriever builder.  Mirrors `eval`'s wiring exactly.
    reranker = None
    rerank_top_n = 50  # matches SearchOptions / RerankerConfig default
    if rerank:
        try:
            reranker, rerank_top_n = _build_reranker_for_eval(fusion=fusion, alpha=alpha)
        except FileNotFoundError:
            ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
            raise typer.Exit(code=2) from None

    retriever = _build_retriever_for_eval(fusion=fusion, alpha=alpha, reranker=reranker)

    fusion_resolved: str = fusion if fusion is not None else "rrf"
    if fusion_resolved not in ("rrf", "alpha"):
        raise typer.BadParameter(f"--fusion must be 'rrf' or 'alpha'; got {fusion_resolved!r}")
    options = SearchOptions(
        k=k,
        dataset=dataset,
        fusion=fusion_resolved,  # type: ignore[arg-type]
        alpha=alpha if alpha is not None else 0.5,
        rerank=rerank,
        rerank_top_n=rerank_top_n,
    )

    hits = retriever.search(query, options)

    if json_out is not None:
        payload = {
            "query": query,
            "hits": [_hit_to_jsonable(h) for h in hits],
        }
        json_out.write_text(_json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        ui_info(f"Wrote {len(hits)} hits -> {json_out}")
        return

    if not hits:
        ui_info(f"No hits for {query!r}.")
        return

    for rank, hit in enumerate(hits, start=1):
        title = getattr(hit, "title", None) or ""
        source_uri = getattr(hit, "source_uri", None) or ""
        chunk_id = hit.chunk_id
        score = hit.score
        text = getattr(hit, "text", "") or ""
        # Truncate body to keep the terminal output tidy.
        _MAX_BODY_CHARS = 240
        body = text if len(text) <= _MAX_BODY_CHARS else text[: _MAX_BODY_CHARS - 3] + "..."
        header_bits = [f"#{rank}", f"chunk={chunk_id}", f"score={score:.4f}"]
        if title:
            header_bits.append(f"title={title!r}")
        if source_uri:
            header_bits.append(source_uri)
        # Search hits are data — stdout for piping.
        print("  ".join(header_bits))
        print(f"    {body}")
        print("")


# ── classify command (Phase E / C-05) ───────────────────────────────────


def _build_backend_from_config(config):
    """Construct the configured backend (sqlite|postgres) and migrate.

    Lazy-imports so a missing optional extra surfaces at call-time, not
    at module-load time.
    """
    if config.backend.kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    else:
        from corpus_forge.backends.postgres import PostgresBackend

        backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
    backend.migrate()
    return backend


@app.command("classify")
def classify(
    dataset: list[str] = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Restrict to one or more datasets (repeatable).",
    ),
    reclassify: bool = typer.Option(
        False,
        "--reclassify",
        help=(
            "Force re-classification of all documents "
            "(default: skip docs that already carry a classifier:* class label)."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the plan without writing any rows.",
    ),
    limit: int = typer.Option(
        None,
        "--limit",
        help="Stop after processing N documents.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit one JSON object per processed document.",
    ),
    classifier: str = typer.Option(
        None,
        "--classifier",
        help="Force a single classifier (bypass the chain).",
    ),
) -> None:
    """Walk documents and assign content-class labels via the classifier chain.

    The default chain (``classifier.chain = ["rule", "llm"]`` in
    ``config.toml``) starts with the stdlib rule classifier
    (microseconds/doc). When the rule classifier's confidence falls
    below ``classifier.escalation_threshold`` (default 0.4), the LLM
    classifier (Ollama qwen2.5:7b-instruct by default; ~5-10 s/doc on
    M-series) takes over. High-confidence rule outputs short-circuit
    the LLM call entirely — the cost-guard preflight prints a worst-
    case estimate so you know what you're paying for before the run
    starts.

    Idempotent: documents that already carry a ``namespace='class'``
    label with ``source LIKE 'classifier:%'`` are skipped unless
    ``--reclassify`` is set.

    The ``source`` column distinguishes ``classifier:rule`` from
    ``classifier:llm`` so downstream consumers can audit which
    classifier produced each label.
    """
    import json as _json

    from corpus_forge.classifiers import register_default_classifiers
    from corpus_forge.classifiers.registry import ClassifierRegistry
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit(code=2) from None

    # Build the chain. ``--classifier`` filters down to a single named
    # classifier (helpful for debugging — e.g. force rule even when LLM
    # is configured).
    try:
        registry: ClassifierRegistry = register_default_classifiers(config.classifier)
    except ValueError as exc:
        ui_error(f"Classifier-config error: {exc}")
        raise typer.Exit(code=2) from None

    if classifier is not None:
        filtered = ClassifierRegistry()
        target = registry.get(classifier)
        if target is None:
            ui_error(
                f"--classifier {classifier!r} is not in the configured chain "
                f"({registry.names()}). Available classifiers: ['rule', 'llm'].",
            )
            raise typer.Exit(code=2)
        filtered.register(target)
        registry = filtered

    threshold = config.classifier.escalation_threshold
    backend = _build_backend_from_config(config)

    # Resolve dataset filter(s). When --dataset is not supplied, iterate
    # every dataset by passing ``None`` to the backend helper.
    if dataset:
        dataset_ids: list[int | None] = []
        for name in dataset:
            ds_id = backend.find_dataset_id_by_name(name)
            if ds_id is None:
                ui_error(f"Dataset not found: {name}")
                raise typer.Exit(code=2)
            dataset_ids.append(ds_id)
    else:
        dataset_ids = [None]

    # Cost-guard preflight — count what we're about to do.
    total_to_process = 0
    for ds_id in dataset_ids:
        total_to_process += sum(
            1
            for _ in backend.iter_documents_for_classification(ds_id, include_classified=reclassify)
        )

    chain_names = registry.names()
    ui_info(f"Classifying {total_to_process} document(s) via chain={chain_names}.")
    if "llm" in chain_names:
        ui_info(
            f"Worst-case LLM cost: up to {total_to_process} LLM call(s) "
            f"(~5-10 s/doc on qwen2.5:7b-instruct, M-series). "
            f"Rule classifier short-circuits high-confidence docs "
            f"(confidence >= {threshold:.2f}).",
        )
    else:
        ui_info("Rule classifier only — microseconds per document.")

    processed = 0
    applied = 0
    for ds_id in dataset_ids:
        for doc in backend.iter_documents_for_classification(ds_id, include_classified=reclassify):
            if limit is not None and processed >= limit:
                break
            outcome = registry.classify(doc, threshold=threshold)
            if outcome is None:
                # Every classifier returned None — nothing to write.
                processed += 1
                continue
            winner_name, result = outcome
            source = f"classifier:{winner_name}"

            write_now = not dry_run
            if write_now:
                backend.apply_label(
                    "document",
                    doc.document_id,
                    "class",
                    result.value,
                    source=source,
                    confidence=result.confidence,
                )
                applied += 1

            if json_out:
                payload = {
                    "doc_id": doc.document_id,
                    "source_uri": doc.source_uri,
                    "class": result.value,
                    "confidence": float(result.confidence),
                    "rationale": result.rationale,
                    "applied": bool(write_now),
                    "classifier": winner_name,
                }
                # JSON line — stdout, no markup mangling.
                print(_json.dumps(payload, ensure_ascii=False))
            else:
                action = "would assign" if dry_run else "assigned"
                # Per-doc human result — stdout for piping.
                print(
                    f"{doc.source_uri} -> {action} class={result.value} "
                    f"({result.confidence:.2f}) [{winner_name}: {result.rationale}]"
                )
            processed += 1
        if limit is not None and processed >= limit:
            break

    ui_info(f"Processed {processed} document(s); applied {applied}.")


# ── rechunk command (Phase F / F-04) ────────────────────────────────────


#: Expected metadata-key signature for each class-mapped chunker. When
#: this key is missing from ALL chunks of a document, the rechunk CLI
#: knows the document is still on the pre-Phase-F chunker output and
#: must be rechunked — even if the chunk text happens to match (small
#: documents that fit in a single chunk hit this case).
#:
#: ``None`` means "no required signature — text-equality is sufficient
#: for idempotency".
_CLASS_METADATA_SIGNATURE: dict[str, str | None] = {
    "code": "byte_range",  # CodeChunker stamps byte_range (also 'kind'/'name' when AST hits)
    "chat": None,
    "reference": None,
    "book": "cdc_fingerprint",
    "textbook": "cdc_fingerprint",
    "paper": "cdc_fingerprint",
    "article": "cdc_fingerprint",
    "note": "cdc_fingerprint",
    "other": "cdc_fingerprint",
}


def _expected_metadata_signature(class_value: str) -> str | None:
    """Return the required metadata key for the given class, or None."""
    return _CLASS_METADATA_SIGNATURE.get(class_value)


def _all_prior_chunks_have_key(backend, document_id: int, key: str) -> bool:
    """Return True iff every stored chunk of ``document_id`` has ``key``
    set in its metadata. Used by the rechunk idempotency check.

    Uses ``get_document_chunk_metadatas`` if the backend exposes it,
    otherwise falls back to a defensive ``False`` so the rechunk pass
    always runs (correctness > efficiency on the fallback path).
    """
    if not hasattr(backend, "get_document_chunk_metadatas"):
        return False
    metadatas = backend.get_document_chunk_metadatas(document_id)
    if not metadatas:
        return False
    return all(bool((md or {}).get(key)) for md in metadatas)


def _class_from_format_labels(labels: "list[tuple[str, str]]") -> str | None:
    """Return the ``class=<value>`` label's value, or ``None`` if absent.

    Tied-break: a document may carry multiple ``class=*`` labels (e.g.
    one from ``classifier:rule`` and one from ``classifier:llm``). We
    take the last one — ``apply_label`` is INSERT-order-stable and the
    ``classify`` CLI runs the LLM AFTER the rule classifier when both
    are in the chain, so the last entry reflects the final winning
    decision in the most common workflow.
    """
    candidates = [v for ns, v in labels if ns == "class"]
    if not candidates:
        return None
    return candidates[-1]


@app.command("rechunk")
def rechunk(
    dataset: list[str] = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Restrict to one or more datasets (repeatable).",
    ),
    limit: int = typer.Option(
        None,
        "--limit",
        help="Stop after processing N documents.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the plan without writing any chunks.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit one JSON object per processed document.",
    ),
) -> None:
    """Re-chunk classified documents using the class-mapped chunker (Phase F).

    Walks every document that carries a ``namespace='class'`` label
    (Phase E classifier output) and re-runs the chunker pass using the
    chunker mapped to that class:

    - ``class=code`` -> :class:`CodeChunker` (tree-sitter or byte-line fallback)
    - ``class=chat`` -> :class:`ConversationChunker`
    - ``class=reference`` -> :class:`PassthroughChunker`
    - ``class=book`` / ``textbook`` / ``paper`` / ``article`` / ``note`` /
      ``other`` -> :class:`CDCChunker` (FastCDC rolling hash)

    The Phase C BUG-3 ``content_hash`` chunk-reuse path inside
    :meth:`StorageBackend.upsert_document` preserves embeddings for any
    chunks that come out byte-identical to their pre-rechunk peers.

    Idempotent: when the prospective new chunk-text list matches the
    stored chunk-text list exactly, the upsert is skipped entirely (no
    DB writes).

    Pre-requisite: run ``corpus-forge classify`` first so documents
    have ``class=*`` labels. Unclassified documents are skipped.
    """
    import json as _json

    from corpus_forge.config import Config
    from corpus_forge.ingest import ChunkerDispatcher

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit(code=2) from None

    backend = _build_backend_from_config(config)
    dispatcher = ChunkerDispatcher()

    # Resolve dataset filter(s). When --dataset is not supplied, iterate
    # every dataset by passing ``None``.
    if dataset:
        dataset_ids: list[int | None] = []
        for name in dataset:
            ds_id = backend.find_dataset_id_by_name(name)
            if ds_id is None:
                ui_error(f"Dataset not found: {name}")
                raise typer.Exit(code=2)
            dataset_ids.append(ds_id)
    else:
        dataset_ids = [None]

    processed = 0
    applied = 0
    skipped_noop = 0
    skipped_unclassified = 0

    for ds_id in dataset_ids:
        for doc in backend.iter_documents_for_classification(ds_id, include_classified=True):
            if limit is not None and processed >= limit:
                break

            class_value = _class_from_format_labels(doc.format_labels)
            if class_value is None:
                # Defensive — iter helper yields every document when
                # include_classified=True. Skip docs without a class.
                skipped_unclassified += 1
                continue

            # Resolve the chunker for this class. Unknown classes raise
            # ValueError — surface as a warning and skip rather than fail
            # the whole run.
            try:
                chunker = dispatcher.for_class(class_value)
            except ValueError as exc:
                ui_warn(f"Skipping {doc.source_uri}: {exc}")
                continue

            # Run the chunker. We feed ``doc.text`` directly; the chunker
            # contract is ``chunk(text: str) -> list[TextChunk]`` for all
            # prose-flavoured chunkers. CodeChunker also accepts that
            # surface (it just lacks language/relative_path metadata —
            # the byte-line fallback still produces valid chunks).
            try:
                new_chunks = chunker.chunk(doc.text)
            except Exception as exc:  # pragma: no cover — defensive
                ui_warn(f"Chunker error on {doc.source_uri}: {exc}; skipping.")
                continue

            # Idempotency check: skip the upsert when the stored chunks
            # already match the prospective new chunks — both in text
            # AND in chunker signature.
            #
            # Text-only comparison is insufficient: a small markdown
            # doc whose entire body fits in a single positional chunk
            # is also a single CDC chunk (same text) — but the chunk
            # metadata differs (CDC stamps ``cdc_fingerprint`` /
            # ``byte_range``; markdown stamps nothing). Skipping based
            # on text alone would leave the chunk metadata stuck in
            # the old shape.
            prior_texts = backend.get_document_chunk_texts(doc.document_id)
            new_texts = [c.text for c in new_chunks]
            # Expected metadata-key signature for the class:
            # - CDC chunks -> ``cdc_fingerprint``
            # - code chunks -> ``kind`` (tree-sitter) or ``byte_range`` (byte fallback)
            # - everything else has no required signature -> text-only check.
            expected_key = _expected_metadata_signature(class_value)
            new_has_signature = expected_key is None or all(
                (c.metadata or {}).get(expected_key) for c in new_chunks
            )
            prior_has_signature = expected_key is None or _all_prior_chunks_have_key(
                backend, doc.document_id, expected_key
            )
            if prior_texts == new_texts and (
                expected_key is None or (new_has_signature and prior_has_signature)
            ):
                skipped_noop += 1
                processed += 1
                if json_out:
                    print(
                        _json.dumps(
                            {
                                "doc_id": doc.document_id,
                                "source_uri": doc.source_uri,
                                "class": class_value,
                                "applied": False,
                                "reason": "noop-identical-chunks",
                                "chunk_count": len(new_texts),
                            },
                            ensure_ascii=False,
                        )
                    )
                else:
                    print(
                        f"{doc.source_uri} -> noop ({len(new_texts)} chunks unchanged) "
                        f"[class={class_value}]"
                    )
                continue

            if not dry_run:
                # Use the dedicated chunk-replacement helper. It mirrors
                # the Phase C BUG-3 ``content_hash`` chunk-reuse path
                # inside :meth:`upsert_document` (embeddings survive
                # where chunks come out byte-identical) without touching
                # the document row — we only want to swap the chunk
                # decomposition, not modify text / title / metadata.
                backend.replace_document_chunks(
                    document_id=doc.document_id,
                    chunks=new_chunks,
                )
                applied += 1

            if json_out:
                print(
                    _json.dumps(
                        {
                            "doc_id": doc.document_id,
                            "source_uri": doc.source_uri,
                            "class": class_value,
                            "applied": not dry_run,
                            "chunk_count": len(new_texts),
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                action = "would rechunk" if dry_run else "rechunked"
                print(
                    f"{doc.source_uri} -> {action} into {len(new_texts)} chunks "
                    f"[class={class_value}]"
                )
            processed += 1
        if limit is not None and processed >= limit:
            break

    ui_info(
        f"Processed {processed} document(s); applied {applied}; "
        f"noop {skipped_noop}; unclassified {skipped_unclassified}.",
    )


# ── enrich command (Phase H / H-06) ──────────────────────────────────────


@app.command("enrich")
def enrich(
    dataset: list[str] = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Restrict to one or more datasets (repeatable).",
    ),
    reclassify_on_model_change: bool = typer.Option(
        False,
        "--reclassify-on-model-change",
        help=(
            "Force re-enrichment even when the chunk already carries an "
            "enrichment record (idempotency is normally model-tag-based; "
            "use this to override after a prompt tweak)."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the plan without writing any rows.",
    ),
    limit: int = typer.Option(
        None,
        "--limit",
        help="Stop after enriching N chunks.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit one JSON object per enriched chunk.",
    ),
    backend: str = typer.Option(
        None,
        "--backend",
        help="Force a specific enricher backend (qwen-local | qwen-remote). "
        "Bypasses the config chain.",
    ),
) -> None:
    """Walk ``class=code`` chunks and attach LLM-generated enrichments.

    Each chunk picks up a ``chunks.metadata.enrichment`` record with a
    synthesized docstring, semantic summary, referenced symbols, model
    tag, and confidence. Idempotent on the model tag — chunks already
    enriched with the *current* model are skipped unless
    ``--reclassify-on-model-change`` is set.

    Cost-guard preflight: the CLI counts the candidate chunks and prints
    an estimated wall-clock budget. qwen3.6:35b-a3b-instruct (the
    default) runs ~3-8 s/chunk on M-series for typical code chunks —
    the MoE active-param count keeps it fast.
    """
    import json as _json

    from corpus_forge.config import Config
    from corpus_forge.enrichers import get_active_enricher
    from corpus_forge.enrichers.base import EnricherError, EnricherUnavailableError

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit(code=2) from None

    # Optional --backend override: build the enricher directly without
    # consulting config.code_enricher.backend.
    if backend is not None:
        if backend == "qwen-local":
            from corpus_forge.enrichers.qwen_local import QwenCoderLocal

            enricher = QwenCoderLocal(
                model=config.code_enricher.local_model,
                llm_url=str(config.code_enricher.local_url).rstrip("/"),
                timeout_s=config.code_enricher.timeout_s,
                temperature=config.code_enricher.temperature,
            )
        elif backend == "qwen-remote":
            from corpus_forge.enrichers.qwen_remote import QwenCoderRemote

            enricher = QwenCoderRemote(
                api_shape=config.code_enricher.remote_api_shape,
                model=config.code_enricher.remote_model,
                base_url=str(config.code_enricher.remote_url).rstrip("/"),
                api_key=config.resolve_code_enricher_api_key(),
                timeout_s=config.code_enricher.timeout_s,
                temperature=config.code_enricher.temperature,
            )
        else:
            ui_error(
                f"--backend {backend!r} is not a known enricher backend. "
                f"Available: ['qwen-local', 'qwen-remote'].",
            )
            raise typer.Exit(code=2)
    else:
        try:
            enricher = get_active_enricher(config)
        except EnricherUnavailableError as exc:
            ui_error(f"Enricher unavailable: {exc}")
            raise typer.Exit(code=2) from None

    if enricher.name == "noop":
        ui_error(
            "Code enricher is disabled (config.code_enricher.backend == 'none'). "
            "Set backend to 'local' or 'remote' in config.toml, or pass --backend "
            "qwen-local / --backend qwen-remote to force a specific backend.",
        )
        raise typer.Exit(code=2)

    storage = _build_backend_from_config(config)

    # The "model tag" used for idempotency depends on which concrete
    # enricher we ended up with — local vs remote may target different
    # model names.
    if backend == "qwen-remote" or (backend is None and config.code_enricher.backend == "remote"):
        model_tag = config.code_enricher.remote_model
    else:
        model_tag = config.code_enricher.local_model

    # Resolve dataset filter(s).
    if dataset:
        dataset_ids: list[int | None] = []
        for name in dataset:
            ds_id = storage.find_dataset_id_by_name(name)
            if ds_id is None:
                ui_error(f"Dataset not found: {name}")
                raise typer.Exit(code=2)
            dataset_ids.append(ds_id)
    else:
        dataset_ids = [None]

    # Cost-guard preflight: count candidates across selected datasets.
    # Pass an obviously-bogus model_tag if --reclassify-on-model-change so
    # the iterator returns every chunk (idempotency check is "skip when
    # already enriched with THIS model" — an impossible tag never matches).
    iter_tag = "__force_reenrich__" if reclassify_on_model_change else model_tag
    total_to_process = 0
    for ds_id in dataset_ids:
        total_to_process += sum(1 for _ in storage.iter_code_chunks_for_enrichment(iter_tag, ds_id))

    ui_info(
        f"Enriching {total_to_process} code chunk(s) with {enricher.name} (model={model_tag}).",
    )
    ui_info(
        f"Estimated wall-clock: ~{total_to_process * 5:d}-{total_to_process * 8:d} s "
        f"(3-8 s per chunk on M-series for qwen3.6:35b-a3b-instruct; "
        f"MoE active params ~3B keep this fast).",
    )

    processed = 0
    applied = 0
    failed = 0
    for ds_id in dataset_ids:
        for chunk_id, chunk, language in storage.iter_code_chunks_for_enrichment(iter_tag, ds_id):
            if limit is not None and processed >= limit:
                break

            try:
                enrichment = enricher.enrich(chunk, language=language)
            except EnricherError as exc:
                failed += 1
                processed += 1
                ui_warn(f"chunk {chunk_id}: enricher failed ({exc}); skipping.")
                continue

            write_now = not dry_run
            if write_now:
                storage.update_chunk_enrichment(chunk_id, enrichment)
                applied += 1

            if json_out:
                payload = {
                    "chunk_id": chunk_id,
                    "language": language,
                    "docstring": enrichment.docstring,
                    "summary": enrichment.summary,
                    "symbols": list(enrichment.symbols),
                    "model": enrichment.model,
                    "confidence": float(enrichment.confidence),
                    "applied": bool(write_now),
                }
                print(_json.dumps(payload, ensure_ascii=False))
            else:
                action = "would enrich" if dry_run else "enriched"
                summary_snippet = (enrichment.summary or "").split("\n", 1)[0][:80]
                print(
                    f"chunk {chunk_id} [{language}] -> {action} "
                    f"({enrichment.confidence:.2f}): {summary_snippet}"
                )
            processed += 1
        if limit is not None and processed >= limit:
            break

    ui_info(f"Processed {processed} chunk(s); applied {applied}; failed {failed}.")


# ── estimate command (Phase J / J1) ─────────────────────────────────────


_HUMAN_BYTES_BASE = 1024


def _human_bytes(n: int) -> str:
    """Format ``n`` bytes for human display.

    Uses 1024-based units with two-significant-digit precision for the
    fractional decade ("412 MB", "9.2 GB", "1.4 TB").
    """
    if n < 0:
        return f"-{_human_bytes(-n)}"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(n)
    idx = 0
    while value >= _HUMAN_BYTES_BASE and idx < len(units) - 1:
        value /= _HUMAN_BYTES_BASE
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    # Drop trailing .0 for round numbers; keep one fractional digit for
    # mid-decade ("9.2 GB").
    return f"{value:.1f} {units[idx]}".replace(".0 ", " ")


def _human_count(n: int) -> str:
    """Format ``n`` files with a thousands separator."""
    return f"{n:,}"


def _estimate_pending_files(config, *, embedder_filter=None) -> dict[str, object]:
    """Phase L Wave 4 — query the backend for pending-files counters.

    Returns a JSON-serialisable dict with::

        {
            "documents_not_chunked": int,
            "chunks_missing_embedding": int,
            "sample_paths": list[str],
            "embedder": str | None,
        }

    Best-effort: any backend reachability failure (no migration applied,
    sqlite db missing, postgres unreachable, embedder helper absent) is
    swallowed and reported as zero counters so the CLI never crashes on
    the new section. The first active embedder drives the chunk count
    so the user mental-model matches "the embedder that would run next."
    """
    payload: dict[str, object] = {
        "documents_not_chunked": 0,
        "chunks_missing_embedding": 0,
        "sample_paths": [],
        "embedder": None,
    }

    try:
        # Local import preserves the lazy-load contract of the estimate
        # command (no heavy backend deps until the user actually opts in).
        if config.backend.kind == "sqlite":
            from corpus_forge.backends.sqlite import SQLiteBackend

            backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
        else:
            from corpus_forge.backends.postgres import PostgresBackend

            backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
    except Exception as exc:
        # Backend unreachable — degrade gracefully (no Pending section).
        import logging

        logging.getLogger(__name__).debug(
            "estimate: backend unreachable for pending counts (%s)", exc
        )
        return payload

    try:
        count, samples = backend.pending_documents(limit=5)
        payload["documents_not_chunked"] = count
        payload["sample_paths"] = samples
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("estimate: pending_documents failed (%s)", exc)

    # Pick first active embedder configured (the one ``backfill_embedder``
    # would target next). embedder_filter is the explicit --embedder list
    # if set; otherwise fall back to the first ``active=True`` entry.
    embedder_name: str | None = None
    candidates = embedder_filter or [e.name for e in config.embedders if getattr(e, "active", True)]
    if candidates:
        embedder_name = candidates[0]
    payload["embedder"] = embedder_name

    if embedder_name is not None:
        try:
            rows = backend._execute(
                "SELECT id FROM corpus.embedders WHERE name = %s"
                if config.backend.kind == "postgres"
                else "SELECT id FROM embedders WHERE name = ?",
                (embedder_name,),
            )
            if rows:
                embedder_id = int(rows[0]["id"])
                payload["chunks_missing_embedding"] = backend.count_chunks_missing_embedding(
                    embedder_id
                )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).debug(
                "estimate: count_chunks_missing_embedding failed (%s)", exc
            )

    return payload


def _render_scan_stats_table(scan_stats) -> None:
    """Render the "Scan stats" table (skip silently if no stats available)."""
    if scan_stats is None:
        return
    print("Scan stats:")
    print(f"  {'Elapsed':<14} {scan_stats.elapsed_s:.2f}s")
    print(f"  {'Rate':<14} {scan_stats.scan_rate:.0f} files/s")
    print(f"  {'Files seen':<14} {_human_count(scan_stats.file_count)}")
    print(f"  {'Dirs visited':<14} {_human_count(scan_stats.dir_count)}")
    print("")


def _render_pending_files_table(payload: dict[str, object]) -> None:
    """Render the "Pending files" table; skip if both counters are zero."""
    docs = int(payload.get("documents_not_chunked", 0) or 0)
    chunks = int(payload.get("chunks_missing_embedding", 0) or 0)
    samples = payload.get("sample_paths") or []
    embedder = payload.get("embedder")

    if docs == 0 and chunks == 0:
        return

    print("Pending files:")
    print(f"  {'Documents not chunked':<28} {_human_count(docs)}")
    chunk_label = (
        f"Chunks missing embedding ({embedder})"
        if embedder is not None
        else "Chunks missing embedding"
    )
    print(f"  {chunk_label:<28} {_human_count(chunks)}")
    if samples:
        print("  Sample paths (top 5):")
        for path in samples[:5]:
            print(f"    - {path}")
    print("")


@app.command("estimate")
def estimate(
    path: Path = typer.Argument(
        ...,
        help="Directory to scan. Recursively walked; symlinks not followed.",
        exists=False,  # we validate manually so we can exit code 2 with a friendly message
        file_okay=False,
        dir_okay=True,
        resolve_path=False,
    ),
    dataset: str = typer.Option(
        None,
        "--dataset",
        "-d",
        help=(
            "Filter active embedders by dataset name. Permissive — an unknown "
            "dataset falls back to all active embedders."
        ),
    ),
    embedder: list[str] = typer.Option(
        None,
        "--embedder",
        help="Explicit embedder filter (repeatable). Overrides --dataset.",
    ),
    compression_ratio: float = typer.Option(
        None,
        "--compression-ratio",
        help=(
            "Override [estimate].compression_ratio. Multiplier in (0.0, 1.0] "
            "applied to text-heavy columns. Drop to 0.5 to model LZ4-toasted "
            "text columns."
        ),
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the SyncEstimate dataclass as JSON (schema_version=1).",
    ),
    verbose: bool = typer.Option(  # noqa: ARG001 — reserved for future per-file dump
        False,
        "--verbose",
        help="Print per-file detail (reserved for future use).",
    ),
    ignore_file: Path | None = typer.Option(
        None,
        "--ignore-file",
        help=(
            "Path to a .corpusignore file. Overrides auto-detection of "
            "<path>/.corpusignore. Passing a non-existent path is an error."
        ),
    ),
    no_ignore_file: bool = typer.Option(
        False,
        "--no-ignore-file",
        help=(
            "Disable the local .corpusignore lookup entirely. Mutually "
            "exclusive with --ignore-file."
        ),
    ),
    no_global_ignore: bool = typer.Option(
        False,
        "--no-global-ignore",
        help=(
            "Disable the user-global ignore file "
            "(~/.config/corpus-forge/ignore or $CF_GLOBAL_IGNORE_FILE)."
        ),
    ),
) -> None:
    """Predict the Postgres storage footprint of syncing a folder.

    Pure-prediction — does not touch the database, does not instantiate
    any extractor, does not call any model. Walks the filesystem, buckets
    each file into an extractor class via the per-extension heuristic
    table, then sums the per-row + per-embedder + index overheads.

    Default output is human-readable; pass ``--json`` for the
    JSON-serialised :class:`SyncEstimate` (schema_version=1) suitable for
    piping into downstream tools.

    Tunable via the ``[estimate]`` block in ``config.toml`` (currently
    just ``compression_ratio``). Override the ratio per invocation with
    ``--compression-ratio``.
    """
    import json as _json
    from dataclasses import asdict

    from corpus_forge.config import Config
    from corpus_forge.estimate import estimate_sync
    from corpus_forge.ignore import (
        CorpusIgnore,
        IgnoreStack,
        load_global_ignore,
        load_local_ignore,
    )

    # Mutual-exclusivity guard.
    if no_ignore_file and ignore_file is not None:
        ui_error("--ignore-file and --no-ignore-file are mutually exclusive.")
        raise typer.Exit(code=2)

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit(code=2) from None

    # Embedder selection precedence:
    #   1. --embedder NAME (repeatable)  — explicit filter, hard-fail on
    #      unknown names.
    #   2. --dataset NAME — permissive forward-compat hook; today it
    #      falls back to all active embedders since per-dataset embedder
    #      lists aren't a config field yet.
    #   3. Default = every active embedder.
    if embedder:
        chosen_embedders: list[str] | None = list(embedder)
    else:
        # --dataset is accepted for forward-compat but is a no-op for
        # embedder selection in J1. Mention silently — no warning, no
        # crash — to keep MCP callers' lives simple.
        if dataset is not None:
            configured_names = {d.name for d in config.datasets}
            if dataset not in configured_names:
                ui_info(
                    f"note: --dataset {dataset!r} is not in the configured datasets "
                    f"({sorted(configured_names)}); using all active embedders.",
                )
        chosen_embedders = None  # signal "all active" to estimate_sync

    # Build the ignore stack (local + global). Path resolution mirrors
    # what estimate_sync does internally so the matcher's relative-path
    # math agrees with the walker's.
    resolved_root = Path(path).expanduser().resolve()
    if no_ignore_file:
        local_set = CorpusIgnore.empty(resolved_root)
    else:
        try:
            local_set = load_local_ignore(resolved_root, override=ignore_file)
        except FileNotFoundError as exc:
            ui_error(f"--ignore-file not found: {exc}")
            raise typer.Exit(code=2) from None
    global_set = CorpusIgnore.empty(Path.home()) if no_global_ignore else load_global_ignore()
    ignore_stack = IgnoreStack((global_set, local_set))

    try:
        estimate_result = estimate_sync(
            path,
            config,
            embedders=chosen_embedders,
            compression_ratio=compression_ratio,
            ignore=ignore_stack,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        ui_error(str(exc))
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        ui_error(f"estimate error: {exc}")
        raise typer.Exit(code=2) from None

    # Phase L Wave 4 — compute scan stats + pending-files snapshot so the
    # CLI can render the two new sections regardless of human/JSON mode.
    from corpus_forge.estimate import get_last_scan_stats

    scan_stats = get_last_scan_stats()
    pending_payload = _estimate_pending_files(config, embedder_filter=chosen_embedders)

    if json_out:
        # JSON estimate — stdout for piping (no markup mangling). Wave 4
        # adds two new sibling keys ``"scan"`` + ``"pending"`` alongside
        # the existing SyncEstimate fields so the shape stays additive.
        # The MCP ``estimate_sync_size`` tool still consumes
        # ``asdict(SyncEstimate)`` directly via the corpus_forge.estimate
        # module — it is NOT affected by this CLI-side wrapping.
        out_payload: dict[str, object] = {**asdict(estimate_result)}
        if scan_stats is not None:
            out_payload["scan"] = asdict(scan_stats)
        out_payload["pending"] = pending_payload
        print(_json.dumps(out_payload, ensure_ascii=False))
        return

    # Human output. Layout mirrors the brief's example verbatim. The
    # report itself is the command's data product, so it stays on
    # stdout for piping; status lines (ui_*) are stderr.
    print(
        f"corpus-forge estimate {estimate_result.scanned_path}\n"
        f"Scanned {_human_count(estimate_result.file_count)} files "
        f"across {_human_count(estimate_result.dir_count)} directories "
        f"({_human_bytes(estimate_result.total_raw_bytes)} raw)."
    )
    print("")
    _render_scan_stats_table(scan_stats)
    print("By extractor:")
    for summary in estimate_result.by_extractor:
        chunk_str = (
            "skipped"
            if summary.est_chunks == 0 and summary.extractor_class in ("image", "unknown")
            else f"~{_human_count(summary.est_chunks)} chunks"
        )
        print(
            f"  {summary.extractor_class:<12} "
            f"{_human_count(summary.file_count):>10} files     "
            f"{_human_bytes(summary.raw_bytes):>9}    ->  {chunk_str}"
        )
    print("")
    print("Estimated Postgres footprint (purely additive):")
    print(f"  {'documents':<18} {_human_bytes(estimate_result.documents_bytes):>10}")
    print(f"  {'chunks':<18} {_human_bytes(estimate_result.chunks_bytes):>10}")
    if estimate_result.embeddings:
        print("  embeddings")
        for e in estimate_result.embeddings:
            print(
                f"    {e.name:<16} {_human_bytes(e.total_bytes):>10}   "
                f"({_human_count(e.n_chunks)} x {e.dim} x 4 B + 35% HNSW)"
            )
    print(f"  {'btree indexes':<18} {_human_bytes(estimate_result.btree_index_bytes):>10}")
    print("  " + "-" * 28)
    print(f"  {'Total':<18} {_human_bytes(estimate_result.total_bytes):>10}")
    print("")
    _render_pending_files_table(pending_payload)
    print(
        f"Assumed compression ratio: {estimate_result.compression_ratio}. "
        "Pass `--compression-ratio 0.5` to model LZ4-toasted text columns."
    )


if __name__ == "__main__":
    app()
