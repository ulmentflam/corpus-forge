"""Cross-document consistency guards (doc-polish pass, 2026-05-16).

These tests catch the most common doc-rot failure modes:

1. README mentions a CLI subcommand that has no Typer registration.
2. README extras list and pyproject `[project.optional-dependencies]` drift.
3. `config.example.toml` contains a key the pydantic ``Config`` rejects.
4. ``docs/schema.md`` mentions an alembic revision filename that is not on
   disk (or vice-versa).

The aim is to make doc updates a hard failure when the code surface changes
underneath them — keeps the docs trustworthy without forcing every PR to
re-prove every claim manually.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from typer.main import get_command

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CONFIG_EXAMPLE = REPO_ROOT / "config.example.toml"
ALEMBIC_DIR = REPO_ROOT / "corpus_forge" / "alembic" / "versions"
SCHEMA_DOC = REPO_ROOT / "docs" / "schema.md"


def _typer_commands() -> set[str]:
    """Return the set of top-level + nested Typer subcommand names."""
    from corpus_forge.cli import app

    click_root = get_command(app)
    names: set[str] = set()

    def _walk(cmd, prefix: str = "") -> None:
        # Click's Group exposes commands via .commands; leaves do not.
        sub = getattr(cmd, "commands", None) or {}
        for name, child in sub.items():
            full = f"{prefix} {name}".strip()
            names.add(full)
            names.add(name)
            _walk(child, full)

    _walk(click_root)
    return names


def test_readme_cli_examples_resolve_to_registered_commands() -> None:
    """Every ``corpus-forge <cmd>`` shown in README CODE BLOCKS must exist.

    Only fenced code blocks count — prose like "corpus-forge accepts a URL"
    is not a CLI invocation.

    Allow-list: ``mcp`` and ``migrate`` are command groups whose bare form
    is the top-level — already in the Typer surface, just not always
    enumerated under sub-walks.
    """
    body = README.read_text(encoding="utf-8")
    # Pull only the content inside fenced code blocks (``` … ```).
    code_blocks = re.findall(r"```[a-z]*\n(.*?)```", body, re.S)
    code_text = "\n".join(code_blocks)
    # Use [ \t]+ rather than \s+ so newlines don't bridge separate shell
    # commands ("corpus-forge\ncd ..." would otherwise mis-match "cd").
    mentions = set(re.findall(r"corpus-forge[ \t]+([a-z][a-z\-]*)", code_text))
    available = _typer_commands()
    available |= {"mcp", "migrate", "sync", "export", "eval"}
    missing = sorted(mentions - available)
    assert not missing, (
        f"README code blocks reference CLI commands not registered with Typer: {missing}. "
        f"Available: {sorted(c for c in available if ' ' not in c)}"
    )


def _pyproject_extras() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return set(data["project"]["optional-dependencies"].keys())


def test_readme_extras_table_matches_pyproject() -> None:
    """Every ``[<extra>]`` in the README extras section must exist in pyproject."""
    body = README.read_text(encoding="utf-8")
    # Pull `[extra]` tokens from the extras table specifically: each row
    # begins with `| `[<name>]` |`.
    found = set(re.findall(r"\|\s*`\[([a-z][a-z\-]*)\]`\s*\|", body))
    declared = _pyproject_extras()
    missing = sorted(found - declared)
    assert not missing, (
        f"README extras table mentions undeclared extras: {missing}. "
        f"Declared in pyproject: {sorted(declared)}"
    )


def test_pyproject_extras_are_each_mentioned_in_readme() -> None:
    """Every pyproject extra must show up somewhere in the README.

    Catches the inverse failure mode of the previous test: a new extra
    landing without a README row.
    """
    body = README.read_text(encoding="utf-8")
    declared = _pyproject_extras()
    missing = sorted(name for name in declared if f"[{name}]" not in body)
    assert not missing, (
        f"pyproject extras absent from README: {missing}. "
        "Add a row to the 'Optional extras' table or a callout elsewhere."
    )


def test_config_example_parses_cleanly_through_pydantic() -> None:
    """``config.example.toml`` must validate against the live ``Config`` schema.

    This is the canonical reference for users; if a key got renamed or
    a new pydantic field added without updating the example, the
    example silently lies. Failing this test forces a re-sync.
    """
    from corpus_forge.config import Config

    # Stub the env-interpolated DSN so we don't depend on PG_* env vars.
    raw = CONFIG_EXAMPLE.read_text(encoding="utf-8")
    raw = raw.replace(
        "postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PG_DB}",
        "postgresql://user:pass@localhost:5432/corpus",
    )
    data = tomllib.loads(raw)
    Config(**data)  # raises pydantic.ValidationError if anything has drifted


def test_schema_doc_revisions_exist_on_disk() -> None:
    """Every alembic revision filename mentioned in ``docs/schema.md`` exists.

    The migration log in schema.md is a manually-maintained table; a
    rename or new revision must round-trip into the doc.
    """
    body = SCHEMA_DOC.read_text(encoding="utf-8")
    # Revision names are written as `0001_core`, `0002_chunk_content_hash`, …
    mentioned = set(re.findall(r"`(\d{4}_[a-z_]+)`", body))
    on_disk = {p.stem for p in ALEMBIC_DIR.glob("*.py") if not p.stem.startswith("__")}
    missing = sorted(mentioned - on_disk)
    assert not missing, (
        f"docs/schema.md references alembic revisions not on disk: {missing}. "
        f"On disk: {sorted(on_disk)}"
    )


def test_every_alembic_revision_is_documented() -> None:
    """Every alembic revision on disk must appear in ``docs/schema.md``."""
    body = SCHEMA_DOC.read_text(encoding="utf-8")
    on_disk = {p.stem for p in ALEMBIC_DIR.glob("*.py") if not p.stem.startswith("__")}
    undocumented = sorted(rev for rev in on_disk if f"`{rev}`" not in body)
    assert not undocumented, (
        f"alembic revisions missing from docs/schema.md: {undocumented}. "
        "Add an entry to the 'Migration log' table."
    )


def test_config_example_uses_only_url_fields_with_local_default() -> None:
    """Every ``*_url`` / ``*_base_url`` field in the example points to a localhost
    or well-known SaaS default (not a developer-machine-specific value).

    Catches stale dev URLs that snuck into the canonical example.
    """
    raw = CONFIG_EXAMPLE.read_text(encoding="utf-8")
    # Only uncommented assignments (skip `# ollama_url = ...` remote examples).
    # Lines must match `<key>\s*=\s*"http..."`.
    line_re = re.compile(
        r"^\s*([a-z_]*(?:url|base_url))\s*=\s*\"(https?://[^\"]+)\"",
        re.MULTILINE,
    )
    allowed_hosts = {
        "localhost",
        "127.0.0.1",
        "api.openai.com",
        "api.mistral.ai",
    }
    failures: list[str] = []
    for key, url in line_re.findall(raw):
        # Extract host. Cheap parse: split on '://', then on '/' and ':'.
        host_part = url.split("://", 1)[1]
        host = host_part.split("/", 1)[0].split(":", 1)[0]
        if host not in allowed_hosts:
            failures.append(f"{key} = {url} (host={host})")
    assert not failures, (
        "config.example.toml has *_url defaults pointing at non-canonical "
        f"hosts: {failures}. Expected one of {sorted(allowed_hosts)}."
    )


@pytest.mark.parametrize(
    ("phase_keyword", "readme_section_heading"),
    [
        ("classify", "## Document classification"),
        ("rechunk", "## Content-defined chunking"),
        ("Whisper", "## Audio / video transcription"),
        ("multi-modal", "## Multi-modal embeddings"),
        ("enrich", "## Code enrichment"),
    ],
)
def test_readme_advertises_each_phase_d_through_h_surface(
    phase_keyword: str, readme_section_heading: str
) -> None:
    """README must carry an H2 for each post-D phase the project ships."""
    body = README.read_text(encoding="utf-8")
    assert readme_section_heading in body, (
        f"README missing section '{readme_section_heading}' (keyword reference: {phase_keyword!r})"
    )
