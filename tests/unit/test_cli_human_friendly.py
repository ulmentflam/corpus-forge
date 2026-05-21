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
    assert str(missing) in detail, (
        f"config-check detail must name the missing path; got {detail!r}"
    )
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
