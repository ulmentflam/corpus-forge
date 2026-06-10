"""Unit tests for embed lane pinning (RFC ``rfc-fleet-2-distributed-embedding`` item 4).

Covers the two pure lane primitives in :mod:`corpus_forge.embed`:

- :func:`filter_embedders_by_lanes` — the implicit multi-embedder path
  (embed-worker / ``--all``) intersects the active set with the host's
  ``[embed] lanes``.  Empty lanes ⇒ unchanged set (the backcompat bar).
- :func:`embedder_outside_lanes` — the explicit ``-e`` override predicate;
  warn-and-proceed when the named embedder isn't in the lanes.

Plus the ``cli_agents._run_auto_ingest`` wiring (lanes filter applied to
the "embed every active embedder" loop).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.embed import embedder_outside_lanes, filter_embedders_by_lanes


class TestFilterEmbeddersByLanes:
    """``filter_embedders_by_lanes`` — the implicit worker / --all path."""

    def test_empty_lanes_returns_all_unchanged(self) -> None:
        # The hard backcompat bar: no [embed] lanes ⇒ today's behaviour.
        names = ["nomic", "qwen3_8b", "openai_3l"]
        assert filter_embedders_by_lanes(names, []) == names

    def test_none_lanes_returns_all_unchanged(self) -> None:
        names = ["nomic", "qwen3_8b"]
        assert filter_embedders_by_lanes(names, None) == names

    def test_returns_fresh_list_not_alias(self) -> None:
        names = ["nomic"]
        result = filter_embedders_by_lanes(names, [])
        assert result == names
        assert result is not names

    def test_intersection_keeps_only_pinned(self) -> None:
        names = ["nomic", "qwen3_8b", "openai_3l"]
        assert filter_embedders_by_lanes(names, ["qwen3_8b"]) == ["qwen3_8b"]

    def test_intersection_preserves_input_order(self) -> None:
        names = ["nomic", "qwen3_8b", "openai_3l"]
        # Lanes order is irrelevant; result follows the active-set order.
        assert filter_embedders_by_lanes(names, ["openai_3l", "nomic"]) == [
            "nomic",
            "openai_3l",
        ]

    def test_lane_not_in_active_set_is_dropped(self) -> None:
        # A lane that doesn't match any active embedder simply contributes
        # nothing (config validation already proved the name is a real
        # embedder; it just isn't active on this run).
        names = ["nomic"]
        assert filter_embedders_by_lanes(names, ["qwen3_8b"]) == []


class TestEmbedderOutsideLanes:
    """``embedder_outside_lanes`` — the explicit ``-e`` override predicate."""

    def test_empty_lanes_never_outside(self) -> None:
        # No pinning ⇒ nothing is "outside" ⇒ no warn.
        assert embedder_outside_lanes("anything", []) is False

    def test_none_lanes_never_outside(self) -> None:
        assert embedder_outside_lanes("anything", None) is False

    def test_in_lane_is_not_outside(self) -> None:
        assert embedder_outside_lanes("qwen3_8b", ["qwen3_8b", "nomic"]) is False

    def test_out_of_lane_is_outside(self) -> None:
        assert embedder_outside_lanes("openai_3l", ["qwen3_8b", "nomic"]) is True


class _FakeEmbedderCfg:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEmbedConfig:
    def __init__(self, lanes: list[str]) -> None:
        self.lanes = lanes


class _FakeConfig:
    def __init__(self, names: list[str], lanes: list[str]) -> None:
        self.embedders = [_FakeEmbedderCfg(n) for n in names]
        self.embed = _FakeEmbedConfig(lanes)


class TestAutoIngestLaneFilter:
    """``cli_agents._run_auto_ingest`` honours lane pinning on the --all loop."""

    def _run(self, monkeypatch: Any, names: list[str], lanes: list[str]) -> list[str]:
        from pathlib import Path

        from corpus_forge import cli_agents as cli_agents_mod
        from corpus_forge import embed as embed_mod
        from corpus_forge import ingest as ingest_mod

        backfilled: list[str] = []

        def _fake_backfill(cfg: Any, _ds: Any, _limit: Any) -> None:
            backfilled.append(cfg.name)

        monkeypatch.setattr(embed_mod, "backfill_embedder", _fake_backfill)
        monkeypatch.setattr(ingest_mod, "ingest_once", lambda *_a, **_k: None)
        monkeypatch.setattr(cli_agents_mod, "ui_info", lambda *_a, **_k: None)

        cli_agents_mod._run_auto_ingest(_FakeConfig(names, lanes), Path("/tmp/x"))
        return backfilled

    def test_no_lanes_embeds_all(self, monkeypatch: Any) -> None:
        result = self._run(monkeypatch, ["nomic", "qwen3_8b"], [])
        assert result == ["nomic", "qwen3_8b"]

    def test_lanes_restrict_the_set(self, monkeypatch: Any) -> None:
        result = self._run(monkeypatch, ["nomic", "qwen3_8b"], ["qwen3_8b"])
        assert result == ["qwen3_8b"]


class _LaneConfig:
    """Minimal stand-in for ``Config`` exposing ``embed.lanes``."""

    def __init__(self, lanes: list[str]) -> None:
        self.embed = _FakeEmbedConfig(lanes)


class TestEmbedCliOverrideWarn:
    """``corpus-forge embed -e`` warns-and-proceeds outside the lanes."""

    def _invoke(self, lanes: list[str], embedder: str) -> Any:
        runner = CliRunner()
        warnings: list[str] = []
        with (
            patch("corpus_forge.embed.main") as mp,
            patch("corpus_forge.config.Config.load", return_value=_LaneConfig(lanes)),
            patch("corpus_forge.cli._maybe_handle_drift", lambda _ctx: None),
            patch("corpus_forge.cli.ui_warn", side_effect=warnings.append),
        ):
            result = runner.invoke(app, ["embed", "-e", embedder])
        return result, mp, warnings

    def test_in_lane_no_warn_still_runs(self) -> None:
        result, mp, warnings = self._invoke(["qwen3_8b", "nomic"], "qwen3_8b")
        assert result.exit_code == 0
        assert warnings == []
        mp.assert_called_once()

    def test_out_of_lane_warns_and_proceeds(self) -> None:
        result, mp, warnings = self._invoke(["qwen3_8b"], "openai_3l")
        assert result.exit_code == 0
        # WARN surfaced AND the backfill still ran (override wins).
        assert any("outside this host's pinned embed lanes" in m for m in warnings)
        mp.assert_called_once()

    def test_empty_lanes_no_warn(self) -> None:
        result, mp, warnings = self._invoke([], "anything")
        assert result.exit_code == 0
        assert warnings == []
        mp.assert_called_once()


class TestSetupEmbedLanesFlag:
    """``corpus-forge setup --non-interactive --embed-lanes a,b`` plumbing."""

    def test_flag_sets_cf_embed_lanes_env(self, tmp_path: Any, monkeypatch: Any) -> None:
        captured: dict[str, str] = {}

        def _fake_non_interactive(*, config_dir: Any):
            import os

            captured["CF_EMBED_LANES"] = os.environ.get("CF_EMBED_LANES", "")
            secrets = config_dir / "secrets.env"
            return config_dir / "config.toml", secrets, {}

        monkeypatch.delenv("CF_EMBED_LANES", raising=False)
        runner = CliRunner()
        with (
            patch("corpus_forge.setup.run_non_interactive", side_effect=_fake_non_interactive),
            patch("corpus_forge.cli._maybe_handle_post_setup_drift", lambda **_k: None),
        ):
            result = runner.invoke(
                app,
                [
                    "setup",
                    "--non-interactive",
                    "--config-dir",
                    str(tmp_path),
                    "--embed-lanes",
                    "qwen3_8b,nomic",
                ],
            )
        assert result.exit_code == 0, result.stdout
        assert captured["CF_EMBED_LANES"] == "qwen3_8b,nomic"

    def test_flag_interactive_warns(self, tmp_path: Any, monkeypatch: Any) -> None:
        # --embed-lanes without --non-interactive warns it's a no-op.
        def _fake_wizard(*, config_dir: Any):
            return config_dir / "config.toml", config_dir / "secrets.env", {}

        runner = CliRunner()
        warnings: list[str] = []
        with (
            patch("corpus_forge.setup.run_wizard", side_effect=_fake_wizard),
            patch("corpus_forge.cli._maybe_handle_post_setup_drift", lambda **_k: None),
            patch("corpus_forge.ui.render_banner", lambda *_a, **_k: None),
            patch("corpus_forge.cli.ui_warn", side_effect=warnings.append),
        ):
            result = runner.invoke(
                app,
                ["setup", "--config-dir", str(tmp_path), "--embed-lanes", "x"],
            )
        assert result.exit_code == 0, result.output
        assert any("only applies with --non-interactive" in m for m in warnings)


class TestGetActiveEmbeddersLaneFilter:
    """``ingest.get_active_embedders`` — the daemon embed-worker path.

    Reviewer-blocker regression: the daemon's ``ingest_once`` →
    ``_flush_all_pending_embeddings`` loop resolves its embedder set
    through ``get_active_embedders``, so lane pinning MUST apply there
    or a pinned host still embeds every lane via the daemon.
    """

    @staticmethod
    def _config(lanes: list[str]) -> Any:
        from corpus_forge.config import (
            BackendConfig,
            Config,
            DaemonConfig,
            DatasetConfig,
            DatasetSourceConfig,
            EmbedConfig,
            EmbedderConfig,
        )

        return Config(
            backend=BackendConfig(kind="sqlite", dsn=":memory:"),
            daemon=DaemonConfig(),
            datasets=[
                DatasetConfig(
                    name="d",
                    kind="text",
                    sources=[DatasetSourceConfig(plugin="markdown_vault", chunker="markdown")],
                )
            ],
            embedders=[
                EmbedderConfig(
                    name="lane-a",
                    provider="sentence_transformers",
                    model_id="m/a",
                    dimension=8,
                ),
                EmbedderConfig(
                    name="lane-b",
                    provider="sentence_transformers",
                    model_id="m/b",
                    dimension=8,
                ),
                EmbedderConfig(
                    name="inactive-c",
                    provider="sentence_transformers",
                    model_id="m/c",
                    dimension=8,
                    active=False,
                ),
            ],
            embed=EmbedConfig(lanes=lanes),
        )

    def _names(self, lanes: list[str]) -> list[str]:
        from corpus_forge.ingest import get_active_embedders

        constructed: list[str] = []

        def fake_register(_registry: Any, embedder_config: Any) -> Any:
            constructed.append(embedder_config.name)
            return embedder_config.name  # stand-in object; identity is enough

        with patch("corpus_forge.embedders.registry.register_from_config", fake_register):
            get_active_embedders(self._config(lanes))
        return constructed

    def test_empty_lanes_constructs_all_active(self) -> None:
        # Backcompat bar: no lanes ⇒ today's behaviour (active set only).
        assert self._names([]) == ["lane-a", "lane-b"]

    def test_pinned_lanes_construct_only_that_lane(self) -> None:
        assert self._names(["lane-b"]) == ["lane-b"]

    def test_inactive_lane_still_dropped(self) -> None:
        # Pinning an inactive embedder doesn't resurrect it.
        assert self._names(["lane-b", "inactive-c"]) == ["lane-b"]


class TestEmbedCliUnknownEmbedder:
    """``corpus-forge embed -e <unknown>`` fails cleanly (issue: opaque
    ValueError traceback on a typo'd embedder name)."""

    def _invoke(self, names: list[str], embedder: str):
        runner = CliRunner()
        errors: list[str] = []
        with (
            patch("corpus_forge.embed.main") as mp,
            patch("corpus_forge.config.Config.load", return_value=_FakeConfig(names, [])),
            patch("corpus_forge.cli._maybe_handle_drift", lambda _ctx: None),
            patch("corpus_forge.cli.ui_error", side_effect=errors.append),
        ):
            result = runner.invoke(app, ["embed", "-e", embedder])
        return result, mp, errors

    def test_unknown_embedder_exits_2_without_calling_main(self) -> None:
        result, mp, errors = self._invoke(["qwen3_8b", "nomic"], "qwen3-typo")
        assert result.exit_code == 2, result.output
        mp.assert_not_called()  # never reached the embed run
        # The error names the bad input AND lists the valid options.
        joined = " ".join(errors)
        assert "qwen3-typo" in joined
        assert "qwen3_8b" in joined and "nomic" in joined

    def test_known_embedder_proceeds_to_main(self) -> None:
        result, mp, errors = self._invoke(["qwen3_8b", "nomic"], "qwen3_8b")
        assert result.exit_code == 0, result.output
        mp.assert_called_once()
        assert errors == []
