"""Phase L Wave 2 — static regression: no ``typer.echo`` outside ``ui/``.

After the Wave-2 retrofit, every user-visible call in ``corpus_forge/``
flows through either ``ui.ok`` / ``ui.warn`` / ``ui.error`` / ``ui.info``
(themed status lines), ``ui.title`` (section banners), Rich's
``console.print`` (neutral output), or plain ``print()`` (data lines on
stdout for piping).  This test locks the contract: a future change that
reintroduces ``typer.echo`` / ``typer.secho`` / ``typer.prompt`` /
``typer.confirm`` outside the ``ui/`` package fails CI loudly.

The grep is deliberately textual rather than AST-based so it catches
strings inside comments and docstrings too — any reach for ``typer.*``
IO helpers should at minimum be flagged for code review.

The single exclusion is ``corpus_forge/ui/`` (Wave 1 owns those IO
primitives).  ``corpus_forge/mcp/`` is *included* in the sweep — the
MCP transport uses raw stdin/stdout for JSON-RPC, so it has no business
calling ``typer.*`` either.
"""

from __future__ import annotations

import re
from pathlib import Path

# Match ``typer.echo``, ``typer.secho``, ``typer.prompt``, ``typer.confirm``
# as whole identifier suffixes; the ``\b`` boundary prevents matching
# longer names like ``typer.echo_via_pager`` (none exist today, but the
# regex stays narrow).
_TYPER_IO_RE = re.compile(r"\btyper\.(echo|secho|prompt|confirm)\b")

# Directories under ``corpus_forge/`` whose ``typer.*`` usage is
# intentional and exempt from the sweep.
_EXEMPT_PARTS: tuple[tuple[str, ...], ...] = (
    ("corpus_forge", "ui"),  # Wave 1 owns the IO primitives.
)


def _package_root() -> Path:
    """Resolve ``corpus_forge/`` relative to the test file."""
    return Path(__file__).resolve().parents[2] / "corpus_forge"


def _is_exempt(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root.parent).parts
    return any(rel_parts[: len(exempt)] == exempt for exempt in _EXEMPT_PARTS)


def test_no_typer_io_helpers_outside_ui() -> None:
    """Every ``.py`` under ``corpus_forge/`` (except ``ui/``) must be
    free of ``typer.echo`` / ``typer.secho`` / ``typer.prompt`` /
    ``typer.confirm`` call sites.
    """
    root = _package_root()
    assert root.is_dir(), f"corpus_forge package root not found at {root}"

    hits: list[tuple[Path, int, str]] = []
    for py_file in sorted(root.rglob("*.py")):
        if _is_exempt(py_file, root):
            continue
        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _TYPER_IO_RE.search(line):
                hits.append((py_file, lineno, line.strip()))

    if hits:
        formatted = "\n".join(
            f"  {path.relative_to(root.parent)}:{lineno}: {snippet}"
            for path, lineno, snippet in hits
        )
        raise AssertionError(
            "Phase L Wave 2 contract: no `typer.echo` / `typer.secho` / "
            "`typer.prompt` / `typer.confirm` calls outside `corpus_forge/ui/`. "
            "Use the helpers in `corpus_forge.ui` (ok/warn/error/info/title) "
            "for status lines, plain `print()` for data on stdout, and "
            "`ui.Prompt.ask` / `ui.Confirm.ask` for interactive prompts. "
            f"Offending sites:\n{formatted}"
        )
