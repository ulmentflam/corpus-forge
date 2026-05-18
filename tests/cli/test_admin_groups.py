"""Smoke tests for the Wave 7 admin command groups (Phase L Wave 7).

We assert that ``corpus-forge config|embedder|ollama|dataset|source
--help`` print successfully (exit 0 + listed verbs visible) so a future
refactor that accidentally drops a sub-app from ``cli.py`` fails CI
loudly.

We avoid invoking the verbs themselves here — those are covered in
``tests/admin/`` against scoped fixtures.  This file is the integration
gate that the apps land on the root.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

runner = CliRunner()


_GROUPS_AND_VERBS = [
    ("config", ["get", "set", "unset", "show", "path", "validate", "edit"]),
    ("embedder", ["list", "get", "add", "remove", "set-active", "test"]),
    ("ollama", ["list", "get", "pull", "set-url", "test"]),
    ("dataset", ["list", "get", "add", "remove"]),
    ("source", ["list", "add", "remove"]),
]


@pytest.mark.parametrize(("group", "_"), _GROUPS_AND_VERBS)
def test_group_help_succeeds(group: str, _) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0, result.stdout
    assert group in result.stdout or "Commands" in result.stdout


@pytest.mark.parametrize(("group", "verbs"), _GROUPS_AND_VERBS)
def test_group_help_lists_verbs(group: str, verbs: list[str]) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0
    for verb in verbs:
        assert verb in result.stdout, f"verb {verb!r} missing from {group} --help"


@pytest.mark.parametrize(
    ("group", "verb"),
    [
        ("config", "path"),
        ("config", "get"),
        ("embedder", "list"),
        ("ollama", "list"),
        ("dataset", "list"),
        ("source", "list"),
    ],
)
def test_verb_help_succeeds(group: str, verb: str) -> None:
    """Verb-level ``--help`` smoke for one representative verb per group."""

    result = runner.invoke(app, [group, verb, "--help"])
    assert result.exit_code == 0, result.stdout


def test_root_help_lists_admin_groups() -> None:
    """All five admin groups should appear under the root ``--help``."""

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group, _ in _GROUPS_AND_VERBS:
        assert group in result.stdout
