"""Command-line interface for corpus-forge."""

from pathlib import Path
from typing import Annotated

import typer

from . import __version__

app = typer.Typer(
    name="corpus-forge",
    help="HF-format corpus + multi-embedder ingestion daemon for personal text and chat data.",
    add_completion=False,
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
def ingest(once: bool = typer.Option(False, "--once", help="Run one-shot ingestion pass")):
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

    main(once=once)


@app.command()
def embed(
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

    main(embedder=embedder, dataset=dataset, limit=limit, image=image)


@app.command()
def daemon():
    """Run the corpus-forge ingestion daemon in the foreground.

    Watches every configured source for filesystem changes, debounces them,
    and re-ingests touched documents through the extractor/chunker/embedder
    pipeline. Intended for development; production deployments wrap this
    command via the systemd user unit (Linux) or launchd agent (macOS)
    rendered by ``scripts/{linux,macos}/install.sh``.
    """
    from .daemon import main

    main()


@app.command()
def version():
    """Print version and exit."""
    typer.echo(f"corpus-forge version {__version__}")


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
    typer.echo(f"exported to {out}", err=True)


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
    typer.echo(f"exported to {out}", err=True)


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


def _get_dataset_id(backend, name):
    rows = backend._execute("SELECT id FROM corpus.datasets WHERE name = %s", (name,))
    return rows[0]["id"] if rows else None


@sync_app.command()
def status(
    dataset: str = typer.Option(None, "-d", "--dataset", help="Dataset name"),
):
    """Show sync status per dataset."""
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        typer.echo("No configuration found; run 'corpus-forge migrate' to initialise.")
        raise typer.Exit() from None
    try:
        backend = _get_backend(config)
        for ds in config.datasets:
            if dataset and ds.name != dataset:
                continue
            ds_id = _get_dataset_id(backend, ds.name)
            if not ds_id:
                typer.echo(f"Dataset {ds.name}: not found")
                continue
            pending = backend.pending_remote_revisions(ds_id, None, config.host_id(), limit=1)
            typer.echo(
                f"Dataset {ds.name}: sync={'enabled' if ds.sync_enabled else 'disabled'},"
                f" pending={len(pending)}"
            )
    except Exception as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit() from None


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
        typer.echo("No configuration found.")
        raise typer.Exit() from None
    try:
        backend = _get_backend(config)
        from corpus_forge.ingest import _instantiate_source
        from corpus_forge.sync.echo import EchoSuppressor
        from corpus_forge.sync.pull import PullPipeline

        echo = EchoSuppressor()
        dataset_id = _get_dataset_id(backend, dataset)
        if not dataset_id:
            typer.echo(f"Dataset {dataset}: not found")
            raise typer.Exit()
        for ds in config.datasets:
            if ds.name != dataset:
                continue
            for src_cfg in ds.sources:
                source = _instantiate_source(src_cfg)
                pl = PullPipeline(backend, dataset_id, source.root, echo, config.host_id())
                if once:
                    count = pl.tick()
                    typer.echo(f"Pulled {count} revision(s)")
                else:
                    pl.start(
                        source.root,
                        poll_interval_s=config.daemon.sync_poll_interval_s,
                    )
                    typer.echo(f"Continuous pull started for {ds.name}/{src_cfg.plugin}")
    except Exception as exc:
        typer.echo(f"Error: {exc}")
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
        typer.echo("No configuration found.")
        raise typer.Exit() from None
    try:
        backend = _get_backend(config)
        from corpus_forge.ingest import _instantiate_source
        from corpus_forge.sync.echo import EchoSuppressor
        from corpus_forge.sync.push import PushPipeline

        echo = EchoSuppressor()
        dataset_id = _get_dataset_id(backend, dataset)
        if not dataset_id:
            typer.echo(f"Dataset {dataset}: not found")
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
                typer.echo(f"Push started for {ds.name}/{src_cfg.plugin}")
    except Exception as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit() from None


@sync_app.command()
def resolve(
    conflict_file: str = typer.Argument(..., help="Path to conflict file"),
    strategy: str = typer.Option("ours", "--strategy", help="keep-local|keep-remote"),
):
    """Resolve a sync conflict."""
    if strategy == "merge":
        typer.secho(
            "Merge-based conflict resolution is not yet implemented. "
            "Use --strategy ours or --strategy theirs as a workaround.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    path = Path(conflict_file)
    if strategy == "theirs":
        if path.exists():
            path.unlink()
            typer.echo(f"Removed conflict file {conflict_file} (keeping remote)")
        else:
            typer.echo(f"Nothing to resolve: {conflict_file} not found")
    elif strategy == "ours":
        if path.exists():
            dest = Path(str(path).split(".conflict-")[0])
            path.rename(dest)
            typer.echo(f"Restored local version to {dest}")
        else:
            typer.echo(f"Nothing to resolve: {conflict_file} not found")
    else:
        typer.secho(f"Unknown resolution strategy: {strategy}", err=True)
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
        typer.echo("No configuration found.")
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
            typer.echo(f"No revisions for {source_uri}")
            return
        for row in rows:
            status = "deleted" if row["is_tombstone"] else "alive"
            typer.echo(
                f"#{row['revision_number']} id={row['id']} "
                f"hash={row['content_hash'][:12]}... "
                f"host={row['author_host']} {status} {row['created_at']}"
            )
    except Exception as exc:
        typer.echo(f"Error: {exc}")
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
    """Parse '10,20,30' → [10, 20, 30]; reject empty / non-int entries."""
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

    - ``"cross_encoder"`` → :class:`CrossEncoderReranker` (default;
      uses ``BAAI/bge-reranker-v2-m3`` unless overridden).
    - ``"ollama"`` → :class:`OllamaReranker` (score-via-completion;
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
        typer.echo(str(exc), err=True)
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
            typer.echo(
                "No configuration found; run 'corpus-forge migrate' to initialise.",
                err=True,
            )
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
    typer.echo(report(metrics))
    if json_out is not None:
        dump_json(metrics, json_out)
        typer.echo(f"Wrote metrics → {json_out}", err=True)


def _load_eval_config(fusion: str | None = None, alpha: float | None = None):
    """Load Config and apply CLI overrides; raise typer.Exit on missing config."""
    from corpus_forge.config import Config

    try:
        config = Config.load()
    except FileNotFoundError:
        typer.echo("No configuration found; run 'corpus-forge migrate' to initialise.", err=True)
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
            typer.echo(
                "No configuration found; run 'corpus-forge migrate' to initialise.",
                err=True,
            )
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
        typer.echo(f"Wrote {len(hits)} hits → {json_out}", err=True)
        return

    if not hits:
        typer.echo(f"No hits for {query!r}.")
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
        typer.echo("  ".join(header_bits))
        typer.echo(f"    {body}")
        typer.echo("")


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
        typer.echo(
            "No configuration found; run 'corpus-forge migrate' to initialise.",
            err=True,
        )
        raise typer.Exit(code=2) from None

    # Build the chain. ``--classifier`` filters down to a single named
    # classifier (helpful for debugging — e.g. force rule even when LLM
    # is configured).
    try:
        registry: ClassifierRegistry = register_default_classifiers(config.classifier)
    except ValueError as exc:
        typer.echo(f"Classifier-config error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if classifier is not None:
        filtered = ClassifierRegistry()
        target = registry.get(classifier)
        if target is None:
            typer.echo(
                f"--classifier {classifier!r} is not in the configured chain "
                f"({registry.names()}). Available classifiers: ['rule', 'llm'].",
                err=True,
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
                typer.echo(f"Dataset not found: {name}", err=True)
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
    typer.echo(
        f"Classifying {total_to_process} document(s) via chain={chain_names}.",
        err=True,
    )
    if "llm" in chain_names:
        typer.echo(
            f"Worst-case LLM cost: up to {total_to_process} LLM call(s) "
            f"(~5-10 s/doc on qwen2.5:7b-instruct, M-series). "
            f"Rule classifier short-circuits high-confidence docs "
            f"(confidence >= {threshold:.2f}).",
            err=True,
        )
    else:
        typer.echo(
            "Rule classifier only — microseconds per document.",
            err=True,
        )

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
                typer.echo(_json.dumps(payload, ensure_ascii=False))
            else:
                action = "would assign" if dry_run else "assigned"
                typer.echo(
                    f"{doc.source_uri} -> {action} class={result.value} "
                    f"({result.confidence:.2f}) [{winner_name}: {result.rationale}]"
                )
            processed += 1
        if limit is not None and processed >= limit:
            break

    typer.echo(
        f"Processed {processed} document(s); applied {applied}.",
        err=True,
    )


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

    - ``class=code`` → :class:`CodeChunker` (tree-sitter or byte-line fallback)
    - ``class=chat`` → :class:`ConversationChunker`
    - ``class=reference`` → :class:`PassthroughChunker`
    - ``class=book`` / ``textbook`` / ``paper`` / ``article`` / ``note`` /
      ``other`` → :class:`CDCChunker` (FastCDC rolling hash)

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
        typer.echo(
            "No configuration found; run 'corpus-forge migrate' to initialise.",
            err=True,
        )
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
                typer.echo(f"Dataset not found: {name}", err=True)
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
                typer.echo(
                    f"Skipping {doc.source_uri}: {exc}",
                    err=True,
                )
                continue

            # Run the chunker. We feed ``doc.text`` directly; the chunker
            # contract is ``chunk(text: str) -> list[TextChunk]`` for all
            # prose-flavoured chunkers. CodeChunker also accepts that
            # surface (it just lacks language/relative_path metadata —
            # the byte-line fallback still produces valid chunks).
            try:
                new_chunks = chunker.chunk(doc.text)
            except Exception as exc:  # pragma: no cover — defensive
                typer.echo(
                    f"Chunker error on {doc.source_uri}: {exc}; skipping.",
                    err=True,
                )
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
            # - CDC chunks → ``cdc_fingerprint``
            # - code chunks → ``kind`` (tree-sitter) or ``byte_range`` (byte fallback)
            # - everything else has no required signature → text-only check.
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
                    typer.echo(
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
                    typer.echo(
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
                typer.echo(
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
                typer.echo(
                    f"{doc.source_uri} -> {action} into {len(new_texts)} chunks "
                    f"[class={class_value}]"
                )
            processed += 1
        if limit is not None and processed >= limit:
            break

    typer.echo(
        f"Processed {processed} document(s); applied {applied}; "
        f"noop {skipped_noop}; unclassified {skipped_unclassified}.",
        err=True,
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
        typer.echo(
            "No configuration found; run 'corpus-forge migrate' to initialise.",
            err=True,
        )
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
            typer.echo(
                f"--backend {backend!r} is not a known enricher backend. "
                f"Available: ['qwen-local', 'qwen-remote'].",
                err=True,
            )
            raise typer.Exit(code=2)
    else:
        try:
            enricher = get_active_enricher(config)
        except EnricherUnavailableError as exc:
            typer.echo(f"Enricher unavailable: {exc}", err=True)
            raise typer.Exit(code=2) from None

    if enricher.name == "noop":
        typer.echo(
            "Code enricher is disabled (config.code_enricher.backend == 'none'). "
            "Set backend to 'local' or 'remote' in config.toml, or pass --backend "
            "qwen-local / --backend qwen-remote to force a specific backend.",
            err=True,
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
                typer.echo(f"Dataset not found: {name}", err=True)
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

    typer.echo(
        f"Enriching {total_to_process} code chunk(s) with {enricher.name} (model={model_tag}).",
        err=True,
    )
    typer.echo(
        f"Estimated wall-clock: ~{total_to_process * 5:d}-{total_to_process * 8:d} s "
        f"(3-8 s per chunk on M-series for qwen3.6:35b-a3b-instruct; "
        f"MoE active params ~3B keep this fast).",
        err=True,
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
                typer.echo(
                    f"chunk {chunk_id}: enricher failed ({exc}); skipping.",
                    err=True,
                )
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
                typer.echo(_json.dumps(payload, ensure_ascii=False))
            else:
                action = "would enrich" if dry_run else "enriched"
                summary_snippet = (enrichment.summary or "").split("\n", 1)[0][:80]
                typer.echo(
                    f"chunk {chunk_id} [{language}] -> {action} "
                    f"({enrichment.confidence:.2f}): {summary_snippet}"
                )
            processed += 1
        if limit is not None and processed >= limit:
            break

    typer.echo(
        f"Processed {processed} chunk(s); applied {applied}; failed {failed}.",
        err=True,
    )


if __name__ == "__main__":
    app()
