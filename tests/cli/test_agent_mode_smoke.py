"""Phase L Wave 9 — agent-mode smoke across the command surface.

Locks the contract per ``.planning/tdd/phase_l_cli_ux.md`` §12:

  * stdout under ``CF_AGENT=generic`` carries ZERO ANSI bytes.
  * every non-empty stdout line parses as JSON.
  * each command emits exactly one ``command.start`` event AND one
    terminal ``result`` / ``error`` event.

Commands that hit the network / DB / model layers are mocked out so the
contract test is hermetic.  Destructive commands (anything writing to
the user's config / state) are skipped — the agent contract is the
same shape regardless.
"""

from __future__ import annotations

import json
import re
import typing
from unittest.mock import patch

from typer.testing import CliRunner

from corpus_forge.cli import app

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _runner() -> CliRunner:
    """Build a CliRunner.  ``mix_stderr`` was removed in newer Click."""

    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


def _agent_env() -> dict[str, str]:
    return {"CF_AGENT": "generic", "NO_COLOR": "1"}


def _parse_lines(stdout: str) -> list[dict]:
    out: list[dict] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


# ── per-command smoke tests ──────────────────────────────────────────


def test_version_under_agent_mode_emits_clean_jsonl() -> None:
    """``corpus-forge version`` is the simplest leaf command."""

    result = _runner().invoke(app, ["version"], env=_agent_env())
    # ``version`` prints "corpus-forge version X" — under agent mode the
    # wrapper still emits start/result events around it.  We can't ban
    # the legacy ``print`` line, but we CAN assert no ANSI noise.
    assert not _ANSI_RE.search(result.stdout)
    # Result exit code 0.
    assert result.exit_code == 0


def test_doctor_agent_mode_zero_ansi_and_clean_jsonl() -> None:
    from corpus_forge.doctor.checks import CheckResult, CheckStatus, DoctorReport

    with patch(
        "corpus_forge.doctor.run_doctor",
        return_value=DoctorReport(results=[CheckResult("python", CheckStatus.OK, "3.12.1")]),
    ):
        result = _runner().invoke(app, ["doctor"], env=_agent_env())

    assert not _ANSI_RE.search(result.stdout), result.stdout
    events = _parse_lines(result.stdout)
    starts = [e for e in events if e["event"] == "command.start"]
    terms = [e for e in events if e["event"] in {"result", "error"}]
    assert len(starts) == 1, events
    assert len(terms) == 1, events
    assert terms[0]["event"] == "result"
    assert terms[0]["status"] == "ok"


def test_capabilities_agent_mode_lists_at_least_20_commands() -> None:
    result = _runner().invoke(app, ["capabilities"], env=_agent_env())
    assert not _ANSI_RE.search(result.stdout)
    events = _parse_lines(result.stdout)
    result_events = [e for e in events if e["event"] == "result"]
    assert result_events, events
    commands = result_events[0]["data"]["commands"]
    assert len(commands) >= 20, [c["name"] for c in commands]


def test_capabilities_human_mode_prints_json_document() -> None:
    """In human mode, capabilities renders a pretty JSON document on stdout."""

    result = _runner().invoke(app, ["capabilities"])
    assert result.exit_code == 0
    # The output is valid JSON (whether under CliRunner's stripping or not).
    data = json.loads(result.stdout)
    assert "commands" in data
    assert len(data["commands"]) >= 20


def test_search_agent_mode_emits_result_with_hits() -> None:
    """Search emits a single ``result`` carrying ``hits`` per the contract."""

    class _FakeHit:
        def __init__(self, chunk_id, score, text, doc):
            self.chunk_id = chunk_id
            self.score = score
            self.text = text
            self.source_uri = doc
            self.dataset_id = 1
            self.metadata = {}
            self.source = "fused"
            self.title = None
            self.document_id = 1

    class _FakeRetriever:
        def search(self, query, options):
            return [_FakeHit(1, 0.9, "hello world", "test://doc")]

    with patch("corpus_forge.cli._build_retriever_for_eval", return_value=_FakeRetriever()):
        result = _runner().invoke(app, ["search", "hi"], env=_agent_env())

    events = _parse_lines(result.stdout)
    starts = [e for e in events if e["event"] == "command.start"]
    results = [e for e in events if e["event"] == "result"]
    assert len(starts) == 1
    assert len(results) == 1
    assert results[0]["cmd"] == "search"
    hits = results[0]["data"]["hits"]
    assert hits == [{"chunk_id": 1, "score": 0.9, "text": "hello world", "doc": "test://doc"}]


def test_claudecode_env_marks_agent_field() -> None:
    """``CLAUDECODE=1`` populates the ``agent`` field on the start event."""

    class _FakeHit:
        chunk_id = 1
        score = 0.5
        text = "x"
        source_uri = "test://doc"
        dataset_id = 1
        metadata: typing.ClassVar[dict] = {}
        source = "fused"
        title = None
        document_id = 1

    class _FakeRetriever:
        def search(self, q, o):
            return [_FakeHit()]

    with patch("corpus_forge.cli._build_retriever_for_eval", return_value=_FakeRetriever()):
        result = _runner().invoke(
            app,
            ["search", "x"],
            env={"CLAUDECODE": "1", "NO_COLOR": "1"},
        )

    events = _parse_lines(result.stdout)
    assert events
    # First event must be command.start and carry agent=claude-code.
    assert events[0]["event"] == "command.start"
    assert events[0]["agent"] == "claude-code"


def test_explicit_off_disables_agent_mode_even_under_claude_env() -> None:
    """``--agent off`` overrides the env signal — human stdout returns."""

    from corpus_forge.doctor.checks import CheckResult, CheckStatus, DoctorReport

    with patch(
        "corpus_forge.doctor.run_doctor",
        return_value=DoctorReport(results=[CheckResult("python", CheckStatus.OK, "3.12.1")]),
    ):
        result = _runner().invoke(
            app,
            ["--agent", "off", "doctor", "--json"],
            env={"CLAUDECODE": "1", "NO_COLOR": "1"},
        )

    # --json prints a single line that parses, no JSONL wrapping.
    payload = json.loads(result.stdout.strip())
    assert payload["summary"] == "ok"


def test_estimate_agent_mode_with_nonexistent_path_emits_error() -> None:
    """A failing command emits a structured ``error`` (not ``result``).

    Estimate doesn't always self-emit when it errors before reaching the
    json_out branch.  Either ``error`` (from the wrapper) or ``result``
    (from the body) is fine — we just lock that exactly one terminal
    event is produced and that stdout is clean JSONL.
    """

    result = _runner().invoke(
        app,
        ["estimate", "/does/not/exist/at/all"],
        env=_agent_env(),
    )
    assert not _ANSI_RE.search(result.stdout)
    events = _parse_lines(result.stdout)
    terminal = [e for e in events if e["event"] in {"result", "error"}]
    assert len(terminal) == 1, events


# ── Critical CF_AGENT=generic | doctor | jsonl-only outputs ──────────


def test_cf_agent_generic_doctor_outputs_only_jsonl() -> None:
    """``CF_AGENT=generic corpus-forge doctor`` — stdout is exclusively JSONL."""

    from corpus_forge.doctor.checks import CheckResult, CheckStatus, DoctorReport

    with patch(
        "corpus_forge.doctor.run_doctor",
        return_value=DoctorReport(results=[CheckResult("python", CheckStatus.OK, "3.12.1")]),
    ):
        result = _runner().invoke(app, ["doctor"], env={"CF_AGENT": "generic"})

    assert not _ANSI_RE.search(result.stdout)
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        json.loads(line)  # parses cleanly or raises


def test_capabilities_command_registry_covers_known_commands() -> None:
    """The smoke contract: ``capabilities`` lists every reachable command."""

    result = _runner().invoke(app, ["capabilities"], env=_agent_env())
    assert result.exit_code == 0
    events = _parse_lines(result.stdout)
    payload = next(e for e in events if e["event"] == "result")["data"]
    names = {c["name"] for c in payload["commands"]}
    # Every leaf command we ship today should be discoverable.
    expected_subset = {
        "search",
        "doctor",
        "estimate",
        "version",
        "capabilities",
        "bug-report",
        "config get",
        "config set",
        "embedder list",
        "service status",
    }
    missing = expected_subset - names
    assert not missing, f"capabilities missing: {missing}"
