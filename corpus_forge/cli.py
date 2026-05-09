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


if __name__ == "__main__":
    app()
