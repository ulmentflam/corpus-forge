"""RFC fleet-5 task 4 — drift-prompt embed-worker yields to the drain loop.

When the managed daemon owns a lane (daemon running + `[service]
embed_drain` on + the lane is in this host's `[embed] lanes` pin, or there
is no pin), the detached drift-prompt re-embed worker must NOT spawn for
that lane — the drain loop already drains it continuously.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from corpus_forge import cli as cli_mod


def _cfg(embed_drain: bool, lanes=()) -> SimpleNamespace:
    return SimpleNamespace(
        service=SimpleNamespace(embed_drain=embed_drain),
        embed=SimpleNamespace(lanes=list(lanes)),
    )


def _drift(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


class TestDrainLoopOwnsLane:
    def test_running_drain_on_no_pin_owns_all(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert cli_mod._drain_loop_owns_lane(_cfg(True), "qwen3") is True

    def test_daemon_not_running_not_owned(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=None):
            assert cli_mod._drain_loop_owns_lane(_cfg(True), "qwen3") is False

    def test_embed_drain_off_not_owned(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert cli_mod._drain_loop_owns_lane(_cfg(False), "qwen3") is False

    def test_lane_pinned_elsewhere_not_owned(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert cli_mod._drain_loop_owns_lane(_cfg(True, lanes=["other"]), "qwen3") is False

    def test_lane_in_pin_owned(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert cli_mod._drain_loop_owns_lane(_cfg(True, lanes=["qwen3"]), "qwen3") is True


class TestSpawnBackgroundEmbedYields:
    def test_skips_owned_lane(self) -> None:
        with (
            patch("corpus_forge.admin.foreground.read_pid", return_value=123),
            patch("subprocess.Popen") as popen,
        ):
            cli_mod._spawn_background_embed([_drift("qwen3")], _cfg(True))
        popen.assert_not_called()

    def test_spawns_unowned_lane(self, tmp_path) -> None:
        # daemon not running → drain loop doesn't own the lane → worker spawns.
        with (
            patch("corpus_forge.admin.foreground.read_pid", return_value=None),
            patch("subprocess.Popen") as popen,
            patch.object(cli_mod, "_state_dir_path", return_value=tmp_path),
        ):
            popen.return_value.pid = 999
            cli_mod._spawn_background_embed([_drift("qwen3")], _cfg(True))
        popen.assert_called_once()

    def test_mixed_spawns_only_unowned(self, tmp_path) -> None:
        # embed_drain on + pin owns only "owned"; "free" is not pinned → spawned.
        cfg = _cfg(True, lanes=["owned"])
        with (
            patch("corpus_forge.admin.foreground.read_pid", return_value=123),
            patch("subprocess.Popen") as popen,
            patch.object(cli_mod, "_state_dir_path", return_value=tmp_path),
        ):
            popen.return_value.pid = 999
            cli_mod._spawn_background_embed([_drift("owned"), _drift("free")], cfg)
        # Only the un-owned "free" lane spawned a worker.
        assert popen.call_count == 1
        argv = popen.call_args.args[0]
        assert argv[-1] == "free"
