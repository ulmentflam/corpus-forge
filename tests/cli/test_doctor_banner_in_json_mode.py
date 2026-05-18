"""Phase L Wave 3 — pin the banner-suppression rule for ``doctor --json``.

A separate file from ``test_banner.py`` so the W3-02 worker can own the
banner-in-json-mode assertion without contending with W3-01's banner
test file.
"""

from __future__ import annotations

from typer.testing import CliRunner

from corpus_forge.cli import app

_BANNER_SUBTITLE = "Chat with your data."


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


def test_doctor_human_render_has_banner_but_json_does_not() -> None:
    """``doctor`` (human) renders the banner; ``doctor --json`` suppresses it."""

    runner = CliRunner()

    human = runner.invoke(app, ["doctor"])
    json_out = runner.invoke(app, ["doctor", "--json"])

    human_text = _combined(human)
    json_text = _combined(json_out)

    assert _BANNER_SUBTITLE in human_text, f"human-render doctor missing banner:\n{human_text}"
    assert _BANNER_SUBTITLE not in json_text, f"--json mode leaked banner:\n{json_text}"
