"""Rot-detector for the three new deployment docs and README cross-links.

We do NOT fetch URLs over HTTP; the test asserts that each markdown link
target resolves to a real file in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "docs" / "deployment"
README = REPO_ROOT / "README.md"

POSTGRES_MD = DEPLOY_DIR / "postgres.md"
DOCKER_MD = DEPLOY_DIR / "docker.md"
LXC_MD = DEPLOY_DIR / "lxc.md"


@pytest.fixture(autouse=True)
def _require_docs() -> None:
    for path in (POSTGRES_MD, DOCKER_MD, LXC_MD, README):
        if not path.exists():
            pytest.fail(f"Expected {path}; not found.")


def _h2_sections(path: Path) -> set[str]:
    """Return the set of normalised H2 headings in a markdown file."""
    out: set[str] = set()
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            out.add(line[3:].strip().lower())
    return out


def test_postgres_md_has_expected_sections() -> None:
    sections = _h2_sections(POSTGRES_MD)
    expected = {
        "prerequisites",
        "quick start",
        "manual procedure",
        "tuning",
        "backups",
        "troubleshooting",
    }
    missing = expected - sections
    assert not missing, f"postgres.md missing H2 sections: {missing}; have {sections}"


def test_postgres_md_includes_sizing_table() -> None:
    """The Tuning section should carry the LXC sizing table."""
    body = POSTGRES_MD.read_text()
    # We don't pin exact column layout, but the worked example numbers
    # (49 GB total footprint, 16 GB RAM recommendation) should be there.
    assert "49" in body, "missing total footprint figure in tuning section"
    assert "16" in body, "missing recommended RAM figure"


def test_docker_md_has_expected_sections() -> None:
    sections = _h2_sections(DOCKER_MD)
    expected = {"quick start", "health verification", "production caveats", "backups"}
    missing = expected - sections
    assert not missing, f"docker.md missing H2 sections: {missing}; have {sections}"


def test_docker_md_yells_about_default_password() -> None:
    body = DOCKER_MD.read_text()
    # CHANGEME marker reinforces the .env.postgres.example contract.
    assert "CHANGEME" in body.upper(), "docker.md should call out the placeholder password"


def test_lxc_md_has_expected_sections() -> None:
    sections = _h2_sections(LXC_MD)
    expected = {"proxmox lxc create", "inside the lxc", "pitfalls", "tailscale", "backups combo"}
    missing = expected - sections
    assert not missing, f"lxc.md missing H2 sections: {missing}; have {sections}"


def test_lxc_md_documents_uid_mapping_pitfall() -> None:
    body = LXC_MD.read_text().lower()
    # Phase doc calls out unprivileged uid mapping at 100999. Allow
    # either the exact number or the word "uid" in the pitfalls section.
    assert "100999" in body or "uid map" in body or "uid mapping" in body


@pytest.fixture
def readme_text() -> str:
    return README.read_text()


def _link_pattern(target: str) -> re.Pattern[str]:
    # Match either an inline link [text](docs/deployment/X.md) or a
    # reference-style link. Allow optional anchor.
    escaped = re.escape(target)
    return re.compile(rf"\(\s*{escaped}(?:#[A-Za-z0-9\-_]+)?\s*\)")


def test_readme_links_to_postgres_md(readme_text: str) -> None:
    assert _link_pattern("docs/deployment/postgres.md").search(readme_text), (
        "README is missing a link to docs/deployment/postgres.md"
    )


def test_readme_links_to_docker_md(readme_text: str) -> None:
    assert _link_pattern("docs/deployment/docker.md").search(readme_text), (
        "README is missing a link to docs/deployment/docker.md"
    )


def test_readme_links_to_lxc_md(readme_text: str) -> None:
    assert _link_pattern("docs/deployment/lxc.md").search(readme_text), (
        "README is missing a link to docs/deployment/lxc.md"
    )
