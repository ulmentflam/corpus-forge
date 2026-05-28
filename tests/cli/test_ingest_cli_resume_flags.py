"""SR-T6 RED: CLI flag plumbing for ``corpus-forge ingest`` resumability flags.

Pins the EXACT spelling and mutual-exclusion rules for four new flags:

  --status          read-only; mutually exclusive with all other new flags.
  --resume          opt-in resume from latest non-completed run;
                    REQUIRES --once.
  --wait            block on lock contention instead of fast-fail;
                    REQUIRES --once (implied by real usage).
  --max-scan-age VALUE  duration spec: bare seconds or Ns/Nm/Nh/Nd.

Parser contract locked here for the Coder to implement in
``corpus_forge/cli.py`` (extend the existing ``ingest`` command) and a
thin utility ``corpus_forge.scanner.parse_scan_age_spec`` (new module or
added to an existing scanner helper).

RED contract
------------
Every test in this file MUST FAIL until SR-G6 ships the flags.  The
expected failure mode is:

  Usage Error: No such option: --status    (exit code 2)
  Usage Error: No such option: --resume    (exit code 2)
  Usage Error: No such option: --wait      (exit code 2)
  Usage Error: No such option: --max-scan-age  (exit code 2)

For parse_scan_age_spec tests: ImportError or AttributeError (module
does not exist yet).

Run: uv run pytest tests/cli/test_ingest_cli_resume_flags.py -q
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner() -> CliRunner:
    return CliRunner()


def _make_fake_main() -> tuple[MagicMock, dict[str, Any]]:
    """Return a (spy, captured_kwargs) pair.

    The spy replaces ``corpus_forge.ingest.main`` so the CLI test never
    spins up a real backend.  Captured kwargs are stored in the returned dict.
    """
    captured: dict[str, Any] = {}

    def _fake_main(**kwargs: Any) -> None:
        captured.update(kwargs)

    spy = MagicMock(side_effect=_fake_main)
    return spy, captured


def _make_fake_print_status() -> MagicMock:
    """Spy for ``corpus_forge.ingest.print_ingest_status``."""

    def _fake_status(config: Any) -> None:
        print("run_id=test-run-001  status=completed  progress=10/10 (100.0%)")

    return MagicMock(side_effect=_fake_status)


# ---------------------------------------------------------------------------
# Section 1 — Flag presence (flags must be accepted at all)
# ---------------------------------------------------------------------------


class TestFlagPresence:
    """Confirm each new flag is registered and shows up in --help."""

    def test_status_flag_in_help(self) -> None:
        result = _runner().invoke(app, ["ingest", "--help"])
        combined = (result.output or "") + (result.stderr or "")
        assert "--status" in combined, (
            f"--status not found in ingest --help.\n"
            f"exit_code={result.exit_code}\noutput={combined!r}"
        )

    def test_resume_flag_in_help(self) -> None:
        result = _runner().invoke(app, ["ingest", "--help"])
        combined = (result.output or "") + (result.stderr or "")
        assert "--resume" in combined, (
            f"--resume not found in ingest --help.\n"
            f"exit_code={result.exit_code}\noutput={combined!r}"
        )

    def test_wait_flag_in_help(self) -> None:
        result = _runner().invoke(app, ["ingest", "--help"])
        combined = (result.output or "") + (result.stderr or "")
        assert "--wait" in combined, (
            f"--wait not found in ingest --help.\nexit_code={result.exit_code}\noutput={combined!r}"
        )

    def test_max_scan_age_flag_in_help(self) -> None:
        result = _runner().invoke(app, ["ingest", "--help"])
        combined = (result.output or "") + (result.stderr or "")
        assert "--max-scan-age" in combined, (
            f"--max-scan-age not found in ingest --help.\n"
            f"exit_code={result.exit_code}\noutput={combined!r}"
        )


# ---------------------------------------------------------------------------
# Section 2 — --status happy path
# ---------------------------------------------------------------------------


class TestStatusHappyPath:
    """--status exits 0 and calls print_ingest_status (not ingest main)."""

    def test_status_exits_zero(self) -> None:
        fake_status = _make_fake_print_status()
        with patch("corpus_forge.ingest.print_ingest_status", fake_status):
            result = _runner().invoke(app, ["ingest", "--status"])
        assert result.exit_code == 0, (
            f"--status exited {result.exit_code}.\noutput={result.output!r}"
        )

    def test_status_prints_run_info(self) -> None:
        fake_status = _make_fake_print_status()
        with patch("corpus_forge.ingest.print_ingest_status", fake_status):
            result = _runner().invoke(app, ["ingest", "--status"])
        combined = (result.output or "") + (result.stderr or "")
        # Something indicating run status must appear
        assert any(
            kw in combined
            for kw in (
                "run_id",
                "status",
                "completed",
                "running",
                "interrupted",
                "failed",
                "no runs",
            )
        ), (
            f"--status output contains no recognizable run info.\n"
            f"exit_code={result.exit_code}\ncombined={combined!r}"
        )

    def test_status_does_not_call_ingest_main(self) -> None:
        fake_main = MagicMock()
        fake_status = _make_fake_print_status()
        with (
            patch("corpus_forge.ingest.main", fake_main),
            patch("corpus_forge.ingest.print_ingest_status", fake_status),
        ):
            _runner().invoke(app, ["ingest", "--status"])
        fake_main.assert_not_called()

    def test_status_calls_print_ingest_status(self) -> None:
        fake_status = _make_fake_print_status()
        with patch("corpus_forge.ingest.print_ingest_status", fake_status):
            result = _runner().invoke(app, ["ingest", "--status"])
        assert fake_status.called, (
            f"print_ingest_status was not called.\n"
            f"exit_code={result.exit_code}\noutput={result.output!r}"
        )


# ---------------------------------------------------------------------------
# Section 3 — --status mutex enforcement
# ---------------------------------------------------------------------------


class TestStatusMutex:
    """--status is mutually exclusive with --once, --resume, --wait, --max-scan-age.

    These tests require that once the flags are implemented:
    - The combination exits non-zero.
    - The error message explicitly names BOTH conflicting flags so the user
      understands which combination is invalid (not just "no such option").

    Currently RED because --status does not exist; the "no such option" error
    does not mention both flags in a mutex-aware way.
    """

    def test_status_and_once_is_error(self) -> None:
        result = _runner().invoke(app, ["ingest", "--status", "--once"])
        combined = (result.output or "") + (result.stderr or "")
        assert result.exit_code != 0, (
            f"--status --once should fail but exited 0.\noutput={combined!r}"
        )
        # The implemented error MUST mention both --status and --once in the same
        # message so the user knows exactly which combination is invalid.
        # "No such option: --status" (pre-implementation) does NOT satisfy this.
        assert "once" in combined.lower() and "status" in combined.lower(), (
            f"Error must mention both '--once' and '--status'.\ncombined={combined!r}"
        )

    def test_status_and_resume_is_error(self) -> None:
        result = _runner().invoke(app, ["ingest", "--status", "--resume"])
        combined = (result.output or "") + (result.stderr or "")
        assert result.exit_code != 0, (
            f"--status --resume should fail but exited 0.\noutput={combined!r}"
        )
        assert "resume" in combined.lower() and "status" in combined.lower(), (
            f"Error must mention both '--resume' and '--status'.\ncombined={combined!r}"
        )

    def test_status_and_wait_is_error(self) -> None:
        result = _runner().invoke(app, ["ingest", "--status", "--wait"])
        combined = (result.output or "") + (result.stderr or "")
        assert result.exit_code != 0, (
            f"--status --wait should fail but exited 0.\noutput={combined!r}"
        )
        assert "wait" in combined.lower() and "status" in combined.lower(), (
            f"Error must mention both '--wait' and '--status'.\ncombined={combined!r}"
        )

    def test_status_and_max_scan_age_is_error(self) -> None:
        result = _runner().invoke(app, ["ingest", "--status", "--max-scan-age", "60"])
        combined = (result.output or "") + (result.stderr or "")
        assert result.exit_code != 0, (
            f"--status --max-scan-age 60 should fail but exited 0.\noutput={combined!r}"
        )
        assert (
            "max" in combined.lower() or "scan" in combined.lower()
        ) and "status" in combined.lower(), (
            f"Error must mention both '--max-scan-age' and '--status'.\ncombined={combined!r}"
        )


# ---------------------------------------------------------------------------
# Section 4 — --resume without --once is an error
# ---------------------------------------------------------------------------


class TestResumRequiresOnce:
    """--resume without --once must produce a non-zero exit and a useful message."""

    def test_resume_alone_is_error(self) -> None:
        fake_main = MagicMock()
        with patch("corpus_forge.ingest.main", fake_main):
            result = _runner().invoke(app, ["ingest", "--resume"])
        assert result.exit_code != 0, (
            f"--resume without --once should fail but exited 0.\noutput={result.output!r}"
        )

    def test_resume_alone_message_mentions_once(self) -> None:
        fake_main = MagicMock()
        with patch("corpus_forge.ingest.main", fake_main):
            result = _runner().invoke(app, ["ingest", "--resume"])
        combined = (result.output or "") + (result.stderr or "")
        assert "once" in combined.lower(), (
            f"Error for --resume without --once doesn't mention --once.\ncombined={combined!r}"
        )

    def test_resume_with_once_is_accepted(self) -> None:
        """--once --resume together must NOT error at the flag-parsing layer."""
        fake_main = MagicMock()
        with patch("corpus_forge.ingest.main", fake_main):
            result = _runner().invoke(app, ["ingest", "--once", "--resume"])
        # Flag parsing should succeed; any downstream failure (e.g. no DB) is OK
        # but a "No such option" / "Invalid value" at parse time is not.
        assert result.exit_code != 2, (
            f"--once --resume should not fail at flag-parsing level (exit 2).\n"
            f"output={result.output!r}"
        )


# ---------------------------------------------------------------------------
# Section 5 — --max-scan-age parser
#
# The coder must expose `parse_scan_age_spec(s: str) -> float` in
# `corpus_forge.scanner` (new module) or `corpus_forge.scanner.parse`.
# These tests import it directly so the contract is type-precise.
# ---------------------------------------------------------------------------


class TestParseScanAgeSpec:
    """Unit-level tests for the parse_scan_age_spec(s) -> float helper.

    These tests import from ``corpus_forge.scanner`` directly so the
    Coder cannot accidentally wire the parser as a hidden closure inside
    cli.py.  Exposing it as a named public function is REQUIRED so both
    the CLI callback and SR-T7's integration layer can import it.
    """

    @staticmethod
    def _parse(spec: str) -> float:
        from corpus_forge.scanner import parse_scan_age_spec  # type: ignore[attr-defined]

        return parse_scan_age_spec(spec)

    # ── zero / always-rescan ──────────────────────────────────────────────

    def test_zero_integer_string_returns_zero(self) -> None:
        assert self._parse("0") == 0.0

    def test_zero_with_s_suffix_returns_zero(self) -> None:
        assert self._parse("0s") == 0.0

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("")

    # ── bare seconds (integer and float) ─────────────────────────────────

    def test_bare_integer_60_returns_60(self) -> None:
        assert self._parse("60") == pytest.approx(60.0)

    def test_bare_integer_90_returns_90(self) -> None:
        assert self._parse("90") == pytest.approx(90.0)

    def test_bare_float_60_point_0_returns_60(self) -> None:
        assert self._parse("60.0") == pytest.approx(60.0)

    # ── suffix variants ───────────────────────────────────────────────────

    def test_30_seconds_suffix(self) -> None:
        assert self._parse("30s") == pytest.approx(30.0)

    def test_30m_returns_1800(self) -> None:
        assert self._parse("30m") == pytest.approx(1800.0)

    def test_1_point_5m_returns_90(self) -> None:
        assert self._parse("1.5m") == pytest.approx(90.0)

    def test_2h_returns_7200(self) -> None:
        assert self._parse("2h") == pytest.approx(7200.0)

    def test_1d_returns_86400(self) -> None:
        assert self._parse("1d") == pytest.approx(86400.0)

    def test_3d_returns_259200(self) -> None:
        assert self._parse("3d") == pytest.approx(259200.0)

    # ── boundary / off-by-one ─────────────────────────────────────────────

    def test_1s_returns_1(self) -> None:
        assert self._parse("1s") == pytest.approx(1.0)

    def test_1h_returns_3600(self) -> None:
        assert self._parse("1h") == pytest.approx(3600.0)

    def test_24h_returns_86400(self) -> None:
        assert self._parse("24h") == pytest.approx(86400.0)

    # ── negative values → error ───────────────────────────────────────────

    def test_negative_integer_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("-1")

    def test_negative_with_suffix_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("-1h")

    # ── invalid / malformed specs → error ────────────────────────────────

    def test_alpha_only_string_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("abc")

    def test_unknown_suffix_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("10y")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("   ")

    def test_multiple_suffixes_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("1hm")

    def test_number_without_value_raises(self) -> None:
        with pytest.raises((ValueError, SystemExit)):
            self._parse("m")


# ---------------------------------------------------------------------------
# Section 6 — --max-scan-age wired through CLI
# ---------------------------------------------------------------------------


class TestMaxScanAgeCLIWiring:
    """The CLI must parse --max-scan-age and forward the float value to main()."""

    def test_max_scan_age_30m_passed_as_1800(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "30m"])
        # Flag parse must succeed
        assert result.exit_code != 2, (
            f"--max-scan-age 30m rejected at parse level.\noutput={result.output!r}"
        )
        assert spy.called, "ingest.main was not called"
        # The parsed float must have been forwarded
        assert "max_scan_age" in captured, (
            f"max_scan_age not forwarded to main(); captured={captured!r}"
        )
        assert captured["max_scan_age"] == pytest.approx(1800.0), (
            f"30m should be 1800.0 seconds, got {captured['max_scan_age']!r}"
        )

    def test_max_scan_age_2h_passed_as_7200(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "2h"])
        assert result.exit_code != 2, (
            f"--max-scan-age 2h rejected at parse level.\noutput={result.output!r}"
        )
        assert "max_scan_age" in captured
        assert captured["max_scan_age"] == pytest.approx(7200.0)

    def test_max_scan_age_1d_passed_as_86400(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "1d"])
        assert result.exit_code != 2, (
            f"--max-scan-age 1d rejected at parse level.\noutput={result.output!r}"
        )
        assert "max_scan_age" in captured
        assert captured["max_scan_age"] == pytest.approx(86400.0)

    def test_max_scan_age_bare_90_passed_as_90(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "90"])
        assert result.exit_code != 2, (
            f"--max-scan-age 90 rejected at parse level.\noutput={result.output!r}"
        )
        assert "max_scan_age" in captured
        assert captured["max_scan_age"] == pytest.approx(90.0)

    def test_max_scan_age_zero_passed_as_0(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "0"])
        assert result.exit_code != 2, (
            f"--max-scan-age 0 rejected at parse level.\noutput={result.output!r}"
        )
        assert "max_scan_age" in captured
        assert captured["max_scan_age"] == pytest.approx(0.0)

    def test_max_scan_age_invalid_spec_exits_nonzero(self) -> None:
        result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "abc"])
        combined = (result.output or "") + (result.stderr or "")
        assert result.exit_code != 0, (
            f"--max-scan-age abc should fail but exited 0.\noutput={combined!r}"
        )
        # Before implementation: "No such option: --max-scan-age" (doesn't mention "abc").
        # After implementation: "Invalid value for '--max-scan-age': abc" (mentions "abc").
        # This assertion pins the post-implementation error shape.
        assert "abc" in combined, (
            f"Error must mention the invalid spec value 'abc'.\n"
            f"Pre-implementation produces 'No such option' which lacks this.\n"
            f"combined={combined!r}"
        )

    def test_max_scan_age_negative_exits_nonzero(self) -> None:
        result = _runner().invoke(app, ["ingest", "--once", "--max-scan-age", "-1"])
        combined = (result.output or "") + (result.stderr or "")
        assert result.exit_code != 0, (
            f"--max-scan-age -1 should fail but exited 0.\noutput={combined!r}"
        )
        # Before implementation: "No such option: --max-scan-age" (doesn't mention "-1").
        # After implementation: error mentions "-1" or "negative" or "invalid".
        assert "-1" in combined or "negative" in combined.lower(), (
            f"Error must mention the invalid value '-1' or 'negative'.\n"
            f"Pre-implementation 'No such option' lacks this.\n"
            f"combined={combined!r}"
        )


# ---------------------------------------------------------------------------
# Section 7 — --wait wired through CLI
# ---------------------------------------------------------------------------


class TestWaitCLIWiring:
    """--wait must be forwarded to main() as wait=True."""

    def test_wait_flag_forwarded_to_main(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--wait"])
        assert result.exit_code != 2, (
            f"--once --wait rejected at parse level.\noutput={result.output!r}"
        )
        assert spy.called, "ingest.main was not called"
        assert "wait" in captured, f"wait kwarg not forwarded to main(); captured={captured!r}"
        assert captured["wait"] is True, (
            f"--wait should forward wait=True but got {captured['wait']!r}"
        )

    def test_no_wait_flag_defaults_to_false(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once"])
        assert result.exit_code != 2, f"--once rejected at parse level.\noutput={result.output!r}"
        # spy MUST be called — main() must be invoked when no new flags present
        assert spy.called, "ingest.main was not called for bare --once"
        # wait kwarg MUST be present and False (the new default)
        assert "wait" in captured, f"wait kwarg not forwarded to main(); captured={captured!r}"
        assert captured["wait"] is False, (
            f"Default wait should be False but got {captured['wait']!r}"
        )


# ---------------------------------------------------------------------------
# Section 8 — --resume wired through CLI
# ---------------------------------------------------------------------------


class TestResumeCLIWiring:
    """--resume with --once must be forwarded to main() as resume=True."""

    def test_resume_flag_forwarded_to_main(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once", "--resume"])
        assert result.exit_code != 2, (
            f"--once --resume rejected at parse level.\noutput={result.output!r}"
        )
        assert spy.called, "ingest.main was not called"
        assert "resume" in captured, f"resume kwarg not forwarded to main(); captured={captured!r}"
        assert captured["resume"] is True, (
            f"--resume should forward resume=True but got {captured['resume']!r}"
        )

    def test_no_resume_flag_defaults_to_false(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once"])
        assert result.exit_code != 2, f"--once rejected at parse level.\noutput={result.output!r}"
        # spy MUST be called — main() must be invoked for bare --once
        assert spy.called, "ingest.main was not called for bare --once"
        # resume kwarg MUST be present and False (the new default)
        assert "resume" in captured, f"resume kwarg not forwarded to main(); captured={captured!r}"
        assert captured["resume"] is False, (
            f"Default resume should be False but got {captured['resume']!r}"
        )


# ---------------------------------------------------------------------------
# Section 9 — Backwards-compatibility invariants
# ---------------------------------------------------------------------------


class TestBackwardsCompatibility:
    """``ingest --once`` and bare ``ingest`` must behave exactly as before."""

    def test_ingest_once_reaches_main_with_once_true(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest", "--once"])
        # exit code 2 = parse error → regression
        assert result.exit_code != 2, (
            f"ingest --once failed at parse level (regression).\noutput={result.output!r}"
        )
        assert spy.called, "ingest.main not called for --once"
        assert captured.get("once") is True, (
            f"once=True not forwarded to main(); captured={captured!r}"
        )

    def test_ingest_once_resume_false_by_default(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            _runner().invoke(app, ["ingest", "--once"])
        assert spy.called, "ingest.main not called"
        assert "resume" in captured, f"resume not in captured={captured!r}"
        assert captured["resume"] is False

    def test_ingest_once_wait_false_by_default(self) -> None:
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            _runner().invoke(app, ["ingest", "--once"])
        assert spy.called, "ingest.main not called"
        assert "wait" in captured, f"wait not in captured={captured!r}"
        assert captured["wait"] is False

    def test_ingest_once_max_scan_age_none_by_default(self) -> None:
        # New contract (per CodeRabbit review on PR #72): when the user
        # does not pass --max-scan-age, the CLI forwards ``None`` (not
        # ``0.0``) so ``ingest_once`` falls back to ``config.scan.max_scan_age``.
        # Passing 0.0 explicitly means "always rescan", which is now a
        # deliberate caller opt-in rather than the implicit default.
        spy, captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            _runner().invoke(app, ["ingest", "--once"])
        assert spy.called, "ingest.main not called"
        assert "max_scan_age" in captured, f"max_scan_age not in captured={captured!r}"
        assert captured["max_scan_age"] is None, (
            f"--max-scan-age default must forward None (use config), "
            f"got {captured['max_scan_age']!r}"
        )

    def test_bare_ingest_is_still_accepted(self) -> None:
        """Plain ``ingest`` (daemon mode) must still parse without error."""
        spy, _captured = _make_fake_main()
        with patch("corpus_forge.ingest.main", spy):
            result = _runner().invoke(app, ["ingest"])
        assert result.exit_code != 2, (
            f"Bare ingest rejected at parse level (regression).\noutput={result.output!r}"
        )
        assert spy.called, "ingest.main not called for bare ingest"
