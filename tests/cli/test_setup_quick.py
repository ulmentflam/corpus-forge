"""Phase L Wave 3 — ``corpus-forge setup --quick`` tests.

The quick wizard runs a 6-question subset (backend, postgres_dsn,
ollama_url, embedder_model_id, dataset_name, scan_root). It probes
Ollama's ``/api/tags`` endpoint to default-pick an embedder model from
the locally-available list. The output config must round-trip through
``Config.load()`` cleanly.

Tests pin:
- non-interactive sqlite path produces a valid config (Config.load
  round-trip).
- sqlite path does NOT prompt for the postgres DSN.
- postgres path threads the DSN through to the rendered config.
- the Ollama probe picks a sensible model from the reported tags.
"""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from corpus_forge.cli import app


def _runner() -> CliRunner:
    return CliRunner()


# ── 1. minimal sqlite quick path ──────────────────────────────────────


def test_quick_non_interactive_sqlite_minimal(tmp_path: Path, monkeypatch) -> None:
    """``setup --quick --non-interactive`` with sqlite + a model id env
    writes a config that round-trips through ``Config.load()``."""

    monkeypatch.setenv("CF_BACKEND", "sqlite")
    monkeypatch.setenv("CF_EMBEDDER_MODEL_ID", "qwen3:8b")

    config_dir = tmp_path / "cf"
    result = _runner().invoke(
        app,
        ["setup", "--quick", "--non-interactive", "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0, result.output

    config_path = config_dir / "config.toml"
    assert config_path.exists(), f"config.toml missing under {config_dir}"

    # Raw TOML shape check: backend is sqlite, one embedder, model_id
    # matches the env override.
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["backend"]["kind"] == "sqlite"
    embedders = parsed["embedders"]
    assert len(embedders) == 1
    assert embedders[0]["model_id"] == "qwen3:8b"

    # Round-trip through Config.load() — the canonical "this config is
    # valid" check.
    from corpus_forge.config import Config

    cfg = Config.load(config_path=config_path, secrets_path=config_dir / "secrets.env")
    assert cfg.backend.kind == "sqlite"
    assert any(e.model_id == "qwen3:8b" for e in cfg.embedders)


# ── 2. sqlite path does NOT prompt for DSN ────────────────────────────


def test_quick_sqlite_does_not_prompt_for_dsn(tmp_path: Path) -> None:
    """Interactive ``--quick`` with backend=sqlite must skip the postgres
    DSN prompt entirely.

    Drive via ``run_quick(interactive=True, ...)`` so we can inspect
    ``stream_out`` directly without going through Typer.
    """

    from corpus_forge.setup import run_quick

    # Pre-feed answers: backend=sqlite, then defaults for everything
    # else. The exact answer count depends on the implementation but
    # 20 blank lines is plenty of slack.
    stream_in = io.StringIO("sqlite\n" + "\n" * 20)
    stream_out = io.StringIO()

    with patch("urllib.request.urlopen", side_effect=OSError("no ollama")):
        run_quick(
            config_dir=tmp_path,
            interactive=True,
            stream_in=stream_in,
            stream_out=stream_out,
        )

    transcript = stream_out.getvalue().lower()
    # The DSN prompt's identifying substring must never appear when
    # backend=sqlite. The full wizard's DSN question mentions
    # "postgresql://" — pin that token.
    assert "postgresql://" not in transcript, (
        f"DSN prompt leaked into sqlite quick path:\n{transcript}"
    )


# ── 3. postgres path threads DSN through ──────────────────────────────


def test_quick_postgres_writes_dsn_through(tmp_path: Path, monkeypatch) -> None:
    """``--quick --non-interactive`` with backend=postgres + DSN env var
    writes the DSN unchanged into the rendered config."""

    monkeypatch.setenv("CF_BACKEND", "postgres")
    monkeypatch.setenv("CF_BACKEND_DSN", "postgresql://alice:secret@db.example/cf")
    monkeypatch.setenv("CF_EMBEDDER_MODEL_ID", "qwen3:8b")

    config_dir = tmp_path / "cf"
    result = _runner().invoke(
        app,
        ["setup", "--quick", "--non-interactive", "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0, result.output

    config_path = config_dir / "config.toml"
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["backend"]["kind"] == "postgres"
    assert parsed["backend"]["dsn"] == "postgresql://alice:secret@db.example/cf"

    # Config.load() round-trip — pydantic will accept the DSN unchanged.
    from corpus_forge.config import Config

    cfg = Config.load(config_path=config_path, secrets_path=config_dir / "secrets.env")
    assert cfg.backend.dsn == "postgresql://alice:secret@db.example/cf"


# ── 4. Ollama probe picks the first embed-capable model ───────────────


class _FakeResponse:
    """Minimal context-manager mimicking ``urllib.request.urlopen``."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_probe_picks_first_embed_capable_model() -> None:
    """``_probe_ollama`` returns the first model whose name contains
    'embed', 'bge', 'qwen', or 'nomic' from ``/api/tags``."""

    from corpus_forge.setup import wizard

    payload = {
        "models": [
            {"name": "llama3.2"},
            {"name": "qwen3:8b"},  # qwen substring — should win
            {"name": "bge-m3"},
        ]
    }
    with patch.object(wizard, "_urlopen_compat", return_value=_FakeResponse(payload)):
        picked = wizard._probe_ollama("http://localhost:11434", timeout_s=0.1)
    assert picked == "qwen3:8b"


def test_probe_falls_back_when_no_embed_model() -> None:
    """When no model contains an embed-family substring, the probe
    either returns the first listed model or None — both are
    acceptable for the quick path default."""

    from corpus_forge.setup import wizard

    payload = {"models": [{"name": "llama3.2"}, {"name": "mistral"}]}
    with patch.object(wizard, "_urlopen_compat", return_value=_FakeResponse(payload)):
        picked = wizard._probe_ollama("http://localhost:11434", timeout_s=0.1)
    # Both pinned behaviors are acceptable:
    assert picked in {"llama3.2", None}


def test_probe_returns_none_on_network_failure() -> None:
    """Any exception during the probe must yield ``None`` (no raise)."""

    from corpus_forge.setup import wizard

    with patch.object(wizard, "_urlopen_compat", side_effect=OSError("conn refused")):
        picked = wizard._probe_ollama("http://localhost:11434", timeout_s=0.1)
    assert picked is None


def test_probe_returns_none_on_empty_payload() -> None:
    """Empty model list → ``None``."""

    from corpus_forge.setup import wizard

    with patch.object(wizard, "_urlopen_compat", return_value=_FakeResponse({"models": []})):
        picked = wizard._probe_ollama("http://localhost:11434", timeout_s=0.1)
    assert picked is None


# ── 5. Probed model becomes the embedder default in non-interactive ──


def test_probed_model_becomes_embedder_default(tmp_path: Path, monkeypatch) -> None:
    """When ``CF_EMBEDDER_MODEL_ID`` is unset, the probed model wins."""

    monkeypatch.setenv("CF_BACKEND", "sqlite")
    monkeypatch.delenv("CF_EMBEDDER_MODEL_ID", raising=False)
    monkeypatch.setenv("CF_OLLAMA_URL", "http://localhost:11434")

    payload = {
        "models": [
            {"name": "llama3.2"},
            {"name": "bge-m3"},
        ]
    }
    # Patch into the wizard's namespace so the probe routes to the mock.
    from corpus_forge.setup import wizard

    config_dir = tmp_path / "cf"
    with patch.object(wizard, "_urlopen_compat", return_value=_FakeResponse(payload)):
        result = _runner().invoke(
            app,
            ["setup", "--quick", "--non-interactive", "--config-dir", str(config_dir)],
        )
    assert result.exit_code == 0, result.output

    parsed = tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))
    # bge-m3 is the first embedding-capable model in the mocked payload.
    assert parsed["embedders"][0]["model_id"] == "bge-m3"
