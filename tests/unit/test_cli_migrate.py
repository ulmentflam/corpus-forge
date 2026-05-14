"""D-08 RED — CLI subcommands `migrate revision` + `migrate history`.

Four tests that will all FAIL at RED because `corpus-forge migrate` is
currently a plain Typer command with no subcommand group.  The D-08 coder
must convert it to a Typer app group and wire:

- ``corpus-forge migrate revision -m "<message>"``
  → ``alembic.command.revision(config, message=..., autogenerate=False)``
- ``corpus-forge migrate history``
  → ``alembic.command.history(config, verbose=False, indicate_current=True)``

The existing plain ``corpus-forge migrate`` (upgrade to head) keeps working.
"""

from __future__ import annotations

from typer.testing import CliRunner

from corpus_forge.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Test 1 — migrate revision -m "<msg>" dispatches to alembic.command.revision
# ---------------------------------------------------------------------------


def test_migrate_revision_emits_a_file_into_versions_dir(monkeypatch) -> None:
    """``corpus-forge migrate revision -m "test slice"`` must:

    - Exit with code 0.
    - Call ``alembic.command.revision`` exactly once with ``message="test slice"``.

    FAILS at RED: ``migrate`` is a plain command with no ``revision`` sub-command.
    Typer returns exit code 2 with "Got unexpected extra argument (revision)".
    """
    import alembic.command as alembic_command_mod

    calls: list[dict] = []

    def _fake_revision(config, message=None, autogenerate=False, **kwargs):
        calls.append({"message": message, "autogenerate": autogenerate})

    monkeypatch.setattr(alembic_command_mod, "revision", _fake_revision)

    result = runner.invoke(app, ["migrate", "revision", "-m", "test slice"])

    assert result.exit_code == 0, (
        f"``migrate revision -m 'test slice'`` must exit 0; got {result.exit_code}.\n"
        f"Output:\n{result.output}"
    )
    assert len(calls) == 1, (
        f"``alembic.command.revision`` must be called exactly once; got {len(calls)} calls."
    )
    assert calls[0]["message"] == "test slice", (
        f"revision must be called with message='test slice'; got {calls[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — migrate revision without -m fails with a clear error
# ---------------------------------------------------------------------------


def test_migrate_revision_requires_message_arg() -> None:
    """``corpus-forge migrate revision`` (without ``-m``) must fail non-zero.

    Typer/Click should surface a clear error about the missing required
    ``--message`` / ``-m`` argument.

    FAILS at RED: there is no ``revision`` subcommand at all — exit code 2
    with "Got unexpected extra argument (revision)" instead of a missing-
    message error.
    """
    result = runner.invoke(app, ["migrate", "revision"])

    assert result.exit_code != 0, (
        "``migrate revision`` without ``-m`` must exit non-zero; "
        f"got exit_code={result.exit_code}.\nOutput:\n{result.output}"
    )
    # The error must say something useful — either "missing" or "required"
    # or the flag name itself, so users know what to supply.
    combined = (result.output + (result.stderr if hasattr(result, "stderr") else "")).lower()
    has_helpful_hint = any(
        token in combined for token in ("missing", "required", "-m", "--message", "message")
    )
    assert has_helpful_hint, (
        f"Error output must mention the missing -m / --message argument; got:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Test 3 — migrate history dispatches to alembic.command.history
# ---------------------------------------------------------------------------


def test_migrate_history_prints_revision_list(monkeypatch) -> None:
    """``corpus-forge migrate history`` must:

    - Exit with code 0.
    - Call ``alembic.command.history`` exactly once with
      ``indicate_current=True``.

    FAILS at RED: ``migrate`` has no ``history`` subcommand. Typer returns
    exit code 2 with "Got unexpected extra argument (history)".
    """
    import alembic.command as alembic_command_mod

    calls: list[dict] = []

    def _fake_history(config, rev_range=None, verbose=False, indicate_current=False):
        calls.append({"indicate_current": indicate_current, "verbose": verbose})

    monkeypatch.setattr(alembic_command_mod, "history", _fake_history)

    result = runner.invoke(app, ["migrate", "history"])

    assert result.exit_code == 0, (
        f"``migrate history`` must exit 0; got {result.exit_code}.\nOutput:\n{result.output}"
    )
    assert len(calls) == 1, (
        f"``alembic.command.history`` must be called exactly once; got {len(calls)} calls."
    )
    assert calls[0]["indicate_current"] is True, (
        f"history must be called with indicate_current=True; got {calls[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — migrate --help lists both subcommands
# ---------------------------------------------------------------------------


def test_migrate_subcommand_help_lists_revision_and_history() -> None:
    """``corpus-forge migrate --help`` must list both ``revision`` and ``history``.

    FAILS at RED: ``migrate`` is a plain single command; its --help lists only
    ``--help`` and nothing about subcommands.
    """
    result = runner.invoke(app, ["migrate", "--help"])

    assert result.exit_code == 0, (
        f"``migrate --help`` must exit 0; got {result.exit_code}.\nOutput:\n{result.output}"
    )

    output_lower = result.output.lower()
    assert "revision" in output_lower, (
        f"``migrate --help`` must mention the 'revision' subcommand; got:\n{result.output}"
    )
    assert "history" in output_lower, (
        f"``migrate --help`` must mention the 'history' subcommand; got:\n{result.output}"
    )
