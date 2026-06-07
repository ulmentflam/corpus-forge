"""RFC fleet-4 — consumption-point resolution tests.

Every place a ``ts://`` URL/DSN is consumed must route through
``resolve_endpoint`` so the tailnet name is turned into a connectable
address. These tests patch ``tailscale.resolve`` (the single boundary)
and assert each consumption point hands the resolved value to the
concrete client / connection. They also assert the plain-URL path is
byte-identical (no resolution side effects).
"""

from __future__ import annotations

import pytest

from corpus_forge.config import (
    ClassifierConfig,
    EmbedderConfig,
    EnricherConfig,
    TailscaleConfig,
    VLMConfig,
    WhisperConfig,
)


@pytest.fixture
def magicdns_on(monkeypatch: pytest.MonkeyPatch):
    """``tailscale.resolve`` → identity (MagicDNS-on no-op rename)."""
    import corpus_forge.net.tailscale as ts

    monkeypatch.setattr(ts, "resolve", lambda name, *, prefer_magicdns=True: name)


class _Cfg:
    """Minimal duck-typed Config carrying just the blocks under test."""

    def __init__(self, *, tailscale: TailscaleConfig, **blocks: object) -> None:
        self.tailscale = tailscale
        for key, value in blocks.items():
            setattr(self, key, value)


_TS_ON = TailscaleConfig(enabled=True)


# ── embedder registry (base_url) ────────────────────────────────────────


def test_embedder_base_url_resolved(magicdns_on) -> None:
    from corpus_forge.embedders.registry import _per_provider_extras

    cfg = EmbedderConfig(
        name="r", provider="openai", model_id="m", dimension=1, base_url="ts://gb10:8000/v1"
    )
    extras = _per_provider_extras(cfg, _TS_ON)
    assert extras["base_url"] == "http://gb10:8000/v1"


def test_embedder_base_url_plain_passthrough_without_tailscale() -> None:
    from corpus_forge.embedders.registry import _per_provider_extras

    cfg = EmbedderConfig(
        name="r", provider="openai", model_id="m", dimension=1, base_url="http://host:8000/v1/"
    )
    # No tailscale arg → pre-RFC behaviour, plain str + rstrip.
    assert _per_provider_extras(cfg)["base_url"] == "http://host:8000/v1"


# ── classifier (llm_url) ────────────────────────────────────────────────


def test_classifier_llm_url_resolved(magicdns_on) -> None:
    pytest.importorskip("requests")
    from corpus_forge.classifiers import _load_classifier

    cfg = ClassifierConfig(llm_url="ts://gb10:11434")
    clf = _load_classifier("llm", cfg, _TS_ON)
    assert clf.llm_url == "http://gb10:11434"


def test_classifier_llm_url_plain_without_tailscale() -> None:
    pytest.importorskip("requests")
    from corpus_forge.classifiers import _load_classifier

    cfg = ClassifierConfig(llm_url="http://host:11434")
    clf = _load_classifier("llm", cfg)
    assert clf.llm_url == "http://host:11434"


# ── VLM registry (ollama_url / mistral_base_url) ────────────────────────


def test_vlm_ollama_url_resolved(magicdns_on, monkeypatch: pytest.MonkeyPatch) -> None:
    ocr = pytest.importorskip("corpus_forge.vlm.ollama")
    from corpus_forge.vlm.registry import get_active_vlm, registry

    captured: dict[str, object] = {}

    class _Fake:
        name = "ollama"

        def __init__(self, *, model: str, ollama_url: str, timeout_s: float) -> None:
            captured["ollama_url"] = ollama_url

    monkeypatch.setattr(ocr, "OllamaVLM", _Fake)
    registry.clear()
    cfg = _Cfg(tailscale=_TS_ON, vlm=VLMConfig(backend="ollama", ollama_url="ts://gb10:11434"))
    get_active_vlm(cfg)  # type: ignore[arg-type]
    assert captured["ollama_url"] == "http://gb10:11434"


# ── whisper registry (remote_base_url) ──────────────────────────────────


def test_whisper_remote_base_url_resolved(magicdns_on, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = pytest.importorskip("corpus_forge.whisper.remote")
    from corpus_forge.whisper.registry import get_active_whisper, registry

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured: dict[str, object] = {}

    class _Fake:
        name = "remote"

        def __init__(self, *, base_url: str, model: str, api_key: str, timeout_s: float) -> None:
            captured["base_url"] = base_url

    monkeypatch.setattr(remote, "RemoteWhisper", _Fake)
    registry.clear()
    cfg = _Cfg(
        tailscale=_TS_ON,
        whisper=WhisperConfig(backend="remote", remote_base_url="ts://gb10:9000/v1"),
    )
    get_active_whisper(cfg)  # type: ignore[arg-type]
    assert captured["base_url"] == "http://gb10:9000/v1"


# ── enricher (local_url / remote_url) ───────────────────────────────────


def test_enricher_local_url_resolved(magicdns_on, monkeypatch: pytest.MonkeyPatch) -> None:
    local = pytest.importorskip("corpus_forge.enrichers.qwen_local")
    from corpus_forge import enrichers

    captured: dict[str, object] = {}

    class _Fake:
        name = "qwen_local"

        def __init__(self, *, model: str, llm_url: str, timeout_s: float, temperature: float):
            captured["llm_url"] = llm_url

    monkeypatch.setattr(local, "QwenCoderLocal", _Fake)
    enrichers.registry.clear()
    cfg = _Cfg(
        tailscale=_TS_ON,
        code_enricher=EnricherConfig(backend="local", local_url="ts://gb10:11434"),
    )
    enrichers.get_active_enricher(cfg)  # type: ignore[arg-type]
    assert captured["llm_url"] == "http://gb10:11434"


# ── backend DSN (postgres helper + conn.open_conn) ──────────────────────


def test_postgres_dsn_helper_resolves(magicdns_on, monkeypatch: pytest.MonkeyPatch) -> None:
    # The helper lazy-imports ``get_config`` from corpus_forge.config only
    # on the ts:// path; patch it there to surface enabled tailscale.
    import corpus_forge.config as cfg_mod
    from corpus_forge.backends import postgres

    fake = _Cfg(tailscale=_TS_ON)
    monkeypatch.setattr(cfg_mod, "get_config", lambda: fake)
    out = postgres._resolve_dsn_endpoint("ts://gb10:5432/corpus")
    assert out == "postgresql://gb10:5432/corpus"


def test_postgres_dsn_helper_plain_passthrough() -> None:
    from corpus_forge.backends import postgres

    # Plain DSN: no get_config, no tailscale import, byte-identical.
    assert (
        postgres._resolve_dsn_endpoint("postgresql://localhost/forge")
        == "postgresql://localhost/forge"
    )


def test_open_conn_resolves_ts_dsn(magicdns_on, monkeypatch: pytest.MonkeyPatch) -> None:
    psycopg = pytest.importorskip("psycopg")
    from corpus_forge.backends import conn as conn_mod

    captured: dict[str, object] = {}
    monkeypatch.setattr(psycopg, "connect", lambda dsn: captured.setdefault("dsn", dsn))

    class _Backend:
        kind = "postgres"
        dsn = "ts://gb10:5432/corpus"

    cfg = _Cfg(tailscale=_TS_ON, backend=_Backend())
    conn_mod.open_conn(cfg)
    assert captured["dsn"] == "postgresql://gb10:5432/corpus"
