"""On-disk writers for ``.corpus-agents/*`` + root AGENTS.md — T5 (redirected).

Three public functions:

- :func:`write_corpus_agents_dir` — write the four files in
  ``.corpus-agents/`` (``AGENTS.md``, ``shareable.md``, ``citations.json``,
  ``meta.json``). ``--force`` overwrites these files; without ``--force``
  they are still refreshed (it's the principal's scratchpad), but the
  result records ``existed_before`` so the CLI can warn.
- :func:`maybe_write_root_agents_md` — write ``<root>/AGENTS.md`` ONLY
  when the file is absent and ``enabled=True``. Never overwrites; the
  root file is the user's commitment surface.
- :func:`ensure_gitignore_entry` — append ``.corpus-agents/`` to
  ``<root>/.gitignore`` once, idempotently.

CRITICAL safety contract: there is no API in this module that
overwrites the project-root ``AGENTS.md``. ``--force`` applies only to
``.corpus-agents/*``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.agents.synthesizer import ChunkRef


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────


_GITIGNORE_ENTRY = ".corpus-agents/"


# ─────────────────────────────────────────────────────────────────────────
# Public surface
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WriteResult:
    """Result of a :func:`write_corpus_agents_dir` call."""

    dir_path: Path
    paths_written: list[Path] = field(default_factory=list)
    existed_before: bool = False


def _citations_to_json(citations: list[ChunkRef]) -> str:
    """Serialize ``[ChunkRef]`` to a compact pretty JSON list."""

    payload = [
        {
            "chunk_id": int(c.chunk_id),
            "source_uri": str(c.source_uri),
            "score": float(c.score),
        }
        for c in citations
    ]
    return json.dumps(payload, indent=2, sort_keys=True)


def _meta_to_json(meta: dict[str, Any]) -> str:
    return json.dumps(meta, indent=2, sort_keys=True, default=str)


def write_corpus_agents_dir(
    dir_path: Path,
    *,
    private_md: str,
    shareable_md: str,
    citations: list[ChunkRef],
    meta: dict[str, Any],
    force: bool,
) -> WriteResult:
    """Write the four files in ``.corpus-agents/``.

    Args:
        dir_path: target directory (typically ``<root>/.corpus-agents/``).
        private_md: full corpus-grounded synthesis body.
        shareable_md: sanitized body intended for the root AGENTS.md.
        citations: list of :class:`ChunkRef` from the private pass.
        meta: free-form metadata block (tool version, run timestamp,
            etc.) serialized as ``meta.json``.
        force: when True, overwrite without warning. Without force the
            files are still rewritten — this directory is the
            principal's scratchpad — but ``existed_before=True`` is
            surfaced in the result so the CLI can prompt.
    """

    dir_path = Path(dir_path)
    existed_before = dir_path.exists() and any(dir_path.iterdir() if dir_path.is_dir() else [])
    dir_path.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []

    # 1. private AGENTS.md
    agents_path = dir_path / "AGENTS.md"
    agents_path.write_text(private_md, encoding="utf-8")
    paths.append(agents_path)

    # 2. shareable.md
    shareable_path = dir_path / "shareable.md"
    shareable_path.write_text(shareable_md, encoding="utf-8")
    paths.append(shareable_path)

    # 3. citations.json
    citations_path = dir_path / "citations.json"
    citations_path.write_text(_citations_to_json(citations), encoding="utf-8")
    paths.append(citations_path)

    # 4. meta.json
    meta_path = dir_path / "meta.json"
    meta_path.write_text(_meta_to_json(meta), encoding="utf-8")
    paths.append(meta_path)

    # ``force`` is part of the API contract; record it on the result so
    # callers can audit. It does not change disk semantics for this
    # directory (the principal owns it), but the parameter exists so
    # the CLI's ``--force`` plumbs through uniformly.
    _ = force

    return WriteResult(
        dir_path=dir_path,
        paths_written=paths,
        existed_before=existed_before,
    )


def maybe_write_root_agents_md(
    project_root: Path,
    shareable_md: str,
    *,
    enabled: bool = True,
) -> bool:
    """Write ``<project_root>/AGENTS.md`` from ``shareable_md`` IFF absent.

    Never overwrites. Returns True iff a fresh file was created.

    Args:
        project_root: the project root directory.
        shareable_md: sanitized body to seed the root file with.
        enabled: when False, do nothing — used by ``--no-root-write``.
    """

    if not enabled:
        return False
    project_root = Path(project_root)
    root_agents = project_root / "AGENTS.md"
    if root_agents.exists():
        # Sacred — user's commitment surface.
        return False
    project_root.mkdir(parents=True, exist_ok=True)
    root_agents.write_text(shareable_md, encoding="utf-8")
    return True


def ensure_gitignore_entry(
    project_root: Path,
    *,
    enabled: bool = True,
) -> bool:
    """Append ``.corpus-agents/`` to ``<project_root>/.gitignore`` if missing.

    Idempotent: re-running this function does not duplicate the entry.

    Returns True iff the file was written (created or appended); False
    when the entry was already present, when ``enabled=False``, or when
    no write was needed.

    Args:
        project_root: the project root directory.
        enabled: when False, do nothing — used by ``--no-gitignore``.
    """

    if not enabled:
        return False
    project_root = Path(project_root)
    gitignore = project_root / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        # Match the literal line; check word-bounded to avoid false hits.
        lines = [ln.strip() for ln in existing.splitlines()]
        if _GITIGNORE_ENTRY in lines or _GITIGNORE_ENTRY.rstrip("/") in lines:
            return False
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        new_body = existing + suffix + _GITIGNORE_ENTRY + "\n"
        gitignore.write_text(new_body, encoding="utf-8")
        return True

    project_root.mkdir(parents=True, exist_ok=True)
    gitignore.write_text(_GITIGNORE_ENTRY + "\n", encoding="utf-8")
    return True
