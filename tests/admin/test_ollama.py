"""Tests for :mod:`corpus_forge.admin.ollama` (Phase L Wave 7).

We mock ``urllib.request.urlopen`` to make every test hermetic — no
local Ollama daemon required.  The verbs themselves run inside a
Typer ``CliRunner`` so the Pass/exit-code contract is exercised too.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterable
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import corpus_forge.admin.ollama as ollama_mod
from corpus_forge.admin.ollama import (
    _human_bytes,
    _update_pull_progress,
    fetch_tags,
    ollama_app,
    show_model,
)

runner = CliRunner()


class _FakeResp:
    """Stand-in for ``urllib.request.urlopen``'s context-manager return."""

    def __init__(self, payload: bytes | Iterable[bytes]):
        if isinstance(payload, (bytes, bytearray)):
            self._buf = io.BytesIO(bytes(payload))
            self._iter: Iterable[bytes] = []
            self._streaming = False
        else:
            self._buf = io.BytesIO()
            self._iter = list(payload)
            self._streaming = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.getvalue()

    def __iter__(self):
        return iter(self._iter)


_TAGS_PAYLOAD = {
    "models": [
        {
            "name": "qwen2.5vl:7b",
            "size": 5_400_000_000,
            "modified_at": "2026-04-01T12:00:00Z",
            "details": {"family": "qwen2vl"},
        },
        {
            "name": "bge-m3:latest",
            "size": 1_100_000_000,
            "modified_at": "2026-03-15T09:30:00Z",
            "details": {"family": "bert"},
        },
    ]
}


@pytest.fixture
def patch_urlopen():
    """Yield a context-aware mock for ``urllib.request.urlopen``."""

    with patch("corpus_forge.admin.ollama.urllib.request.urlopen") as mock:
        yield mock


@pytest.fixture(autouse=True)
def patch_base_url():
    """Replace ``_base_url`` so we never touch the user's config file."""

    with patch.object(ollama_mod, "_base_url", return_value="http://localhost:11434") as p:
        yield p


# ── fetch_tags ──────────────────────────────────────────────────────────


def test_fetch_tags_parses_models(patch_urlopen) -> None:
    patch_urlopen.return_value = _FakeResp(json.dumps(_TAGS_PAYLOAD).encode("utf-8"))
    models = fetch_tags()
    assert len(models) == 2
    assert models[0].name == "qwen2.5vl:7b"
    assert models[0].family == "qwen2vl"
    assert models[0].size == 5_400_000_000
    assert models[1].family == "bert"


def test_fetch_tags_empty_response(patch_urlopen) -> None:
    patch_urlopen.return_value = _FakeResp(b'{"models": []}')
    assert fetch_tags() == []


def test_fetch_tags_robust_to_missing_fields(patch_urlopen) -> None:
    patch_urlopen.return_value = _FakeResp(json.dumps({"models": [{"name": "x"}]}).encode("utf-8"))
    models = fetch_tags()
    assert models[0].name == "x"
    assert models[0].family == "?"
    assert models[0].size == 0


# ── show_model ──────────────────────────────────────────────────────────


def test_show_model_returns_payload(patch_urlopen) -> None:
    payload = {"license": "Apache-2.0", "details": {"parameter_size": "7B"}}
    patch_urlopen.return_value = _FakeResp(json.dumps(payload).encode("utf-8"))
    out = show_model("qwen2.5vl:7b")
    assert out["license"] == "Apache-2.0"
    assert out["details"]["parameter_size"] == "7B"


# ── _human_bytes ────────────────────────────────────────────────────────


def test_human_bytes_under_kb() -> None:
    assert _human_bytes(512) == "512 B"


def test_human_bytes_kilobytes() -> None:
    assert _human_bytes(2048).endswith(" KB")


def test_human_bytes_gigabytes() -> None:
    out = _human_bytes(5_400_000_000)
    assert out.endswith(" GB")
    assert "5." in out  # ~5.0 GB


# ── _update_pull_progress ───────────────────────────────────────────────


class _FakeProgress:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, _task, **kwargs):
        self.updates.append(kwargs)


def test_pull_progress_event_with_total_and_completed() -> None:
    progress = _FakeProgress()
    _update_pull_progress(progress, task=1, event={"total": 100, "completed": 25})
    # Total set, completed set.
    assert any("total" in u and u["total"] == 100 for u in progress.updates)
    assert any("completed" in u and u["completed"] == 25 for u in progress.updates)


def test_pull_progress_event_status_only() -> None:
    progress = _FakeProgress()
    _update_pull_progress(progress, task=1, event={"status": "downloading"})
    assert any("description" in u for u in progress.updates)


def test_pull_progress_event_empty_ignored() -> None:
    progress = _FakeProgress()
    _update_pull_progress(progress, task=1, event={})
    assert progress.updates == []


# ── CLI verbs ───────────────────────────────────────────────────────────


def test_cli_list_renders_rows(patch_urlopen) -> None:
    patch_urlopen.return_value = _FakeResp(json.dumps(_TAGS_PAYLOAD).encode("utf-8"))
    result = runner.invoke(ollama_app, ["list"])
    assert result.exit_code == 0, result.stderr or result.stdout
    # Rich table renders into stderr (via the singleton console).
    combined = (result.stdout or "") + (result.stderr or "")
    assert "qwen2.5vl:7b" in combined
    assert "bge-m3" in combined


def test_cli_list_handles_empty(patch_urlopen) -> None:
    patch_urlopen.return_value = _FakeResp(b'{"models": []}')
    result = runner.invoke(ollama_app, ["list"])
    assert result.exit_code == 0


def test_cli_list_network_error_exits_nonzero(patch_urlopen) -> None:
    import urllib.error

    patch_urlopen.side_effect = urllib.error.URLError("Connection refused")
    result = runner.invoke(ollama_app, ["list"])
    assert result.exit_code == 1


def test_cli_get_prints_json(patch_urlopen) -> None:
    payload = {"license": "MIT", "format": "gguf"}
    patch_urlopen.return_value = _FakeResp(json.dumps(payload).encode("utf-8"))
    result = runner.invoke(ollama_app, ["get", "qwen3:8b"])
    assert result.exit_code == 0
    assert '"license": "MIT"' in result.stdout


def test_cli_pull_streams_progress(patch_urlopen) -> None:
    """``ollama pull`` consumes the NDJSON stream + finishes ok."""

    events = [
        b'{"status": "pulling manifest"}\n',
        b'{"status": "downloading", "total": 1000, "completed": 250}\n',
        b'{"status": "downloading", "total": 1000, "completed": 1000}\n',
        b'{"status": "success"}\n',
    ]
    patch_urlopen.return_value = _FakeResp(events)
    result = runner.invoke(ollama_app, ["pull", "qwen3:8b"])
    assert result.exit_code == 0, (result.stderr or "") + (result.stdout or "")


def test_cli_pull_network_error_exits_nonzero(patch_urlopen) -> None:
    import urllib.error

    patch_urlopen.side_effect = urllib.error.URLError("Timeout")
    result = runner.invoke(ollama_app, ["pull", "x"])
    assert result.exit_code == 1


def test_cli_set_url_persists_and_probes(monkeypatch, patch_urlopen, tmp_path) -> None:
    """``ollama set-url`` round-trips through the config writer."""

    from corpus_forge.admin import config as admin_config

    calls: list[tuple[str, str]] = []

    def _fake_set(key: str, value: str) -> None:
        calls.append((key, value))

    monkeypatch.setattr(admin_config, "_set_config_value_atomic", _fake_set)
    # Also patch on the ollama module's reference (since it imports the
    # function symbol directly).
    monkeypatch.setattr(ollama_mod, "_set_config_value_atomic", _fake_set)

    patch_urlopen.return_value = _FakeResp(json.dumps(_TAGS_PAYLOAD).encode("utf-8"))
    result = runner.invoke(ollama_app, ["set-url", "http://example:11434"])
    assert result.exit_code == 0
    assert calls == [("ollama.base_url", "http://example:11434")]


def test_cli_set_url_skip_probe(monkeypatch, patch_urlopen) -> None:
    """``--skip-probe`` should not invoke ``fetch_tags``."""

    from corpus_forge.admin import config as admin_config

    monkeypatch.setattr(admin_config, "_set_config_value_atomic", lambda *_a, **_k: None)
    monkeypatch.setattr(ollama_mod, "_set_config_value_atomic", lambda *_a, **_k: None)

    result = runner.invoke(ollama_app, ["set-url", "http://example:11434", "--skip-probe"])
    assert result.exit_code == 0
    patch_urlopen.assert_not_called()


def test_cli_test_falls_back_to_probe_when_no_embedder(monkeypatch, patch_urlopen) -> None:
    """``ollama test`` against a config with no Ollama embedder probes tags."""

    from typing import ClassVar

    from corpus_forge import config as cf_config

    class _FakeCfg:
        embedders: ClassVar[list] = []
        ollama = None

    monkeypatch.setattr(cf_config.Config, "load", classmethod(lambda cls: _FakeCfg()))
    patch_urlopen.return_value = _FakeResp(json.dumps(_TAGS_PAYLOAD).encode("utf-8"))
    result = runner.invoke(ollama_app, ["test"])
    assert result.exit_code == 0
