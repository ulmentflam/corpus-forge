"""Phase L Wave 3 — ``corpus-forge doctor --json`` tests.

``--json`` suppresses the banner and the human-rendered table, emits a
single parseable JSON document on stdout, and sets the process exit code
from the report summary:

- summary == "ok"   → exit 0
- summary == "warn" → exit 2
- summary == "fail" → exit 1

The JSON shape:

```json
{
  "checks": [{"name": ..., "status": "OK|WARN|FAIL|SKIP", "detail": ...}],
  "summary": "ok|warn|fail",
  "version": "<corpus-forge version>",
  "ts": "<iso 8601 timestamp>"
}
```
"""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from corpus_forge import __version__
from corpus_forge.cli import app
from corpus_forge.doctor.checks import CheckResult, CheckStatus, DoctorReport


def _runner() -> CliRunner:
    return CliRunner()


def _combined(result) -> str:
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    try:
        if result.stderr:
            parts.append(result.stderr)
    except (AttributeError, ValueError):
        pass
    return "".join(parts) or result.output


def _ok_report() -> DoctorReport:
    return DoctorReport(
        results=[
            CheckResult("python", CheckStatus.OK, "3.12.1"),
            CheckResult("uv", CheckStatus.OK, "uv 0.5.7"),
        ]
    )


def _warn_report() -> DoctorReport:
    return DoctorReport(
        results=[
            CheckResult("python", CheckStatus.OK, "3.12.1"),
            CheckResult("ffmpeg", CheckStatus.WARN, "not on PATH"),
        ]
    )


def _fail_report() -> DoctorReport:
    return DoctorReport(
        results=[
            CheckResult("python", CheckStatus.OK, "3.12.1"),
            CheckResult("config", CheckStatus.FAIL, "missing"),
            CheckResult("ffmpeg", CheckStatus.WARN, "not on PATH"),
        ]
    )


# ── 1. emits a single parseable JSON document ────────────────────────


def test_doctor_json_prints_single_parseable_document() -> None:
    """``doctor --json`` prints exactly one JSON object on stdout."""

    with patch("corpus_forge.cli.run_doctor", create=True, return_value=_ok_report()) as _r:
        # Some implementations import run_doctor inside the function;
        # the second patch covers that path too.
        with patch("corpus_forge.doctor.run_doctor", return_value=_ok_report()):
            result = _runner().invoke(app, ["doctor", "--json"])
        del _r

    assert result.exit_code == 0, result.output
    # Single parseable JSON doc — accept compact or pretty formatting.
    parsed = json.loads(result.stdout.strip())
    assert isinstance(parsed, dict)


# ── 2. JSON shape matches DoctorReport.to_json() ──────────────────────


def test_doctor_json_shape_has_required_keys() -> None:
    """The JSON document carries ``checks``, ``summary``, ``version``,
    ``ts``."""

    with patch("corpus_forge.doctor.run_doctor", return_value=_ok_report()):
        result = _runner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output

    parsed = json.loads(result.stdout.strip())
    assert set(parsed) >= {"checks", "summary", "version", "ts"}
    assert parsed["version"] == __version__
    # ``ts`` looks like an ISO-8601 timestamp.
    assert "T" in parsed["ts"]
    # ``checks`` is a list of dicts with the three locked fields.
    assert isinstance(parsed["checks"], list)
    for entry in parsed["checks"]:
        assert set(entry) >= {"name", "status", "detail"}
        assert entry["status"] in {"OK", "WARN", "FAIL", "SKIP"}


# ── 3. summary + exit-code mapping ─────────────────────────────────────


def test_doctor_json_all_ok_exits_zero() -> None:
    with patch("corpus_forge.doctor.run_doctor", return_value=_ok_report()):
        result = _runner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout.strip())
    assert parsed["summary"] == "ok"


def test_doctor_json_warn_only_exits_two() -> None:
    with patch("corpus_forge.doctor.run_doctor", return_value=_warn_report()):
        result = _runner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 2, result.output
    parsed = json.loads(result.stdout.strip())
    assert parsed["summary"] == "warn"


def test_doctor_json_fail_exits_one() -> None:
    with patch("corpus_forge.doctor.run_doctor", return_value=_fail_report()):
        result = _runner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1, result.output
    parsed = json.loads(result.stdout.strip())
    assert parsed["summary"] == "fail"


# ── 4. banner is suppressed in --json mode ───────────────────────────


def test_doctor_json_does_not_render_banner() -> None:
    """``doctor --json`` prints no banner. The subtitle ``"Chat with
    your data."`` must not appear in either stdout or stderr."""

    with patch("corpus_forge.doctor.run_doctor", return_value=_ok_report()):
        result = _runner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output

    combined = _combined(result)
    assert "Chat with your data." not in combined, f"banner leaked into --json output:\n{combined}"


# ── 5. DoctorReport.to_json() shape (direct unit test) ──────────────


def test_doctor_report_to_json_shape() -> None:
    """``DoctorReport.to_json()`` returns a dict matching the CLI output
    shape — exercise the method directly so the shape is locked
    independently of the Typer plumbing."""

    report = _warn_report()
    data = report.to_json()
    assert set(data) >= {"checks", "summary", "version", "ts"}
    assert data["summary"] == "warn"
    assert data["version"] == __version__
    assert isinstance(data["checks"], list)
    assert len(data["checks"]) == 2
    names = {c["name"] for c in data["checks"]}
    assert names == {"python", "ffmpeg"}


def test_doctor_report_to_json_summary_ok_with_skip_only() -> None:
    """A report containing only OK + SKIP statuses summarizes as ``ok``."""

    report = DoctorReport(
        results=[
            CheckResult("python", CheckStatus.OK, ""),
            CheckResult("poppler", CheckStatus.SKIP, "not configured"),
        ]
    )
    assert report.to_json()["summary"] == "ok"


def test_doctor_report_to_json_status_values_are_uppercase_strings() -> None:
    """Status values are the raw enum strings (``OK``/``WARN``/``FAIL``/``SKIP``)."""

    report = _fail_report()
    statuses = {c["status"] for c in report.to_json()["checks"]}
    assert statuses <= {"OK", "WARN", "FAIL", "SKIP"}
