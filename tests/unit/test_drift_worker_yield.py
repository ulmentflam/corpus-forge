"""RFC fleet-5 item 4 — the detached drift re-embed worker yields to the
managed service's drain loop when the service owns the lane.

`_drain_loop_owns_lane` is True only when the daemon is running AND
`[service] embed_drain` is on AND the embedder is in the host's lanes
(empty `[embed] lanes` → all lanes owned). `_spawn_background_embed` skips
owned lanes (logging the yield) and spawns detached workers only for the
rest. Config is passed in, never loaded — no config-less-CI trap.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from corpus_forge import cli as cli_mod
from corpus_forge.cli import _drain_loop_owns_lane, _spawn_background_embed


def _cfg(*, embed_drain: bool, lanes: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        service=SimpleNamespace(embed_drain=embed_drain),
        embed=SimpleNamespace(lanes=list(lanes or [])),
    )


class TestDrainLoopOwnsLane:
    def test_no_pin_daemon_running_owns_all(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert _drain_loop_owns_lane(_cfg(embed_drain=True), "any") is True

    def test_daemon_down_owns_nothing(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=None):
            assert _drain_loop_owns_lane(_cfg(embed_drain=True), "any") is False

    def test_embed_drain_off_owns_nothing(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert _drain_loop_owns_lane(_cfg(embed_drain=False), "any") is False

    def test_pinned_elsewhere_not_owned(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert _drain_loop_owns_lane(_cfg(embed_drain=True, lanes=["other"]), "mine") is False

    def test_lane_in_pin_is_owned(self) -> None:
        with patch("corpus_forge.admin.foreground.read_pid", return_value=123):
            assert _drain_loop_owns_lane(_cfg(embed_drain=True, lanes=["mine"]), "mine") is True


class TestSpawnBackgroundEmbedYields:
    def test_owned_lane_does_not_spawn(self, tmp_path) -> None:
        drifts = [SimpleNamespace(name="owned")]
        with (
            patch("corpus_forge.admin.foreground.read_pid", return_value=123),
            patch.object(cli_mod, "_state_dir_path", return_value=tmp_path),
            patch("subprocess.Popen") as popen,
        ):
            _spawn_background_embed(drifts, _cfg(embed_drain=True))
        popen.assert_not_called()
        assert not (tmp_path / "embed-worker.pid").exists()

    def test_daemon_down_spawns_normally(self, tmp_path) -> None:
        drifts = [SimpleNamespace(name="lane")]
        with (
            patch("corpus_forge.admin.foreground.read_pid", return_value=None),
            patch.object(cli_mod, "_state_dir_path", return_value=tmp_path),
            patch("subprocess.Popen") as popen,
        ):
            popen.return_value = SimpleNamespace(pid=999)
            _spawn_background_embed(drifts, _cfg(embed_drain=True))
        popen.assert_called_once()

    def test_mixed_spawns_only_unowned(self, tmp_path) -> None:
        drifts = [SimpleNamespace(name="owned"), SimpleNamespace(name="free")]
        with (
            patch("corpus_forge.admin.foreground.read_pid", return_value=123),
            patch.object(cli_mod, "_state_dir_path", return_value=tmp_path),
            patch("subprocess.Popen") as popen,
        ):
            popen.return_value = SimpleNamespace(pid=999)
            _spawn_background_embed(drifts, _cfg(embed_drain=True, lanes=["owned"]))
        assert popen.call_count == 1
        argv = popen.call_args.args[0]
        assert argv[-1] == "free"
