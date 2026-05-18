"""``corpus-forge config ...`` admin verbs (Phase L Wave 7).

Surface: a Typer sub-app implementing the seven verbs spec'd in
``phase_l_cli_ux.md`` §10:

- ``get <dotted.key>`` — print scalar or JSON value
- ``set <key> <value>`` — atomic write with Pydantic validation
- ``unset <key>`` — restore the Pydantic default
- ``show [--diff] [--secrets]`` — render the active config (redacted by
  default)
- ``path`` — print the absolute config-file path
- ``validate [--file <path>]`` — round-trip through ``Config.load``
- ``edit`` — open ``$EDITOR`` on the config, validate on save, rollback
  on invalid

The writer (``_set_config_value_atomic``) is exposed at the module level
so :mod:`corpus_forge.admin.ollama` (``set-url``) and the
:mod:`corpus_forge.admin.source` / :mod:`corpus_forge.admin.dataset`
verbs reuse it without duplicating the temp-file-swap dance.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any

import tomlkit
import typer
from rich.table import Table

from corpus_forge.admin._path import (
    PathNotFound,
    Token,
    coerce_for_field,
    get_at_path,
    parse_dotted_key,
    set_at_path,
)
from corpus_forge.diagnostics.redact import redact_toml_dict
from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok
from corpus_forge.ui.console import warn as ui_warn
from corpus_forge.ui.prompts import Confirm

logger = logging.getLogger(__name__)

config_app = typer.Typer(
    help="Read / write corpus-forge configuration (atomic, validated).",
    add_completion=False,
)


# ── Path resolution ─────────────────────────────────────────────────────


def resolve_config_path(explicit: Path | None = None) -> Path:
    """Resolve the config-file path that ``Config.load`` would use.

    Resolution order mirrors :meth:`corpus_forge.config.Config.load`:

    1. ``explicit`` argument (used by ``validate --file``).
    2. ``CORPUS_FORGE_CONFIG`` env var.
    3. ``~/.config/corpus-forge/config.toml`` default.
    """

    if explicit is not None:
        return explicit
    env_path = os.environ.get("CORPUS_FORGE_CONFIG")
    if env_path:
        return Path(env_path)
    return Path.home() / ".config" / "corpus-forge" / "config.toml"


# ── tomlkit IO ──────────────────────────────────────────────────────────


def load_toml_document(path: Path) -> tomlkit.TOMLDocument:
    """Load ``path`` as a tomlkit document.

    A missing file is returned as an empty document so ``config set``
    can seed values onto a fresh install.
    """

    if not path.exists():
        return tomlkit.document()
    text = path.read_text(encoding="utf-8")
    return tomlkit.parse(text)


def write_toml_atomic(path: Path, doc: tomlkit.TOMLDocument) -> None:
    """Atomically write ``doc`` to ``path`` (tempfile + rename)."""

    rendered = tomlkit.dumps(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


# ── Pydantic introspection ──────────────────────────────────────────────


def _resolve_field_info(dotted: str):
    """Walk the Pydantic model tree to fetch the ``FieldInfo`` for ``dotted``.

    Returns ``None`` when the key doesn't correspond to a declared
    Pydantic field (still legal — ``Config(**raw)`` may accept it via
    ``extra='allow'`` on some submodels).
    """

    try:
        from corpus_forge.config import Config
    except ImportError:
        return None

    tokens = parse_dotted_key(dotted)
    current = Config
    field_info = None
    for token in tokens:
        if token.kind == "index":
            # Indices step into the inner type of a ``list[...]``.
            current = _list_inner_type(current)
            continue
        # token.kind == "key"
        fields = getattr(current, "model_fields", None)
        if not fields or token.key not in fields:
            return None
        field_info = fields[token.key]
        annotation = field_info.annotation
        # Unwrap ``X | None``.
        from corpus_forge.admin._path import _unwrap_optional  # local import

        annotation = _unwrap_optional(annotation)
        # Step into the next model when the annotation is one.
        from pydantic import BaseModel as _BM

        if isinstance(annotation, type) and issubclass(annotation, _BM):
            current = annotation
        else:
            current = annotation
    return field_info


def _list_inner_type(annotation: Any) -> Any:
    """Return the inner type of a ``list[X]`` annotation; passthrough otherwise."""

    from typing import get_args, get_origin

    if get_origin(annotation) is list:
        args = get_args(annotation)
        return args[0] if args else Any
    return annotation


# ── Defaults & diff ─────────────────────────────────────────────────────


def _config_defaults() -> dict:
    """Render the default-construction of the ``Config`` model as a dict.

    We can't naively ``Config()`` since required fields lack defaults;
    instead, we walk ``model_fields`` and build the dict ourselves so
    ``show --diff`` has a baseline that mirrors what's optional today.
    """

    from pydantic import BaseModel

    from corpus_forge.config import Config

    def _walk(model_cls: type[BaseModel]) -> dict:
        out: dict = {}
        for name, info in model_cls.model_fields.items():
            from corpus_forge.admin._path import _unwrap_optional  # local

            ann = _unwrap_optional(info.annotation)
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                # Nested model — try its defaults too; fall back to {}.
                try:
                    out[name] = ann().model_dump(mode="json")
                except Exception:
                    out[name] = _walk(ann)
                continue
            # Scalar / list / dict default.
            if info.default is not None and info.default is not ...:
                out[name] = info.default
            elif info.default_factory is not None:
                try:
                    out[name] = info.default_factory()
                except Exception:
                    out[name] = None
        return out

    return _walk(Config)


def _diff_dicts(a: dict, b: dict, *, path: str = "") -> dict:
    """Return the subset of ``a`` whose values differ from ``b``.

    Recurses into nested dicts; lists are compared whole (no per-item
    diff — keeps the diff readable).
    """

    out: dict = {}
    for key, value in a.items():
        baseline = b.get(key)
        if isinstance(value, dict) and isinstance(baseline, dict):
            sub = _diff_dicts(value, baseline, path=f"{path}.{key}".lstrip("."))
            if sub:
                out[key] = sub
            continue
        if value != baseline:
            out[key] = value
    return out


# ── Public writer (shared with ollama/source/dataset) ────────────────────


class ConfigWriteError(Exception):
    """Raised when ``_set_config_value_atomic`` rejects the new value."""


def _set_config_value_atomic(
    key: str,
    raw_value: str,
    *,
    config_path: Path | None = None,
) -> Any:
    """Set ``key`` to ``raw_value`` in the live config (atomic).

    Steps:

    1. Load the tomlkit doc (preserves comments + order).
    2. Coerce ``raw_value`` using the resolved Pydantic ``FieldInfo``.
    3. Apply via ``set_at_path``.
    4. Round-trip the doc through ``Config.load`` for validation.
    5. Only on success, write back atomically.

    Raises :class:`ConfigWriteError` (with the validation message) on
    any failure.  The on-disk file is never partially updated.
    """

    path = resolve_config_path(config_path)
    doc = load_toml_document(path)
    field_info = _resolve_field_info(key)
    try:
        typed_value = coerce_for_field(raw_value, field_info)
    except ValueError as exc:
        raise ConfigWriteError(f"could not coerce value for {key}: {exc}") from exc

    set_at_path(doc, key, typed_value)

    # Validate by round-tripping through Config.load on a temp file.
    rendered = tomlkit.dumps(doc)
    fd, tmp_name = tempfile.mkstemp(prefix="cf-config-validate-", suffix=".toml")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        from corpus_forge.config import Config

        Config.load(config_path=tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise ConfigWriteError(f"config invalid after set: {exc}") from exc
    tmp_path.unlink(missing_ok=True)

    write_toml_atomic(path, doc)
    return typed_value


def _unset_config_value_atomic(
    key: str,
    *,
    config_path: Path | None = None,
) -> None:
    """Restore ``key`` to its Pydantic default (or remove the row).

    For optional fields with ``default=None`` we remove the row; for
    fields with a real default we write the default value.  Result:
    ``Config.load`` after ``unset`` matches what a freshly-generated
    config would produce for that key.
    """

    path = resolve_config_path(config_path)
    doc = load_toml_document(path)
    tokens = parse_dotted_key(key)

    field_info = _resolve_field_info(key)
    if field_info is None:
        # Unknown key — try to remove it if present.
        _remove_at_path(doc, tokens)
    else:
        default = _field_default(field_info)
        if default is _SENTINEL_REMOVE:
            _remove_at_path(doc, tokens)
        else:
            set_at_path(doc, key, default)

    # Validate.
    rendered = tomlkit.dumps(doc)
    fd, tmp_name = tempfile.mkstemp(prefix="cf-config-validate-", suffix=".toml")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        from corpus_forge.config import Config

        Config.load(config_path=tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise ConfigWriteError(f"config invalid after unset: {exc}") from exc
    tmp_path.unlink(missing_ok=True)

    write_toml_atomic(path, doc)


_SENTINEL_REMOVE = object()


def _field_default(field_info) -> Any:
    """Resolve the default value for ``field_info``.

    Returns :data:`_SENTINEL_REMOVE` when the field has no default and
    is ``Optional`` — meaning ``unset`` should delete the row entirely.
    """

    if field_info.default is not None and field_info.default is not ...:
        return field_info.default
    if field_info.default_factory is not None:
        try:
            return field_info.default_factory()
        except Exception:
            return _SENTINEL_REMOVE
    # No default — treat as removable when the field is Optional.
    return _SENTINEL_REMOVE


def _remove_at_path(doc: Any, tokens: Iterable[Token]) -> None:
    """Best-effort delete of the leaf at ``tokens``."""

    tokens = list(tokens)
    if not tokens:
        return
    node = doc
    try:
        for token in tokens[:-1]:
            node = _walk_one(node, token)
    except PathNotFound:
        return
    last = tokens[-1]
    try:
        if last.kind == "index" and isinstance(node, list):
            idx = int(last.key)
            if 0 <= idx < len(node):
                node.pop(idx)
        elif last.kind == "key" and last.key in getattr(node, "keys", lambda: [])():
            del node[last.key]
    except (KeyError, IndexError):
        return


def _walk_one(container: Any, token: Token) -> Any:
    if token.kind == "index":
        return container[int(token.key)]
    return container[token.key]


# ── Verbs ───────────────────────────────────────────────────────────────


@config_app.command("path")
def cmd_path() -> None:
    """Print the absolute path to the config file."""

    # Data line — stdout for piping.
    print(resolve_config_path())


@config_app.command("get")
def cmd_get(
    key: Annotated[str, typer.Argument(help="Dotted key (e.g. 'backend.kind').")],
) -> None:
    """Print the value at ``key`` (scalar prints raw; dict/list as JSON)."""

    path = resolve_config_path()
    doc = load_toml_document(path)
    try:
        value = get_at_path(doc, key)
    except PathNotFound as exc:
        ui_error(f"Key not found: {key} ({exc})")
        raise typer.Exit(code=1) from exc

    rendered = _render_value(value)
    print(rendered)


def _render_value(value: Any) -> str:
    """Render ``value`` for stdout: scalars as-is, containers as JSON."""

    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_to_json_safe(value), indent=2, sort_keys=True, default=str)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_json_safe(value: Any) -> Any:
    """Recursively coerce tomlkit values into plain Python for JSON output."""

    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v) for v in value]
    return value


@config_app.command("set")
def cmd_set(
    key: Annotated[str, typer.Argument(help="Dotted key to assign.")],
    value: Annotated[str, typer.Argument(help="New value (coerced per the field type).")],
) -> None:
    """Set ``key`` to ``value`` (validated, atomic)."""

    try:
        typed = _set_config_value_atomic(key, value)
    except ConfigWriteError as exc:
        ui_error(str(exc))
        raise typer.Exit(code=1) from exc

    ui_ok(f"{key} = {typed!r}")


@config_app.command("unset")
def cmd_unset(
    key: Annotated[str, typer.Argument(help="Dotted key to reset / remove.")],
) -> None:
    """Reset ``key`` to its Pydantic default (or remove when optional)."""

    try:
        _unset_config_value_atomic(key)
    except ConfigWriteError as exc:
        ui_error(str(exc))
        raise typer.Exit(code=1) from exc
    ui_ok(f"unset {key}")


@config_app.command("show")
def cmd_show(
    diff: Annotated[
        bool,
        typer.Option("--diff", help="Show only fields that differ from the Pydantic defaults."),
    ] = False,
    secrets: Annotated[
        bool,
        typer.Option(
            "--secrets",
            help="Show raw secret values (DSN, api_keys, tokens). Off by default.",
        ),
    ] = False,
) -> None:
    """Render the active config (redacted by default)."""

    path = resolve_config_path()
    doc = load_toml_document(path)
    if not secrets:
        # Walk the document AST and replace secret-keyed string values.
        # (``redact_toml_dict`` operates in place.)
        redact_toml_dict(doc)

    if diff:
        # Convert to plain dict for the diff (we don't want comment
        # nodes in the diff calculus).  Then re-render as JSON for
        # output — diff doesn't try to preserve TOML formatting.
        live = _to_json_safe(json.loads(json.dumps(_to_plain(doc))))
        baseline = _config_defaults()
        delta = _diff_dicts(live, baseline)
        print(json.dumps(delta, indent=2, sort_keys=True, default=str))
        return

    print(tomlkit.dumps(doc))


def _to_plain(doc: Any) -> Any:
    """Best-effort tomlkit → plain Python (for the diff path)."""

    if isinstance(doc, dict):
        return {str(k): _to_plain(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_to_plain(v) for v in doc]
    return doc


@config_app.command("validate")
def cmd_validate(
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Validate ``file`` instead of the active config."),
    ] = None,
) -> None:
    """Round-trip the config through ``Config.load`` without writing."""

    target = file if file is not None else resolve_config_path()
    try:
        from corpus_forge.config import Config

        Config.load(config_path=target)
    except Exception as exc:
        ui_error(f"Invalid: {exc}")
        raise typer.Exit(code=1) from exc

    ui_ok(f"{target} validated")


@config_app.command("edit")
def cmd_edit() -> None:
    """Open ``$EDITOR`` on the config; validate on save; roll back if invalid."""

    path = resolve_config_path()
    if not path.exists():
        ui_error(f"No config at {path} — run `corpus-forge setup` first.")
        raise typer.Exit(code=1)

    editor = _resolve_editor()
    if editor is None:
        ui_error("Could not find an editor.  Set $EDITOR (or $VISUAL) to your preferred editor.")
        raise typer.Exit(code=1)

    # Take a backup so we can roll back on invalid edits.
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copyfile(path, backup)

    rc = subprocess.call([*editor, str(path)])
    if rc != 0:
        ui_warn(f"Editor exited with code {rc}; leaving config unchanged.")
        backup.unlink(missing_ok=True)
        raise typer.Exit(code=rc)

    try:
        from corpus_forge.config import Config

        Config.load(config_path=path)
    except Exception as exc:
        ui_error(f"Saved file is invalid — rolling back: {exc}")
        shutil.copyfile(backup, path)
        backup.unlink(missing_ok=True)
        raise typer.Exit(code=1) from exc

    backup.unlink(missing_ok=True)
    ui_ok(f"{path} saved")


def _resolve_editor() -> list[str] | None:
    """Resolve the editor command per common conventions.

    Order: ``$VISUAL`` → ``$EDITOR`` → platform fallback (``notepad``
    on Windows, ``vim`` / ``vi`` / ``nano`` elsewhere).  Returns ``None``
    when nothing on PATH resolves.
    """

    for env_var in ("VISUAL", "EDITOR"):
        value = os.environ.get(env_var)
        if value:
            # Editor strings may include flags (``"vim -p"``).  Split
            # on whitespace; the caller passes the rest as argv.
            parts = value.split()
            if parts and shutil.which(parts[0]):
                return parts

    candidates = ["notepad.exe"] if sys.platform.startswith("win") else ["vim", "vi", "nano"]
    for cand in candidates:
        path = shutil.which(cand)
        if path:
            return [path]
    return None


# ── Optional side-effect prompt (Wave 7 §10) ────────────────────────────


_SIDE_EFFECT_PREFIXES = (
    "embedders[",
    "ollama.base_url",
    "datasets[",
)


def _maybe_prompt_side_effect(key: str, *, non_interactive: bool) -> bool:
    """Return True if the user opted in to "Apply now" for a touched key.

    Touched keys: embedder fields, ``ollama.base_url``, source roots.
    Today's caller is a no-op (the prompt is informational; the actual
    "Apply now" routing happens in the embedder / ollama / source verbs).
    Centralised here so future verbs can hook in.
    """

    if not any(key.startswith(p) or key == p for p in _SIDE_EFFECT_PREFIXES):
        return False
    if non_interactive:
        ui_info(f"Side-effecting change to {key} — re-run the relevant verb.")
        return False
    return Confirm.ask(f"Apply now ({key} was touched)?", default=True)


# ── Misc ────────────────────────────────────────────────────────────────


def render_table_summary(items: list[dict], *, title: str, columns: list[tuple[str, str]]):
    """Render ``items`` as a ``rich.table.Table`` and print to the singleton console.

    ``columns`` is a list of ``(header, key)`` pairs.  Re-used by
    ``embedder list`` and ``dataset list`` so the visual identity stays
    consistent.
    """

    table = Table(title=title, title_style="h1", show_header=True)
    for header, _key in columns:
        table.add_column(header)
    for item in items:
        table.add_row(*[str(item.get(key, "")) for _h, key in columns])
    ui_console.print(table)


__all__ = [
    "ConfigWriteError",
    "_set_config_value_atomic",
    "_unset_config_value_atomic",
    "config_app",
    "load_toml_document",
    "render_table_summary",
    "resolve_config_path",
    "write_toml_atomic",
]
