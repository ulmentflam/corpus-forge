"""``corpus-forge source ...`` admin verbs (Phase L Wave 7).

All verbs require ``-d <dataset>`` so the user names the host dataset
explicitly.  The reasoning: a source's identity is the
``(dataset, plugin, root)`` tuple, so naming it free-floating would be
ambiguous.

Three verbs:

- ``list``   — table of every source in the named dataset.
- ``add``    — wizard; after writing the entry, optionally trigger an
  ingest pass.
- ``remove`` — confirm + drop the entry; index-addressed
  (``source remove -d <dataset> <i>``) because sources lack a stable
  name field in the schema.
"""

from __future__ import annotations

import logging
import sys
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

source_app = typer.Typer(
    help="Manage sources nested under a dataset (list / add / remove).",
    add_completion=False,
)


def _find_dataset(doc, name: str) -> dict[str, Any] | None:
    for d in doc.get("datasets") or []:
        if d.get("name") == name:
            return d
    return None


def _source_root_field(source: dict) -> str:
    """Return the most-informative path field for ``source``."""

    for key in ("root", "vault_root", "projects_root", "storage_root"):
        val = source.get(key)
        if val:
            return str(val)
    # Phase M Wave 4: Zotero sources have a nested `zotero` table with
    # either library_path (local/both) or user_id (web).
    z = source.get("zotero") or {}
    if isinstance(z, dict):
        return str(z.get("library_path") or z.get("user_id") or "")
    return ""


@source_app.command("list")
def cmd_list(
    dataset: Annotated[
        str, typer.Option("--dataset", "-d", help="Dataset name to list sources for.")
    ],
) -> None:
    """Render every source under ``--dataset`` as a Rich table."""

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    ds = _find_dataset(doc, dataset)
    if ds is None:
        ui_error(f"Dataset {dataset!r} not found.")
        raise typer.Exit(code=1)

    sources = ds.get("sources") or []
    if not sources:
        ui_warn(f"No sources configured for {dataset!r}.")
        return

    table = Table(
        title=f"Sources for {dataset}",
        title_style="h1",
        show_header=True,
    )
    table.add_column("#", justify="right", style="muted")
    table.add_column("Plugin", style="muted")
    table.add_column("Root", style="accent.path")
    table.add_column("Chunker", style="muted")
    for i, source in enumerate(sources):
        table.add_row(
            str(i),
            str(source.get("plugin", "")),
            _source_root_field(source),
            str(source.get("chunker", "")),
        )
    ui_console.print(table)


@source_app.command("add")
def cmd_add(
    ctx: typer.Context,
    dataset: Annotated[
        str, typer.Option("--dataset", "-d", help="Dataset to attach the new source to.")
    ],
    ingest_now: Annotated[
        bool,
        typer.Option(
            "--ingest/--no-ingest",
            help="Prompt to ingest immediately after writing. Defaults to ask.",
        ),
    ] = True,
) -> None:
    """Add a new source under ``--dataset`` via a small wizard.

    After the entry lands in ``config.toml`` the verb (interactively)
    asks whether to trigger an ingest pass.  Foreground unless the
    CLI-level ``--background`` flag is set.
    """

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    ds = _find_dataset(doc, dataset)
    if ds is None:
        ui_error(f"Dataset {dataset!r} not found.")
        raise typer.Exit(code=1)

    plugin = Prompt.ask(
        "Plugin",
        choices=["filesystem", "markdown_vault", "claude_code", "opencode", "zotero"],
        default="filesystem",
    )

    if plugin == "filesystem":
        root = Prompt.ask("Root directory", default="~/Documents/notes")
        chunker = Prompt.ask("Chunker", choices=["markdown", "conversation"], default="markdown")
        source = {"plugin": plugin, "root": root, "chunker": chunker}
    elif plugin == "markdown_vault":
        vault_root = Prompt.ask("Vault root directory")
        source = {"plugin": plugin, "vault_root": vault_root, "chunker": "markdown"}
    elif plugin == "claude_code":
        projects_root = Prompt.ask("Projects root", default="~/.claude/projects")
        source = {
            "plugin": plugin,
            "projects_root": projects_root,
            "chunker": "conversation",
        }
    elif plugin == "zotero":
        # Phase M Wave 4: mode-aware wizard. We collect only the bare
        # minimum here — full knobs (collections, mime allowlist, cache
        # dir) round-trip via `config edit`.
        mode = Prompt.ask("Mode", choices=["local", "web", "both"], default="local")
        zotero: dict[str, str | None] = {"mode": mode}
        if mode in ("local", "both"):
            zotero["library_path"] = Prompt.ask(
                "Local zotero.sqlite path",
                default="~/Zotero/zotero.sqlite",
            )
        if mode in ("web", "both"):
            zotero["user_id"] = Prompt.ask("Zotero user_id (numeric)")
            zotero["api_key_env"] = Prompt.ask("API key env var", default="ZOTERO_API_KEY")
        source = {
            "plugin": plugin,
            "zotero": zotero,
            "chunker": "markdown",
        }
    else:  # opencode
        storage_root = Prompt.ask("Storage root", default="~/.opencode")
        source = {
            "plugin": plugin,
            "storage_root": storage_root,
            "chunker": "conversation",
        }

    sources = ds.setdefault("sources", [])
    sources.append(source)
    write_toml_atomic(config_path, doc)
    try:
        from corpus_forge.config import Config

        Config.load(config_path=config_path)
    except Exception as exc:
        ui_error(f"New source invalid: {exc}")
        raise typer.Exit(code=1) from exc

    ui_ok(f"Added source to dataset {dataset!r}")

    if not ingest_now:
        return
    if not Confirm.ask("Ingest now?", default=True):
        return

    background = bool(getattr(getattr(ctx, "obj", None), "background", False))
    from corpus_forge.admin.foreground import run_attached

    rc = run_attached(
        [sys.executable, "-m", "corpus_forge", "ingest", "--once"],
        component="ingest",
        background=background,
    )
    if rc != 0:
        ui_warn(f"Ingest exited with rc={rc}")


@source_app.command("remove")
def cmd_remove(
    dataset: Annotated[str, typer.Option("--dataset", "-d", help="Dataset hosting the source.")],
    index: Annotated[int, typer.Argument(help="Source index (see `source list`).")],
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove the source at ``index`` from ``--dataset``."""

    config_path = resolve_config_path()
    doc = load_toml_document(config_path)
    ds = _find_dataset(doc, dataset)
    if ds is None:
        ui_error(f"Dataset {dataset!r} not found.")
        raise typer.Exit(code=1)

    sources = ds.get("sources") or []
    if index < 0 or index >= len(sources):
        ui_error(f"Source index {index} out of range (have {len(sources)} sources).")
        raise typer.Exit(code=1)

    target = sources[index]
    summary = f"{target.get('plugin', '?')} @ {_source_root_field(target) or '(no root)'}"
    if not yes and not Confirm.ask(f"Remove source #{index}: {summary}?", default=False):
        ui_info("Aborted.")
        raise typer.Exit(code=0)

    sources.pop(index)
    write_toml_atomic(config_path, doc)
    try:
        from corpus_forge.config import Config

        Config.load(config_path=config_path)
    except Exception as exc:
        ui_error(f"Config invalid after removal: {exc}")
        raise typer.Exit(code=1) from exc

    ui_ok(f"Removed source #{index} from {dataset!r}")


__all__ = ["source_app"]
