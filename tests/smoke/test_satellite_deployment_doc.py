"""E-01 — Satellite deployment doc (`docs/deployment-satellite.md`).

Rot-detector: pins the required section structure and a handful of
load-bearing names so the doc and the multi-host deployment topology
don't silently diverge.

Tests verify only shape/presence — the prose is free to evolve as long
as these invariants hold.

Mirrors the pattern in tests/unit/test_claude_integration_doc.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "deployment-satellite.md"

_REQUIRED_H2 = (
    r"Prerequisites",
    r"Bootstrap Postgres",
    r"Configure host_id",
    r"Enable sync",
    r"Verify",
)


def test_doc_exists() -> None:
    """Satellite deployment guide lives at the canonical path."""
    assert DOC_PATH.is_file(), f"missing {DOC_PATH}"


def test_doc_has_required_h2_sections() -> None:
    """Every required H2 must be present (exact match, multiline regex)."""
    body = DOC_PATH.read_text(encoding="utf-8")
    for heading in _REQUIRED_H2:
        pattern = re.compile(rf"^## {re.escape(heading)}$", re.MULTILINE)
        assert pattern.search(body), f"docs/deployment-satellite.md missing '## {heading}'"


def test_doc_references_migrate_command() -> None:
    """Bootstrap Postgres section must reference the corpus-forge migrate command (D-07 rewire)."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "corpus-forge migrate" in body, (
        "doc must reference `corpus-forge migrate` in Bootstrap Postgres section"
    )


def test_doc_references_sync_status_command() -> None:
    """Verify section must reference the corpus-forge sync status command."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "corpus-forge sync status" in body, (
        "doc must reference `corpus-forge sync status` in Verify section"
    )


def test_doc_references_host_id_path() -> None:
    """Configure host_id section must name the canonical config path."""
    body = DOC_PATH.read_text(encoding="utf-8")
    assert "~/.config/corpus-forge/host_id" in body, (
        "doc must reference `~/.config/corpus-forge/host_id` in Configure host_id section"
    )
