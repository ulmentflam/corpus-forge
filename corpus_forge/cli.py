"""Command-line interface for corpus-forge."""

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
    from .schema.migrate import main  # noqa: PLC0415

    main()


@app.command()
def ingest(once: bool = typer.Option(False, "--once", help="Run one-shot ingestion pass")):
    """Run ingestion daemon or one-shot pass."""
    from .ingest import main  # noqa: PLC0415

    main(once=once)


@app.command()
def embed(
    embedder: str = typer.Option(..., "-e", help="Embedder name"),
    dataset: str | None = typer.Option(None, "-d", help="Dataset name"),
    limit: int | None = typer.Option(None, "-l", help="Max chunks to process"),
):
    """Backfill embeddings for chunks."""
    from .embed import main  # noqa: PLC0415

    main(embedder=embedder, dataset=dataset, limit=limit)


@app.command()
def daemon():
    """Run daemon in foreground (dev)."""
    from .daemon import main  # noqa: PLC0415

    main()


@app.command()
def version():
    """Print version and exit."""
    typer.echo(f"corpus-forge version {__version__}")


if __name__ == "__main__":
    app()
