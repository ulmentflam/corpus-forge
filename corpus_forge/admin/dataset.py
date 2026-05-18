"""``corpus-forge dataset ...`` admin verbs (Phase L Wave 7).

Four verbs:

- ``list``   — table of every dataset (name / kind / source count /
  document count / last-sync ts).
- ``get``    — JSON record + the same backend-side counts.
- ``add``    — wizard (name + kind + a single first source).
- ``remove`` — confirm + optional ``--drop-vectors`` cascade.

Backend reads (``documents`` count, last-sync ts) degrade gracefully:
when the backend isn't reachable we print ``"?"`` rather than failing
the whole verb.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

import typer
from rich.table import Table

from corpus_forge.admin.config import (
    load_toml_document,
    resolve_config_path,
    write_toml_atomic,
)
from corpus_forge.admin.embedder import _get_backend, _load_config
from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok
from corpus_forge.ui.console import warn as ui_warn
from corpus_forge.ui.prompts import Confirm, Prompt

logger = logging.getLogger(__name__)

dataset_app = typer.Typer(
    help="Manage configured datasets (list / add / remove).",
    add_completion=False,
)


def _doc_count(backend, name: str) -> int | str:
    """Best-effort count of documents in ``name``."""

    try:
        rows = backend._execute(
            "SELECT COUNT(d.id) AS n FROM corpus.documents d "
            "JOIN corpus.datasets ds ON ds.id = d.dataset_id "
            "WHERE ds.name = %s",
            (name,),
        )
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        return "?"


def _list_config_datasets() -> list[dict[str, Any]]:
    """Read datasets from the live tomlkit doc (preserves order)."""

    path = resolve_config_path()
    doc = load_toml_document(path)
    return [dict(d) for d in (doc.get("datasets") or [])]


@dataset_app.command("list")
def cmd_list() -> None:
    """Render configured datasets as a Rich table."""

    config_datasets = _list_config_datasets()
    if not config_datasets:
        ui_warn("No datasets configured.")
        return

    backend = None
    try:
        backend = _get_backend(_load_config())
    except Exception:
        backend = None

    table = Table(title="Datasets", title_style="h1", show_header=True)
    table.add_column("Name", style="accent.path")
    table.add_column("Kind", style="muted")
    table.add_column("Sources", justify="right", style="accent.number")
    table.add_column("Documents", justify="right", style="accent.number")
    for d in config_datasets:
        n_sources = len(d.get("sources") or [])
        n_docs = _doc_count(backend, d.get("name", "")) if backend is not None else "?"
        table.add_row(
            str(d.get("name", "")),
            str(d.get("kind", "")),
            str(n_sources),
            str(n_docs),
        )
    ui_console.print(table)


@dataset_app.command("get")
def cmd_get(
    name: Annotated[str, typer.Argument(help="Dataset name.")],
) -> None:
    """Print the full dataset record (config + backend counts) as JSON."""

    config_datasets = _list_config_datasets()
    record = next((d for d in config_datasets if d.get("name") == name), None)
    if record is None:
        ui_error(f"Dataset {name!r} not found.")
        raise typer.Exit(code=1)

    payload: dict[str, Any] = dict(record)
    try:
        backend = _get_backend(_load_config())
        payload["document_count"] = _doc_count(backend, name)
    except Exception:
        payload["document_count"] = "?"

    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


@dataset_app.command("add")
def cmd_add(
    name: Annotated[str, typer.Argument(help="New dataset name (unique).")],
) -> None:
    """Add a new dataset via a small wizard."""

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    existing = doc.get("datasets") or []
    if any(d.get("name") == name for d in existing):
        ui_error(f"Dataset {name!r} already exists.")
        raise typer.Exit(code=1)

    kind = Prompt.ask("Dataset kind", choices=["text", "chat"], default="text")
    plugin = Prompt.ask(
        "First source plugin",
        choices=["filesystem", "markdown_vault", "claude_code", "opencode"],
        default="filesystem",
    )
    if plugin == "filesystem":
        root = Prompt.ask("Source root directory", default="~/Documents/notes")
        chunker = Prompt.ask("Chunker", choices=["markdown", "conversation"], default="markdown")
        source = {"plugin": plugin, "root": root, "chunker": chunker}
    elif plugin == "markdown_vault":
        vault_root = Prompt.ask("Vault root directory", default="~/Documents/notes")
        source = {"plugin": plugin, "vault_root": vault_root, "chunker": "markdown"}
    else:
        # chat-source families use projects_root / storage_root
        projects_root = Prompt.ask("Projects root", default="~/.claude/projects")
        source = {
            "plugin": plugin,
            "projects_root": projects_root,
            "chunker": "conversation",
        }

    entry = {
        "name": name,
        "kind": kind,
        "sources": [source],
    }
    if "datasets" not in doc:
        doc["datasets"] = []
    doc["datasets"].append(entry)

    write_toml_atomic(config_path, doc)
    try:
        _load_config()
    except Exception as exc:
        ui_error(f"New dataset invalid: {exc}")
        raise typer.Exit(code=1) from exc
    ui_ok(f"Added dataset {name!r}")


@dataset_app.command("remove")
def cmd_remove(
    name: Annotated[str, typer.Argument(help="Dataset name to remove.")],
    drop_vectors: Annotated[
        bool,
        typer.Option(
            "--drop-vectors",
            help="Also drop the dataset's documents + embeddings from the backend.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove a dataset from the config (optionally drop its rows)."""

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    datasets = doc.get("datasets") or []
    idx = next((i for i, d in enumerate(datasets) if d.get("name") == name), None)
    if idx is None:
        ui_error(f"Dataset {name!r} not found.")
        raise typer.Exit(code=1)

    if not yes and not Confirm.ask(
        f"Remove dataset {name!r}? (config only — vectors only dropped with --drop-vectors)",
        default=False,
    ):
        ui_info("Aborted.")
        raise typer.Exit(code=0)

    datasets.pop(idx)
    write_toml_atomic(config_path, doc)
    ui_ok(f"Removed dataset {name!r} from config.")

    if drop_vectors:
        try:
            config = _load_config()
            backend = _get_backend(config)
            if config.backend.kind == "sqlite":
                rows = backend._execute("SELECT id FROM datasets WHERE name = ?", (name,))
            else:
                rows = backend._execute("SELECT id FROM corpus.datasets WHERE name = %s", (name,))
            if not rows:
                ui_info("No backend rows to drop — dataset was never registered.")
                return
            ds_id = rows[0]["id"]
            if config.backend.kind == "sqlite":
                backend._execute("DELETE FROM documents WHERE dataset_id = ?", (ds_id,))
                backend._execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
            else:
                backend._execute("DELETE FROM corpus.documents WHERE dataset_id = %s", (ds_id,))
                backend._execute("DELETE FROM corpus.datasets WHERE id = %s", (ds_id,))
            ui_ok(f"Dropped backend rows for dataset {name!r}.")
        except Exception as exc:
            ui_warn(f"Vector / row drop failed: {exc}")


__all__ = ["dataset_app"]
