"""Phase I-09 — channel detection + dispatch."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from corpus_forge.update.channels import (
    UpgradeResult,
    detect_channel,
    run_update,
)


class TestDetectChannel:
    def test_uv_tool_path(self) -> None:
        ch = detect_channel(
            executable="/home/runner/.local/share/uv/tools/corpus-forge/bin/python",
            env={},
        )
        assert ch == "uv-tool"

    def test_pipx_path(self) -> None:
        ch = detect_channel(
            executable="/home/alice/.local/pipx/venvs/corpus-forge/bin/python",
            env={},
        )
        assert ch == "pipx"

    def test_brew_path(self) -> None:
        ch = detect_channel(
            executable="/opt/homebrew/Cellar/corpus-forge/0.1/bin/python",
            env={},
        )
        assert ch == "brew"

    def test_docker_env_var(self) -> None:
        ch = detect_channel(executable="/usr/bin/python3", env={"container": "docker"})
        assert ch == "docker"

    def test_pip_fallback(self) -> None:
        # Use an isolated tmp-ish executable path with no env hints — must
        # NOT match source (no .git nearby).
        ch = detect_channel(executable="/usr/local/bin/python", env={})
        assert ch == "pip"

    def test_windows_uv_tool_path(self) -> None:
        ch = detect_channel(
            executable="C:\\Users\\Alice\\AppData\\Local\\uv\\tools\\corpus-forge\\Scripts\\python.exe",
            env={},
        )
        assert ch == "uv-tool"


class TestRunUpdateDryRun:
    @pytest.mark.parametrize(
        ("channel", "expected_head"),
        [
            ("uv-tool", "uv"),
            ("pipx", "pipx"),
            ("brew", "brew"),
            ("docker", "docker"),
            ("source", "git"),
            ("pip", None),  # sys.executable — platform-specific
        ],
    )
    def test_command_dispatch(self, channel: str, expected_head: str | None) -> None:
        result = run_update(channel=channel, dry_run=True)  # type: ignore[arg-type]
        assert isinstance(result, UpgradeResult)
        assert result.channel == channel
        assert result.succeeded
        if expected_head is not None:
            assert result.command[0] == expected_head

    def test_unknown_channel_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown channel"):
            run_update(channel="bogus")  # type: ignore[arg-type]

    def test_real_run_missing_binary_returns_127(self) -> None:
        """If the channel binary isn't on PATH, return rc=127 instead of crashing."""
        with patch("subprocess.run", side_effect=FileNotFoundError("no brew")):
            result = run_update(channel="brew", dry_run=False)
        assert result.returncode == 127
        assert "no brew" in result.stderr
