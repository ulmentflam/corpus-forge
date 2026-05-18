"""Phase L Wave 9 — table-driven tests for ``ui.agent.detect``.

Mirrors the precedence ladder documented in
``.planning/tdd/phase_l_cli_ux.md`` §12 and the canonical
``cli/cli/internal/agents/detect.go``.
"""

from __future__ import annotations

import pytest

from corpus_forge.ui.agent import AgentClient, Detection, detect, is_agent_mode


def _no_tty_env(env: dict | None = None) -> dict[str, str]:
    """Return an env dict with no agent hints set."""

    base = {} if env is None else dict(env)
    return base


# ── explicit --agent flag wins over env ─────────────────────────────


def test_explicit_off_forces_human_even_with_claude_env() -> None:
    det = detect(
        explicit="off",
        env={"CLAUDECODE": "1"},
        stdin_tty=False,
        stdout_tty=False,
    )
    assert det.client is AgentClient.HUMAN
    assert det.signal == "--agent"


def test_explicit_auto_falls_through_to_env() -> None:
    det = detect(
        explicit="auto",
        env={"CLAUDECODE": "1"},
        stdin_tty=False,
        stdout_tty=False,
    )
    assert det.client is AgentClient.CLAUDE_CODE
    assert det.signal == "CLAUDECODE"


def test_explicit_unknown_value_falls_back_to_ai_generic() -> None:
    det = detect(explicit="my-future-agent", env={}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.AI_GENERIC
    assert det.signal == "--agent"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("claude-code", AgentClient.CLAUDE_CODE),
        ("opencode", AgentClient.OPENCODE),
        ("gemini-cli", AgentClient.GEMINI_CLI),
        ("copilot-cli", AgentClient.COPILOT_CLI),
        ("codex", AgentClient.CODEX),
        ("amp", AgentClient.AMP),
        ("generic", AgentClient.GENERIC),
    ],
)
def test_explicit_known_value_maps_to_enum(value: str, expected: AgentClient) -> None:
    det = detect(explicit=value, env={}, stdin_tty=True, stdout_tty=True)
    assert det.client is expected


# ── CF_AGENT env var (same vocabulary as --agent) ────────────────────


def test_cf_agent_env_resolves() -> None:
    det = detect(env={"CF_AGENT": "claude-code"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.CLAUDE_CODE
    assert det.signal == "CF_AGENT"


def test_cf_agent_off_forces_human() -> None:
    det = detect(env={"CF_AGENT": "off", "CLAUDECODE": "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.HUMAN


# ── AI_AGENT prefix matching ─────────────────────────────────────────


def test_ai_agent_claude_code_prefix() -> None:
    det = detect(env={"AI_AGENT": "claude-code_2.1.133_agent"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.AI_GENERIC or det.client is AgentClient.CLAUDE_CODE
    # The "claude-code" prefix should match the canonical agent.
    # (Underscore-split + lowercased prefix.)
    assert det.client is AgentClient.CLAUDE_CODE
    assert det.signal == "AI_AGENT"


def test_ai_agent_opencode_prefix() -> None:
    det = detect(env={"AI_AGENT": "opencode_x"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.OPENCODE


def test_ai_agent_unknown_prefix_falls_back_to_ai_generic() -> None:
    det = detect(env={"AI_AGENT": "futurething_v1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.AI_GENERIC


def test_ai_agent_invalid_characters_skipped() -> None:
    """``AI_AGENT`` values that don't match ``^[a-zA-Z0-9_-]+$`` fall
    through to the next signal."""

    det = detect(
        env={"AI_AGENT": "claude code!", "CLAUDECODE": "1"},
        stdin_tty=True,
        stdout_tty=True,
    )
    # Invalid AI_AGENT skipped — CLAUDECODE picks it up.
    assert det.client is AgentClient.CLAUDE_CODE


# ── AMP precedence over CLAUDECODE ───────────────────────────────────


def test_agent_amp_wins_over_claudecode() -> None:
    det = detect(env={"AGENT": "amp", "CLAUDECODE": "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.AMP
    assert det.signal == "AGENT"


# ── CODEX (any of three vars) ────────────────────────────────────────


@pytest.mark.parametrize("var", ["CODEX_SANDBOX", "CODEX_CI", "CODEX_THREAD_ID"])
def test_codex_envs_detect(var: str) -> None:
    det = detect(env={var: "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.CODEX
    assert det.signal == var


# ── single-var agents ────────────────────────────────────────────────


def test_gemini_cli() -> None:
    det = detect(env={"GEMINI_CLI": "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.GEMINI_CLI


def test_copilot_cli() -> None:
    det = detect(env={"COPILOT_CLI": "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.COPILOT_CLI


def test_opencode() -> None:
    det = detect(env={"OPENCODE": "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.OPENCODE


def test_claudecode_alone() -> None:
    det = detect(env={"CLAUDECODE": "1"}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.CLAUDE_CODE


# ── MCP stdio carve-out ──────────────────────────────────────────────


def test_mcp_stdio_env_forces_agent_mode() -> None:
    det = detect(
        env={"CF_MCP_TRANSPORT": "stdio"},
        stdin_tty=True,
        stdout_tty=True,
    )
    assert is_agent_mode(det) is True
    assert det.client is AgentClient.GENERIC
    assert det.signal == "CF_MCP_TRANSPORT"


def test_mcp_stdio_argv_forces_agent_mode() -> None:
    det = detect(
        env={},
        stdin_tty=True,
        stdout_tty=True,
        argv=["corpus-forge", "mcp", "serve", "--transport", "stdio"],
    )
    assert is_agent_mode(det) is True


def test_mcp_stdio_argv_equals_form() -> None:
    det = detect(
        env={},
        stdin_tty=True,
        stdout_tty=True,
        argv=["corpus-forge", "mcp", "serve", "--transport=stdio"],
    )
    assert is_agent_mode(det) is True


# ── CI heuristic fallback ────────────────────────────────────────────


def test_ci_alone_stays_human() -> None:
    """``CI=true`` (with or without a TTY) MUST NOT auto-flip into agent
    mode.  The earlier draft turned every CI run into JSONL, which broke
    legacy ``--json`` tests and human-substring assertions in pytest under
    GitHub Actions.  Agent mode now requires an explicit signal."""

    det_no_tty = detect(env={"CI": "true"}, stdin_tty=False, stdout_tty=False)
    assert det_no_tty.client is AgentClient.HUMAN

    det_with_tty = detect(env={"CI": "true"}, stdin_tty=True, stdout_tty=True)
    assert det_with_tty.client is AgentClient.HUMAN


def test_no_signals_returns_human() -> None:
    det = detect(env={}, stdin_tty=True, stdout_tty=True)
    assert det.client is AgentClient.HUMAN
    assert is_agent_mode(det) is False


# ── Detection dataclass exposes signal + raw_value ──────────────────


def test_detection_carries_raw_value() -> None:
    det = detect(env={"AI_AGENT": "claude-code_2.1.133_agent"}, stdin_tty=True, stdout_tty=True)
    assert isinstance(det, Detection)
    assert det.raw_value == "claude-code_2.1.133_agent"
