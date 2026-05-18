"""Coverage targeting :mod:`corpus_forge.admin.ollama` — branches not
covered by ``test_ollama.py``.

Focus: ``_base_url`` resolution, ``_stream_ndjson`` malformed-line skip,
``_human_bytes`` PB bucket, ``cmd_get`` network error, ``set-url``
failures, ``cmd_test`` Config.load failure + embedder-found branch,
background pull dispatch.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from typing import ClassVar
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import corpus_forge.admin.ollama as ollama_mod
from corpus_forge.admin.ollama import _human_bytes, _stream_ndjson


class _FakeResp:
    def __init__(self, payload: bytes | Iterable[bytes]):
        if isinstance(payload, (bytes, bytearray)):
            self._buf = io.BytesIO(bytes(payload))
            self._iter: Iterable[bytes] = []
        else:
            self._buf = io.BytesIO()
            self._iter = list(payload)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.getvalue()

    def __iter__(self):
        return iter(self._iter)


# ── _base_url ───────────────────────────────────────────────────────────


def test_base_url_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_base_url` reads from Config.load and strips trailing slashes."""

    class _FakeOllama:
        base_url = "http://localhost:11434/"

    class _FakeConfig:
        ollama = _FakeOllama()

        @classmethod
        def load(cls):
            return cls()

    monkeypatch.setattr("corpus_forge.config.Config", _FakeConfig)
    assert ollama_mod._base_url() == "http://localhost:11434"


# ── _stream_ndjson ──────────────────────────────────────────────────────


def test_stream_ndjson_skips_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines that don't parse as JSON are silently skipped."""

    events = [
        b'{"status": "starting"}\n',
        b"not-json-garbage\n",
        b'{"status": "done"}\n',
    ]
    with patch("corpus_forge.admin.ollama.urllib.request.urlopen") as mock:
        mock.return_value = _FakeResp(events)
        out = list(_stream_ndjson("http://x/api/pull", body={"name": "x"}, timeout=1.0))
    # The garbage line is filtered out; we should only see the two valid events.
    assert len(out) == 2
    assert out[0]["status"] == "starting"
    assert out[1]["status"] == "done"


def test_stream_ndjson_skips_empty_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [b"\n", b'{"a": 1}\n', b"  \n"]
    with patch("corpus_forge.admin.ollama.urllib.request.urlopen") as mock:
        mock.return_value = _FakeResp(events)
        out = list(_stream_ndjson("http://x", body={}, timeout=1.0))
    assert out == [{"a": 1}]


# ── _human_bytes edge ───────────────────────────────────────────────────


def test_human_bytes_petabyte_threshold() -> None:
    """Beyond TB → renders as ``PB``."""

    # 1 PiB-ish.
    pib = 1024**5 * 2  # 2 PB
    assert _human_bytes(pib).endswith(" PB")


# ── cmd_get error path ──────────────────────────────────────────────────


def test_cmd_get_url_error_exits_one() -> None:
    import urllib.error

    with (
        patch("corpus_forge.admin.ollama.urllib.request.urlopen") as mock,
        patch.object(ollama_mod, "_base_url", return_value="http://x:11434"),
    ):
        mock.side_effect = urllib.error.URLError("refused")
        runner = CliRunner()
        result = runner.invoke(ollama_mod.ollama_app, ["get", "model"])
        assert result.exit_code == 1


# ── cmd_set_url failure paths ───────────────────────────────────────────


def test_cmd_set_url_config_write_error_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ollama_mod, "_set_config_value_atomic", _fail)
    runner = CliRunner()
    result = runner.invoke(ollama_mod.ollama_app, ["set-url", "http://x:11434", "--skip-probe"])
    assert result.exit_code == 1


def test_cmd_set_url_probe_failure_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the post-write probe fails, the verb still exits 0 (writeback succeeded)."""

    import urllib.error

    monkeypatch.setattr(ollama_mod, "_set_config_value_atomic", lambda *a, **k: None)
    with patch("corpus_forge.admin.ollama.urllib.request.urlopen") as mock:
        mock.side_effect = urllib.error.URLError("timeout")
        runner = CliRunner()
        result = runner.invoke(ollama_mod.ollama_app, ["set-url", "http://x:11434"])
        assert result.exit_code == 0


# ── cmd_test branches ───────────────────────────────────────────────────


def test_cmd_test_config_load_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ollama test`` exits 1 when Config.load fails."""

    from corpus_forge import config as cf_config

    def _explode(*a, **k):
        raise FileNotFoundError("no config")

    monkeypatch.setattr(
        cf_config.Config,
        "load",
        classmethod(lambda cls, **kw: _explode(**kw)),
    )
    runner = CliRunner()
    result = runner.invoke(ollama_mod.ollama_app, ["test"])
    assert result.exit_code == 1


def test_cmd_test_probe_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Ollama embedder → fallback probe; if the probe fails too, exit 1."""

    from corpus_forge import config as cf_config

    class _FakeCfg:
        embedders: ClassVar[list] = []
        ollama = None

    monkeypatch.setattr(cf_config.Config, "load", classmethod(lambda cls: _FakeCfg()))

    import urllib.error

    with (
        patch("corpus_forge.admin.ollama.urllib.request.urlopen") as mock,
        patch.object(ollama_mod, "_base_url", return_value="http://x:11434"),
    ):
        mock.side_effect = urllib.error.URLError("refused")
        runner = CliRunner()
        result = runner.invoke(ollama_mod.ollama_app, ["test"])
        assert result.exit_code == 1


def test_cmd_test_with_ollama_embedder_runs_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """When an Ollama-backed embedder is configured, the smoke helper is invoked."""

    from corpus_forge import config as cf_config

    class _FakeEmbedder:
        name = "olm"
        provider = "openai"
        base_url = "http://localhost:11434/v1"

    class _FakeCfg:
        embedders: ClassVar[list] = [_FakeEmbedder()]
        ollama = None

    monkeypatch.setattr(cf_config.Config, "load", classmethod(lambda cls: _FakeCfg()))

    class _Outcome:
        name = "olm"
        provider = "openai"
        model_id = "x"
        dim = 256
        elapsed_s = 0.01

    monkeypatch.setattr(
        "corpus_forge.admin.embedder.run_embedder_smoke",
        lambda name: _Outcome(),
    )
    runner = CliRunner()
    result = runner.invoke(ollama_mod.ollama_app, ["test"])
    assert result.exit_code == 0


def test_cmd_test_with_ollama_embedder_smoke_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the smoke encode fails, ``ollama test`` exits 1."""

    from corpus_forge import config as cf_config

    class _FakeEmbedder:
        name = "olm"
        provider = "openai"
        base_url = "http://localhost:11434/v1"

    class _FakeCfg:
        embedders: ClassVar[list] = [_FakeEmbedder()]
        ollama = None

    monkeypatch.setattr(cf_config.Config, "load", classmethod(lambda cls: _FakeCfg()))

    def _explode(name: str):
        raise RuntimeError("encode failed")

    monkeypatch.setattr("corpus_forge.admin.embedder.run_embedder_smoke", _explode)
    runner = CliRunner()
    result = runner.invoke(ollama_mod.ollama_app, ["test"])
    assert result.exit_code == 1


# ── background pull dispatch ────────────────────────────────────────────


def test_cmd_pull_background_dispatches_to_run_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ctx.obj.background=True, ``ollama pull`` shells out via run_attached."""

    calls: list[list[str]] = []

    def _fake_run_attached(argv, *, component, background):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(
        "corpus_forge.admin.foreground.run_attached",
        _fake_run_attached,
    )

    # Build a Typer Context shim with .obj.background = True.

    class _Obj:
        background = True

    runner = CliRunner()

    # We use the parent CLI so the global --background flag flows through.
    from corpus_forge.cli import app

    result = runner.invoke(app, ["--background", "ollama", "pull", "qwen3:8b"])
    # The background dispatch typer.Exit(0) is fine.
    assert result.exit_code == 0
    assert calls, "run_attached should have been invoked once"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
