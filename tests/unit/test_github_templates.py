"""Phase BR-02 GitHub issue/PR template pins.

Validates the `.github/ISSUE_TEMPLATE/*` form-syntax YAML files, the
`.github/PULL_REQUEST_TEMPLATE.md` body, and the `.github/FUNDING.yml`
placeholder file. Dependabot is covered separately in
``test_dependabot_config.py``.

Reference: master plan §Phase BR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
ISSUE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
PR_TEMPLATE = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
FUNDING = REPO_ROOT / ".github" / "FUNDING.yml"


# ── helper ────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    assert path.exists(), f"Missing: {path.relative_to(REPO_ROOT)}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"Empty: {path.relative_to(REPO_ROOT)}"
    return yaml.safe_load(text)


# ── bug_report.yml ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def bug_report() -> dict:
    return _load_yaml(ISSUE_DIR / "bug_report.yml")


def test_bug_report_is_github_form(bug_report: dict) -> None:
    """GitHub issue forms require `name`, `description`, `body`."""
    for key in ("name", "description", "body"):
        assert key in bug_report, f"bug_report.yml missing required key: {key}"
    assert isinstance(bug_report["body"], list), "bug_report `body` must be a list"
    assert len(bug_report["body"]) >= 2, (
        "bug_report should declare at least 2 fields (description + repro)"
    )


def test_bug_report_has_labels(bug_report: dict) -> None:
    assert "labels" in bug_report, (
        "bug_report.yml should pre-tag with at least one label (e.g. 'bug')"
    )
    labels = bug_report["labels"]
    assert isinstance(labels, list) and labels, "labels must be a non-empty list"
    assert any("bug" in str(label).lower() for label in labels), (
        "bug_report should include a label like 'bug' or 'triage'"
    )


# ── feature_request.yml ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def feature_request() -> dict:
    return _load_yaml(ISSUE_DIR / "feature_request.yml")


def test_feature_request_is_github_form(feature_request: dict) -> None:
    for key in ("name", "description", "body"):
        assert key in feature_request, f"feature_request.yml missing required key: {key}"
    assert isinstance(feature_request["body"], list)
    assert len(feature_request["body"]) >= 2


def test_feature_request_has_enhancement_label(feature_request: dict) -> None:
    labels = feature_request.get("labels", [])
    assert isinstance(labels, list) and labels, "feature_request must declare non-empty labels list"
    joined = " ".join(str(label).lower() for label in labels)
    assert "enhancement" in joined or "feature" in joined, (
        "feature_request should include an 'enhancement' or 'feature' label"
    )


# ── config.yml (blank issues disabled) ────────────────────────────────────


@pytest.fixture(scope="module")
def issue_config() -> dict:
    return _load_yaml(ISSUE_DIR / "config.yml")


def test_issue_config_disables_blank_issues(issue_config: dict) -> None:
    assert issue_config.get("blank_issues_enabled") is False, (
        ".github/ISSUE_TEMPLATE/config.yml must set blank_issues_enabled: false"
    )


# ── PULL_REQUEST_TEMPLATE.md ──────────────────────────────────────────────


def test_pr_template_present_and_meaningful() -> None:
    assert PR_TEMPLATE.exists(), f"Missing {PR_TEMPLATE.relative_to(REPO_ROOT)}"
    text = PR_TEMPLATE.read_text(encoding="utf-8")
    assert text.strip(), "PR template must not be empty"
    # The template should at least invite a summary + a test/verify note.
    lowered = text.lower()
    assert "summary" in lowered, "PR template should ask for a summary section"
    assert any(kw in lowered for kw in ("test", "verify", "checklist")), (
        "PR template should ask for tests / a verification checklist"
    )


# ── FUNDING.yml ───────────────────────────────────────────────────────────


def test_funding_file_present() -> None:
    """FUNDING.yml is optional but Phase BR ships it (even as a placeholder)."""
    assert FUNDING.exists(), f"Missing {FUNDING.relative_to(REPO_ROOT)}"
    # Empty FUNDING (just a comment) is fine; just guard the file exists.
    text = FUNDING.read_text(encoding="utf-8")
    # Must at least parse as YAML (a pure-comment file parses to None).
    parsed = yaml.safe_load(text)
    assert parsed is None or isinstance(parsed, dict), (
        "FUNDING.yml must parse as YAML (dict or empty)"
    )
