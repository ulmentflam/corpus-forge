"""Phase BR-04 cliff.toml pin.

Validates the git-cliff configuration used by ``release.yml`` to render
the changelog snippet for each release. We only assert structural shape;
the actual rendering is exercised manually by the principal during the
BR-06 sweep.

Reference: master plan §Phase BR (config-only git-cliff).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIFF_PATH = REPO_ROOT / "cliff.toml"


@pytest.fixture(scope="module")
def cliff() -> dict:
    assert CLIFF_PATH.exists(), f"Missing {CLIFF_PATH.relative_to(REPO_ROOT)}"
    with CLIFF_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_cliff_has_changelog_section(cliff: dict) -> None:
    assert "changelog" in cliff, "cliff.toml must declare a [changelog] section"
    cl = cliff["changelog"]
    # at minimum a body template
    assert "body" in cl, "[changelog] must declare a body template"
    assert isinstance(cl["body"], str) and cl["body"].strip(), (
        "[changelog].body must be a non-empty template string"
    )


def test_cliff_has_git_section(cliff: dict) -> None:
    assert "git" in cliff, "cliff.toml must declare a [git] section"
    g = cliff["git"]
    # conventional-commits or commit_parsers must be present.
    assert (
        "conventional_commits" in g or "commit_parsers" in g
    ), "[git] must declare conventional_commits or commit_parsers"


def test_cliff_tag_pattern_accepts_prerelease(cliff: dict) -> None:
    """The tag regex must match prerelease tags like v0.1.0b1."""
    g = cliff["git"]
    pattern = g.get("tag_pattern", "")
    assert pattern, "[git].tag_pattern must be set so cliff scans v* tags"
    # The pattern should at least allow a `v` prefix.
    assert "v" in pattern, f"tag_pattern must accept v-prefixed tags; got {pattern!r}"


def test_cliff_commit_parsers_cover_common_types(cliff: dict) -> None:
    """Commit parsers should at minimum recognise feat/fix or the [role] prefix."""
    g = cliff["git"]
    parsers = g.get("commit_parsers", [])
    assert isinstance(parsers, list) and parsers, (
        "[git].commit_parsers must be a non-empty list"
    )
    # Either conventional-commits style or the in-repo [role] prefix must
    # appear in at least one parser pattern.
    blob = " ".join(str(p) for p in parsers).lower()
    assert any(
        kw in blob
        for kw in (
            "feat",
            "fix",
            "tdd-tester",
            "tdd-coder",
            "tdd-principal",
            "tdd-",
        )
    ), (
        "[git].commit_parsers should match feat/fix/[tdd-*] commit prefixes"
    )
