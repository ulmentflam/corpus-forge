"""Phase L Wave 5 — drift panel renderer + 3-way prompt helper."""

from __future__ import annotations

import io
from unittest.mock import patch


def _make_drift(
    name="qwen3_8b",
    was_model_id="BAAI/bge-m3",
    now_model_id="Qwen/Qwen3-Embedding-8B",
    dim=1024,
    chunks=12481,
    est_seconds=12481 * 0.034,
):
    from corpus_forge.embedders.fingerprint import EmbedderDrift

    return EmbedderDrift(
        name=name,
        was_model_id=was_model_id,
        was_dimension=dim,
        now_model_id=now_model_id,
        now_dimension=dim,
        chunks_to_rerun=chunks,
        est_seconds=est_seconds,
        fingerprint_was="abc123def4567890",
        fingerprint_now="def456abc7890123",
    )


def test_prompt_for_drift_non_interactive_background_returns_now():
    """``non_interactive=True`` + ``background=True`` → auto-run ``now``."""

    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    decision = prompt_for_drift(
        [_make_drift()],
        background=True,
        non_interactive=True,
    )

    assert decision == "now"


def test_prompt_for_drift_non_interactive_foreground_returns_later():
    """``non_interactive=True`` + ``background=False`` → ``later`` (don't ask, don't run)."""

    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    decision = prompt_for_drift(
        [_make_drift()],
        background=False,
        non_interactive=True,
    )

    assert decision == "later"


def test_prompt_for_drift_empty_drifts_returns_skip():
    """Empty drift list → no prompt, returns ``skip``."""

    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    decision = prompt_for_drift(
        [],
        background=False,
        non_interactive=False,
    )

    assert decision == "skip"


def test_prompt_for_drift_interactive_renders_panel_and_returns_choice():
    """Interactive path renders the panel and returns the prompt's answer."""

    from rich.console import Console

    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    buf = io.StringIO()
    from corpus_forge.ui.theme import build_theme

    test_console = Console(file=buf, width=120, force_terminal=False, theme=build_theme())

    with patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="skip") as ask:
        decision = prompt_for_drift(
            [_make_drift()],
            background=False,
            non_interactive=False,
            console=test_console,
        )

    assert decision == "skip"
    rendered = buf.getvalue()
    assert "Embedder changed" in rendered
    assert "qwen3_8b" in rendered
    assert "BAAI/bge-m3" in rendered
    assert "Qwen/Qwen3-Embedding-8B" in rendered
    assert "12,481" in rendered or "12481" in rendered
    ask.assert_called_once()


def test_prompt_for_drift_passes_choices_to_prompt_ask():
    """Interactive prompt offers the 3-way ``now / later / skip`` choice."""

    from rich.console import Console

    from corpus_forge.embedders.drift_prompt import prompt_for_drift
    from corpus_forge.ui.theme import build_theme

    test_console = Console(file=io.StringIO(), width=120, force_terminal=False, theme=build_theme())

    with patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="now") as ask:
        decision = prompt_for_drift(
            [_make_drift()],
            background=False,
            non_interactive=False,
            console=test_console,
        )

    assert decision == "now"
    _, kwargs = ask.call_args
    assert kwargs.get("choices") == ["now", "later", "skip"]
    assert kwargs.get("default") == "now"


def test_prompt_for_drift_renders_minutes_estimate():
    """The panel shows ``~N min`` derived from ``est_seconds``."""

    from rich.console import Console

    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    buf = io.StringIO()
    from corpus_forge.ui.theme import build_theme

    test_console = Console(file=buf, width=120, force_terminal=False, theme=build_theme())

    drift = _make_drift(chunks=12481, est_seconds=420.0)  # 7 minutes

    with patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="skip"):
        prompt_for_drift(
            [drift],
            background=False,
            non_interactive=False,
            console=test_console,
        )

    assert "7 min" in buf.getvalue()


def test_prompt_for_drift_multiple_drifts_renders_all():
    """Multiple drifts all appear in the rendered panel."""

    from rich.console import Console

    from corpus_forge.embedders.drift_prompt import prompt_for_drift

    buf = io.StringIO()
    from corpus_forge.ui.theme import build_theme

    test_console = Console(file=buf, width=120, force_terminal=False, theme=build_theme())

    drifts = [
        _make_drift(name="qwen3_8b", now_model_id="Qwen/Qwen3-Embedding-8B"),
        _make_drift(name="bge_m3", now_model_id="BAAI/bge-m3"),
    ]

    with patch("corpus_forge.embedders.drift_prompt.Prompt.ask", return_value="skip"):
        prompt_for_drift(
            drifts,
            background=False,
            non_interactive=False,
            console=test_console,
        )

    rendered = buf.getvalue()
    assert "qwen3_8b" in rendered
    assert "bge_m3" in rendered
