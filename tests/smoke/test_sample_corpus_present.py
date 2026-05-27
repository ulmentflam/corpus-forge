"""Sample corpus presence guard (`examples/sample-corpus/`).

Rot-detector: pins the documented mini knowledge base so the README
Quickstart pointer doesn't dangle. Pure filesystem — no DB, no models,
no network — so it's independent of any DB-aware conftest.

Tests verify only presence/shape; the prose and data are free to evolve
as long as the documented files exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "examples" / "sample-corpus"

_DOCUMENTED_FILES = (
    "README.md",
    "notes/kickoff-meeting.md",
    "notes/architecture.md",
    "docs/faq.md",
    "data/metrics.csv",
    "config/settings.toml",
    "src/skycast.py",
)


def test_corpus_dir_exists() -> None:
    """The sample corpus lives at the canonical path."""
    assert CORPUS_ROOT.is_dir(), f"missing {CORPUS_ROOT}"


@pytest.mark.parametrize("relpath", _DOCUMENTED_FILES)
def test_documented_file_present(relpath: str) -> None:
    """Every file the README documents must exist and be non-empty."""
    path = CORPUS_ROOT / relpath
    assert path.is_file(), f"missing {path}"
    assert path.stat().st_size > 0, f"empty file {path}"


def test_skycast_module_is_valid_utf8_and_non_empty() -> None:
    """The linted Python module decodes as UTF-8 and carries content."""
    module = CORPUS_ROOT / "src" / "skycast.py"
    text = module.read_text(encoding="utf-8")
    assert text.strip(), "skycast.py is empty or whitespace-only"
