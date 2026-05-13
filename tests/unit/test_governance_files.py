"""Phase BR-01 governance file pins.

Locks the existence and shape of repo-root governance files added in the
beta release phase: ``CHANGELOG.md``, ``CONTRIBUTING.md``,
``CODE_OF_CONDUCT.md``, ``SECURITY.md``. Each file is checked for:

* presence
* non-empty content
* one or more well-known anchor strings appropriate to that file

These are intentionally loose anchor-string checks — they catch a future
rewrite that accidentally drops the file (or replaces it with a
placeholder) without freezing every paragraph in place.

Reference: ``/Users/evanowen/.claude/plans/crispy-yawning-crescent.md``
§Phase BR. License is Apache-2.0 (locked in CI-3).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    assert path.exists(), f"Missing governance file: {path.relative_to(REPO_ROOT)}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"Empty governance file: {path.relative_to(REPO_ROOT)}"
    return text


# ── LICENSE (already on disk from CI-3; re-verify here so a future rewrite
#    that swaps Apache for MIT trips this suite, not just CI-3 pins) ──────


def test_license_file_present_and_apache() -> None:
    text = _read(REPO_ROOT / "LICENSE")
    assert "Apache License" in text, "LICENSE must be Apache 2.0 (not MIT)"
    assert "Version 2.0" in text, "LICENSE must declare Apache Version 2.0"


# ── CHANGELOG.md ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def changelog_text() -> str:
    return _read(REPO_ROOT / "CHANGELOG.md")


def test_changelog_keep_a_changelog_anchor(changelog_text: str) -> None:
    """Header should reference the Keep a Changelog convention."""
    assert "Keep a Changelog" in changelog_text, (
        "CHANGELOG must reference 'Keep a Changelog' convention in its header"
    )


def test_changelog_lists_0_1_0b1_entry(changelog_text: str) -> None:
    """The first beta release section must exist."""
    # Allow either an ISO date or a placeholder MM-DD form.
    assert re.search(
        r"^##\s+\[0\.1\.0b1\]\s+-\s+\d{4}-\d{2}-\d{2}\s*$",
        changelog_text,
        flags=re.MULTILINE,
    ), "CHANGELOG must contain a '## [0.1.0b1] - YYYY-MM-DD' release header"


def test_changelog_summarises_beta_milestone(changelog_text: str) -> None:
    """The 0.1.0b1 entry should mention each phase block that landed in beta."""
    expected_anchors = (
        # SQLite backend (Phase B)
        "SQLite",
        # CI hardening (CI-1..CI-3)
        "CI",
        # Retrieval surface (R1..R5)
        "MCP",
        # Claude integration / skill (CS)
        "Claude",
    )
    for anchor in expected_anchors:
        assert anchor in changelog_text, f"CHANGELOG 0.1.0b1 summary should mention '{anchor}'"


# ── CONTRIBUTING.md ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def contributing_text() -> str:
    return _read(REPO_ROOT / "CONTRIBUTING.md")


def test_contributing_mentions_dev_setup(contributing_text: str) -> None:
    assert "make dev" in contributing_text, (
        "CONTRIBUTING must reference `make dev` for the developer-install path"
    )


def test_contributing_mentions_make_ci(contributing_text: str) -> None:
    assert "make ci" in contributing_text, (
        "CONTRIBUTING must reference `make ci` as the gate before pushing"
    )


def test_contributing_mentions_commit_style(contributing_text: str) -> None:
    """Either Conventional Commits / [role] prefix style must be documented."""
    lowered = contributing_text.lower()
    assert "commit" in lowered, "CONTRIBUTING must mention commits"
    # Either the existing in-repo convention `[role] phase-...` or a generic
    # branching / commit-message section is acceptable.
    assert (
        "[role]" in contributing_text
        or "conventional commits" in lowered
        or "commit message" in lowered
    ), "CONTRIBUTING must describe commit-message style"


# ── CODE_OF_CONDUCT.md ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def coc_text() -> str:
    return _read(REPO_ROOT / "CODE_OF_CONDUCT.md")


def test_coc_is_contributor_covenant(coc_text: str) -> None:
    assert "Contributor Covenant" in coc_text, (
        "CODE_OF_CONDUCT must be based on the Contributor Covenant"
    )


def test_coc_declares_v2_1(coc_text: str) -> None:
    # Accept "version 2.1", "v2.1", "Contributor Covenant, version 2.1", etc.
    assert re.search(r"2\.1", coc_text), (
        "CODE_OF_CONDUCT must declare Contributor Covenant version 2.1"
    )


def test_coc_lists_contact_email(coc_text: str) -> None:
    assert "evan@qwerky.ai" in coc_text, (
        "CODE_OF_CONDUCT must list evan@qwerky.ai as the enforcement contact"
    )


# ── SECURITY.md ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def security_text() -> str:
    return _read(REPO_ROOT / "SECURITY.md")


def test_security_has_reporting_contact(security_text: str) -> None:
    assert "evan@qwerky.ai" in security_text, (
        "SECURITY must list evan@qwerky.ai for vulnerability reports"
    )


def test_security_has_supported_versions_table(security_text: str) -> None:
    """A '| Version |' table column is the keep-a-security convention."""
    lowered = security_text.lower()
    assert "supported" in lowered, "SECURITY must declare a Supported Versions section"
    assert "0.1" in security_text, "SECURITY must list the 0.1.x line as the supported beta version"


def test_security_describes_reporting_flow(security_text: str) -> None:
    lowered = security_text.lower()
    assert any(kw in lowered for kw in ("report", "disclose", "vulnerability")), (
        "SECURITY must describe how to report a vulnerability"
    )
