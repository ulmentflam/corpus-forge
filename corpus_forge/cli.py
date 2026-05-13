"""Command-line interface for corpus-forge."""

from pathlib import Path

import typer

from . import __version__

app = typer.Typer(
    name="corpus-forge",
    help="HF-format corpus + multi-embedder ingestion daemon for personal text and chat data.",
    add_completion=False,
)


@app.command()
def migrate():
    """Apply schema migrations."""
    from .schema.migrate import main

    main()


@app.command()
def ingest(once: bool = typer.Option(False, "--once", help="Run one-shot ingestion pass")):
    """Run ingestion daemon or one-shot pass."""
    from .ingest import main

    main(once=once)


@app.command()
def embed(
    embedder: str = typer.Option(..., "-e", help="Embedder name"),
    dataset: str | None = typer.Option(None, "-d", help="Dataset name"),
    limit: int | None = typer.Option(None, "-l", help="Max chunks to process"),
):
    """Backfill embeddings for chunks."""
    from .embed import main

    main(embedder=embedder, dataset=dataset, limit=limit)


@app.command()
def daemon():
    """Run daemon in foreground (dev)."""
    from .daemon import main

    main()


@app.command()
def version():
    """Print version and exit."""
    typer.echo(f"corpus-forge version {__version__}")


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
# R3 owns the [eval] extra in pyproject.  R4 will wire `--rerank` to a
# real cross-encoder; R3 emits a friendly notice when `--rerank` is
# explicitly requested.


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
    config=None, *, fusion: str | None = None, alpha: float | None = None
):
    """Wire a HybridRetriever to a backend + first-active embedder.

    When ``config`` is None (the CLI path), the config is loaded via
    :func:`_load_eval_config`, which applies the ``fusion`` / ``alpha``
    overrides.  Callers (and tests) may pass a pre-built config object to
    skip the on-disk lookup.

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
    return HybridRetriever(backend=backend, embedder=embedder, embedder_id=eid)


def _emit_rerank_notice(rerank: bool) -> None:
    if rerank:
        typer.echo(
            "Note: --rerank is a no-op in this release — the cross-encoder "
            "reranker lands in Phase R4. Running without rerank.",
            err=True,
        )


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

    _emit_rerank_notice(rerank)

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

    # Build the retriever; Config.load is inside this builder so tests that
    # stub `_build_retriever_for_eval` don't need to also stub `Config.load()`.
    retriever = _build_retriever_for_eval(fusion=fusion, alpha=alpha)

    metrics = evaluate_retriever(retriever, gold_path, k_values=k_values)
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
        False, "--rerank/--no-rerank", help="Apply reranker (Phase R4 — currently a no-op)"
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
        False, "--rerank/--no-rerank", help="Apply reranker (Phase R4 — currently a no-op)"
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


if __name__ == "__main__":
    app()
