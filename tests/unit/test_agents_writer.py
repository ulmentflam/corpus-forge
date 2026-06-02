"""Unit tests for ``corpus_forge.agents.writer`` — T5 (redirected).

Covers:
- ``write_corpus_agents_dir`` writes all four files into ``.corpus-agents/``.
- ``maybe_write_root_agents_md`` creates root AGENTS.md ONLY when absent.
- Root AGENTS.md is NEVER overwritten — even with ``--force``.
- ``ensure_gitignore_entry`` appends ``.corpus-agents/`` once; idempotent.
- ``--no-gitignore`` / ``--no-root-write`` skip behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

from corpus_forge.agents.synthesizer import ChunkRef
from corpus_forge.agents.writer import (
    WriteResult,
    ensure_gitignore_entry,
    maybe_write_root_agents_md,
    write_corpus_agents_dir,
)

# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


def _meta() -> dict[str, object]:
    return {
        "tool": "corpus-forge agents init",
        "version": "0.1.0",
        "generated_at": "2026-06-02T00:00:00Z",
    }


def _citations() -> list[ChunkRef]:
    return [
        ChunkRef(chunk_id=1, source_uri="file:///a/foo.py", score=0.9),
        ChunkRef(chunk_id=2, source_uri="file:///a/bar.py", score=0.8),
    ]


PRIVATE_BODY = "# Private AGENTS.md\nGrounded in chunk_id=1 etc.\n"
SHAREABLE_BODY = "# Shareable AGENTS.md\nNo citations.\n"


# ─────────────────────────────────────────────────────────────────────────
# write_corpus_agents_dir
# ─────────────────────────────────────────────────────────────────────────


def test_write_corpus_agents_dir_writes_all_four_files(tmp_path: Path) -> None:
    out = tmp_path / ".corpus-agents"
    result = write_corpus_agents_dir(
        out,
        private_md=PRIVATE_BODY,
        shareable_md=SHAREABLE_BODY,
        citations=_citations(),
        meta=_meta(),
        force=False,
    )
    assert isinstance(result, WriteResult)
    # Four files
    for name in ("AGENTS.md", "shareable.md", "citations.json", "meta.json"):
        path = out / name
        assert path.exists(), f"missing {path}"
    # Private body
    assert (out / "AGENTS.md").read_text(encoding="utf-8") == PRIVATE_BODY
    # Shareable body
    assert (out / "shareable.md").read_text(encoding="utf-8") == SHAREABLE_BODY
    # citations.json is parseable JSON and reflects our ChunkRef list
    cit = json.loads((out / "citations.json").read_text(encoding="utf-8"))
    assert isinstance(cit, list)
    assert len(cit) == 2
    assert {c["chunk_id"] for c in cit} == {1, 2}
    # meta.json round-trips
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["tool"] == "corpus-forge agents init"


def test_write_corpus_agents_dir_returns_paths_list(tmp_path: Path) -> None:
    out = tmp_path / ".corpus-agents"
    result = write_corpus_agents_dir(
        out,
        private_md=PRIVATE_BODY,
        shareable_md=SHAREABLE_BODY,
        citations=[],
        meta=_meta(),
        force=False,
    )
    assert hasattr(result, "paths_written")
    paths = result.paths_written
    # All four should appear
    names = {Path(p).name for p in paths}
    assert names == {"AGENTS.md", "shareable.md", "citations.json", "meta.json"}


def test_write_corpus_agents_dir_force_overwrites_existing(tmp_path: Path) -> None:
    out = tmp_path / ".corpus-agents"
    out.mkdir()
    (out / "AGENTS.md").write_text("OLD\n", encoding="utf-8")
    (out / "shareable.md").write_text("OLD\n", encoding="utf-8")

    write_corpus_agents_dir(
        out,
        private_md=PRIVATE_BODY,
        shareable_md=SHAREABLE_BODY,
        citations=[],
        meta=_meta(),
        force=True,
    )
    assert (out / "AGENTS.md").read_text(encoding="utf-8") == PRIVATE_BODY
    assert (out / "shareable.md").read_text(encoding="utf-8") == SHAREABLE_BODY


def test_write_corpus_agents_dir_without_force_when_existing_returns_existed_before(
    tmp_path: Path,
) -> None:
    """Without --force, existing files in .corpus-agents/ are still overwritten
    (it's the principal's own scratchpad), but result records existed_before=True."""

    out = tmp_path / ".corpus-agents"
    out.mkdir()
    (out / "AGENTS.md").write_text("OLD\n", encoding="utf-8")

    result = write_corpus_agents_dir(
        out,
        private_md=PRIVATE_BODY,
        shareable_md=SHAREABLE_BODY,
        citations=[],
        meta=_meta(),
        force=False,
    )
    # The contract: it's still safe to refresh .corpus-agents/* — that's the principal's scratchpad.
    # The flag exists so we can distinguish the first-vs-refresh case in the result.
    assert result.existed_before is True


# ─────────────────────────────────────────────────────────────────────────
# maybe_write_root_agents_md
# ─────────────────────────────────────────────────────────────────────────


def test_maybe_write_root_agents_md_creates_when_absent(tmp_path: Path) -> None:
    """Fresh project → root AGENTS.md is created from shareable."""

    created = maybe_write_root_agents_md(tmp_path, SHAREABLE_BODY, enabled=True)
    assert created is True
    root_agents = tmp_path / "AGENTS.md"
    assert root_agents.exists()
    assert root_agents.read_text(encoding="utf-8") == SHAREABLE_BODY


def test_maybe_write_root_agents_md_skips_when_present(tmp_path: Path) -> None:
    """Existing AGENTS.md → leave untouched, return False."""

    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text("USER-AUTHORED\n", encoding="utf-8")
    created = maybe_write_root_agents_md(tmp_path, SHAREABLE_BODY, enabled=True)
    assert created is False
    # Untouched
    assert root_agents.read_text(encoding="utf-8") == "USER-AUTHORED\n"


def test_maybe_write_root_agents_md_disabled_flag_skips(tmp_path: Path) -> None:
    """enabled=False → never writes, even when absent."""

    created = maybe_write_root_agents_md(tmp_path, SHAREABLE_BODY, enabled=False)
    assert created is False
    assert not (tmp_path / "AGENTS.md").exists()


def test_force_never_overwrites_root_agents_md(tmp_path: Path) -> None:
    """CRITICAL safety: there is NO API path that overwrites root AGENTS.md.

    The writer module exposes only ``maybe_write_root_agents_md`` for the
    root file, and its semantics are 'create-when-absent'. There is no
    ``force_write_root_agents_md`` — that's the safety contract.
    """

    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text("SACRED-USER-CONTENT\n", encoding="utf-8")
    # Even calling the API twice (once enabled, once not), no write happens.
    assert maybe_write_root_agents_md(tmp_path, SHAREABLE_BODY, enabled=True) is False
    assert maybe_write_root_agents_md(tmp_path, SHAREABLE_BODY, enabled=False) is False
    assert root_agents.read_text(encoding="utf-8") == "SACRED-USER-CONTENT\n"


# ─────────────────────────────────────────────────────────────────────────
# ensure_gitignore_entry
# ─────────────────────────────────────────────────────────────────────────


def test_ensure_gitignore_creates_when_missing(tmp_path: Path) -> None:
    """No .gitignore yet → create one with the .corpus-agents/ entry."""

    appended = ensure_gitignore_entry(tmp_path, enabled=True)
    assert appended is True
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    contents = gitignore.read_text(encoding="utf-8")
    assert ".corpus-agents/" in contents


def test_ensure_gitignore_appends_when_missing_entry(tmp_path: Path) -> None:
    """Existing .gitignore without our entry → append."""

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("__pycache__/\n.venv/\n", encoding="utf-8")
    appended = ensure_gitignore_entry(tmp_path, enabled=True)
    assert appended is True
    contents = gitignore.read_text(encoding="utf-8")
    assert "__pycache__/" in contents
    assert ".venv/" in contents
    assert ".corpus-agents/" in contents


def test_ensure_gitignore_idempotent(tmp_path: Path) -> None:
    """Running twice does not duplicate the entry."""

    ensure_gitignore_entry(tmp_path, enabled=True)
    first = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    appended_second = ensure_gitignore_entry(tmp_path, enabled=True)
    second = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert appended_second is False
    assert first == second
    assert second.count(".corpus-agents/") == 1


def test_ensure_gitignore_disabled_skips(tmp_path: Path) -> None:
    """enabled=False → don't create or modify .gitignore."""

    appended = ensure_gitignore_entry(tmp_path, enabled=False)
    assert appended is False
    assert not (tmp_path / ".gitignore").exists()


def test_ensure_gitignore_disabled_preserves_existing(tmp_path: Path) -> None:
    """enabled=False with existing .gitignore → leave it unchanged."""

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("__pycache__/\n", encoding="utf-8")
    ensure_gitignore_entry(tmp_path, enabled=False)
    assert gitignore.read_text(encoding="utf-8") == "__pycache__/\n"
