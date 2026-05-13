"""Phase BR-02 dependabot.yml pin.

Validates ``.github/dependabot.yml``:

* version 2
* at least two ecosystems: ``pip`` and ``github-actions``
* each ecosystem schedules ``weekly``
* each ecosystem has a directory configured

Reference: master plan §Phase BR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPENDABOT_PATH = REPO_ROOT / ".github" / "dependabot.yml"


@pytest.fixture(scope="module")
def dependabot() -> dict:
    assert DEPENDABOT_PATH.exists(), f"Missing {DEPENDABOT_PATH.relative_to(REPO_ROOT)}"
    text = DEPENDABOT_PATH.read_text(encoding="utf-8")
    assert text.strip(), "dependabot.yml must not be empty"
    return yaml.safe_load(text)


def test_dependabot_version_2(dependabot: dict) -> None:
    assert dependabot.get("version") == 2, "dependabot.yml must declare version: 2"


def test_dependabot_has_updates_array(dependabot: dict) -> None:
    updates = dependabot.get("updates")
    assert isinstance(updates, list), "dependabot.yml must have an `updates:` list"
    assert len(updates) >= 2, "dependabot.yml must list at least 2 ecosystems"


@pytest.fixture(scope="module")
def update_blocks(dependabot: dict) -> dict[str, dict]:
    """Return updates indexed by `package-ecosystem`."""
    out: dict[str, dict] = {}
    for u in dependabot["updates"]:
        out[u["package-ecosystem"]] = u
    return out


def test_dependabot_lists_pip_ecosystem(update_blocks: dict) -> None:
    assert "pip" in update_blocks, "dependabot must monitor the `pip` ecosystem"
    block = update_blocks["pip"]
    assert block.get("schedule", {}).get("interval") == "weekly", (
        "pip ecosystem must be on a weekly schedule"
    )
    assert "directory" in block, "pip block must declare a directory"


def test_dependabot_lists_github_actions_ecosystem(update_blocks: dict) -> None:
    assert "github-actions" in update_blocks, (
        "dependabot must monitor the `github-actions` ecosystem"
    )
    block = update_blocks["github-actions"]
    assert block.get("schedule", {}).get("interval") == "weekly", (
        "github-actions ecosystem must be on a weekly schedule"
    )
    assert "directory" in block, "github-actions block must declare a directory"
