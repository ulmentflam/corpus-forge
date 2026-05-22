"""Human-friendly CLI output regressions.

Each test exercises a single CLI verb against a deliberately-broken
state and asserts that the error message names the **fix**, not just
the broken thing. See ``.planning/tdd/e2e_ux_flows.md`` for the
testable-property definitions; these tests cover the cheapest P1 set.

Property under test (from `e2e_ux_flows.md` §"What \"human-friendly\"
means as a test assertion"):

  #2 — Error messages name the broken thing AND the fix. "Could not
       load config" alone is not enough; "Could not load config at
       /path/X — run `corpus-forge setup` to create one" is.

These tests deliberately do NOT exercise positive paths (those are
covered by the per-verb unit suites). They're contract pins so a
future refactor that removes "the fix" half from an error message
fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

from corpus_forge.doctor.checks import CheckStatus, _check_config_present


def test_doctor_with_no_config_names_setup_command(tmp_path: Path) -> None:
    """`doctor`'s config check, when the config file is missing, must
    name `corpus-forge setup` as the fix. The user reading the WARN
    line should not need to guess the recovery command.
    """
    missing = tmp_path / "nope" / "config.toml"
    assert not missing.exists()

    result = _check_config_present(missing)

    assert result.status == CheckStatus.WARN, (
        f"missing config must be WARN (not FAIL); got {result.status}"
    )
    detail = result.detail
    # Property 2(a): the detail names the broken thing.
    assert str(missing) in detail, f"config-check detail must name the missing path; got {detail!r}"
    # Property 2(b): the detail names the fix.
    assert "corpus-forge setup" in detail, (
        f"config-check detail must name `corpus-forge setup` as the fix so "
        f"the user knows what to run next; got {detail!r}"
    )
    # Property 2(c): the verb is recognisable — the message uses an
    # imperative-form recovery cue ("run").
    assert "run" in detail.lower(), (
        f"config-check detail should include an imperative ('run', 'execute', etc.) "
        f"so users recognise the recovery action; got {detail!r}"
    )


def test_cli_no_config_messages_name_setup_not_migrate() -> None:
    """Every "No configuration found" error in `corpus_forge/cli.py`
    must point at `corpus-forge setup` (or another config-creating
    verb), NOT at `corpus-forge migrate`.

    Background: `corpus-forge migrate` runs schema migrations against
    an existing config; it does NOT create a config from scratch.
    Earlier copies of these error messages told users to run
    `corpus-forge migrate` for a missing config, which then itself
    failed because migrate couldn't load the (still-missing) config.
    See PR fixing "No config? run setup, not migrate."
    """
    cli_py = Path(__file__).resolve().parents[2] / "corpus_forge" / "cli.py"
    text = cli_py.read_text(encoding="utf-8")

    # Every "No configuration found" line should mention `setup` as the
    # recovery and NOT name `migrate` on the same line.
    pattern = re.compile(r'"(No configuration found[^"]*)"')
    matches = pattern.findall(text)
    assert matches, "expected at least one 'No configuration found' message in cli.py"

    for msg in matches:
        assert "setup" in msg, (
            f"'No configuration found' message must point users at "
            f"`corpus-forge setup`; got: {msg!r}"
        )
        assert "migrate" not in msg, (
            f"'No configuration found' message must NOT tell users to run "
            f"`corpus-forge migrate` (migrate needs the config to exist already); "
            f"got: {msg!r}"
        )


def test_no_embedders_configured_message_describes_fix() -> None:
    """When `corpus-forge eval` / `corpus-forge search` / similar tries to
    build a retriever but no embedders are configured, the error must
    describe the fix — either name a verb (e.g. `corpus-forge embedder add`)
    or tell the user exactly what to put in config.toml.

    A bare "no embedders configured" without a fix violates property #2.
    """
    cli_py = Path(__file__).resolve().parents[2] / "corpus_forge" / "cli.py"
    text = cli_py.read_text(encoding="utf-8")

    pattern = re.compile(r'"(no embedders configured[^"]*)"', re.IGNORECASE)
    matches = pattern.findall(text)
    assert matches, "expected at least one 'no embedders configured' message in cli.py"

    for msg in matches:
        # Either a recovery verb OR a config-edit instruction is acceptable.
        has_verb = any(
            v in msg.lower() for v in ("embedder add", "run `corpus-forge", "run 'corpus-forge")
        )
        has_config_hint = (
            "[[embedders]]" in msg or "config.toml" in msg or "add at least one" in msg.lower()
        )
        assert has_verb or has_config_hint, (
            f"'no embedders configured' message must describe the fix — either name "
            f"a recovery verb like `corpus-forge embedder add` or instruct the user "
            f"to edit config.toml; got: {msg!r}"
        )
