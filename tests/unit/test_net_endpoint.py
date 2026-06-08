"""RFC fleet-4 items 2-3 — ``corpus_forge.net.endpoint`` unit tests.

Covers the ``resolve_endpoint`` contract: the ``ts://`` parse +
scheme-substitution matrix, the passthrough inviolability bar (non-``ts://``
values are byte-identical AND never import the tailscale module), the
disabled-Tailscale error, and the doctor-pointer on resolution failure.

The tailscale boundary is patched at ``corpus_forge.net.tailscale.resolve``
(the function ``resolve_endpoint`` calls) — the real binary is never on the
test path. Passthrough is asserted via a ``sys.modules`` sentinel so a stray
import on the no-Tailscale path fails loudly.
"""

from __future__ import annotations

import sys

import pytest

from corpus_forge.net import EndpointResolutionError, TailscaleUnavailable
from corpus_forge.net.endpoint import resolve_endpoint, resolve_endpoint_for


@pytest.fixture
def patched_resolve(monkeypatch: pytest.MonkeyPatch):
    """Patch ``tailscale.resolve`` to an identity no-op (MagicDNS-on shape)."""
    import corpus_forge.net.tailscale as ts

    calls: list[tuple[str, bool]] = []

    def fake_resolve(name: str, *, prefer_magicdns: bool = True) -> str:
        calls.append((name, prefer_magicdns))
        return name

    monkeypatch.setattr(ts, "resolve", fake_resolve)
    return calls


# ── ts:// parse + scheme-substitution matrix ────────────────────────────


@pytest.mark.parametrize(
    ("value", "default_scheme", "expected"),
    [
        ("ts://gb10", "http", "http://gb10"),
        ("ts://gb10:11434", "http", "http://gb10:11434"),
        ("ts://gb10:5432/corpus", "postgresql", "postgresql://gb10:5432/corpus"),
        ("ts://gb10/v1", "https", "https://gb10/v1"),
        ("ts://gb10:8000/v1/chat", "http", "http://gb10:8000/v1/chat"),
        ("ts://my-host.with.dots", "http", "http://my-host.with.dots"),
    ],
)
def test_ts_scheme_substitution_matrix(
    patched_resolve, value: str, default_scheme: str, expected: str
) -> None:
    out = resolve_endpoint(value, tailscale_enabled=True, default_scheme=default_scheme)
    assert out == expected


def test_resolve_substitutes_resolved_ip_when_magicdns_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MagicDNS-off: the NAME maps to a tailnet IP, port+path carry over."""
    import corpus_forge.net.tailscale as ts

    monkeypatch.setattr(ts, "resolve", lambda name, *, prefer_magicdns=True: "100.124.253.81")
    out = resolve_endpoint(
        "ts://gb10:5432/corpus",
        tailscale_enabled=True,
        prefer_magicdns=False,
        default_scheme="postgresql",
    )
    assert out == "postgresql://100.124.253.81:5432/corpus"


def test_prefer_magicdns_flows_through(patched_resolve) -> None:
    resolve_endpoint("ts://gb10", tailscale_enabled=True, prefer_magicdns=False)
    assert patched_resolve == [("gb10", False)]


# ── passthrough inviolability ───────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:11434",
        "https://api.openai.com/v1",
        "postgresql://user@host:5432/db",
        "dbname=corpus host=100.1.1.1 port=5432",
        "/var/lib/corpus.db",
        "",
    ],
)
def test_non_ts_passthrough_byte_identical(value: str) -> None:
    """Non-ts:// values are returned unchanged regardless of enabled flag."""
    assert resolve_endpoint(value, tailscale_enabled=True) == value
    assert resolve_endpoint(value, tailscale_enabled=False) == value


def test_passthrough_never_imports_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-Tailscale hard bar: a non-ts:// value must not trigger the
    tailscale import. We poison the module entry so any import raises.
    """
    # Drop the cached module and install a sentinel that explodes on access
    # of any attribute the endpoint code would touch (resolve / TailscaleUnavailable).
    monkeypatch.delitem(sys.modules, "corpus_forge.net.tailscale", raising=False)

    class _Poison:
        def __getattr__(self, name: str):  # pragma: no cover - only hit on a bug
            raise AssertionError(
                f"tailscale module was imported on the passthrough path (attr {name!r})"
            )

    monkeypatch.setitem(sys.modules, "corpus_forge.net.tailscale", _Poison())
    # http / dsn / plain host all pass through with the poison in place.
    assert resolve_endpoint("http://x:1", tailscale_enabled=True) == "http://x:1"
    assert resolve_endpoint("postgresql://h/db", tailscale_enabled=False) == "postgresql://h/db"


# ── disabled + ts:// ────────────────────────────────────────────────────


def test_ts_while_disabled_raises_with_fixit() -> None:
    with pytest.raises(EndpointResolutionError) as ei:
        resolve_endpoint("ts://gb10:11434", tailscale_enabled=False)
    msg = str(ei.value)
    assert "ts://gb10:11434" in msg
    assert "enabled = true" in msg


def test_disabled_does_not_import_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even the ts://-while-disabled error path must not import tailscale —
    the enabled check happens before the lazy import.
    """
    monkeypatch.delitem(sys.modules, "corpus_forge.net.tailscale", raising=False)

    class _Poison:
        def __getattr__(self, name: str):  # pragma: no cover - only hit on a bug
            raise AssertionError("tailscale imported on the disabled error path")

    monkeypatch.setitem(sys.modules, "corpus_forge.net.tailscale", _Poison())
    with pytest.raises(EndpointResolutionError):
        resolve_endpoint("ts://gb10", tailscale_enabled=False)


# ── malformed ts:// ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["ts://", "ts://:5432", "ts:///corpus", "ts://-bad"])
def test_malformed_ts_raises(value: str) -> None:
    with pytest.raises(EndpointResolutionError):
        resolve_endpoint(value, tailscale_enabled=True)


# ── TailscaleUnavailable propagation + doctor pointer ───────────────────


@pytest.mark.parametrize("reason", ["daemon", "name"])
def test_unavailable_propagates_with_doctor_pointer(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    import corpus_forge.net.tailscale as ts

    def boom(name: str, *, prefer_magicdns: bool = True) -> str:
        raise TailscaleUnavailable("original remediation text", reason=reason)

    monkeypatch.setattr(ts, "resolve", boom)
    with pytest.raises(TailscaleUnavailable) as ei:
        resolve_endpoint("ts://gb10", tailscale_enabled=True)
    assert ei.value.reason == reason
    msg = str(ei.value)
    assert "original remediation text" in msg
    assert "corpus-forge doctor" in msg


# ── resolve_endpoint_for (config convenience) ───────────────────────────


class _FakeTailscale:
    def __init__(self, enabled: bool, prefer_magicdns: bool = True) -> None:
        self.enabled = enabled
        self.prefer_magicdns = prefer_magicdns


class _FakeConfig:
    def __init__(self, enabled: bool, prefer_magicdns: bool = True) -> None:
        self.tailscale = _FakeTailscale(enabled, prefer_magicdns)


def test_resolve_endpoint_for_reads_config_flags(patched_resolve) -> None:
    cfg = _FakeConfig(enabled=True, prefer_magicdns=False)
    out = resolve_endpoint_for("ts://gb10:5432/db", cfg, default_scheme="postgresql")  # type: ignore[arg-type]
    assert out == "postgresql://gb10:5432/db"
    assert patched_resolve == [("gb10", False)]


def test_resolve_endpoint_for_disabled_raises() -> None:
    cfg = _FakeConfig(enabled=False)
    with pytest.raises(EndpointResolutionError):
        resolve_endpoint_for("ts://gb10", cfg)  # type: ignore[arg-type]


def test_resolve_endpoint_for_passthrough() -> None:
    cfg = _FakeConfig(enabled=False)
    assert resolve_endpoint_for("http://x:1", cfg) == "http://x:1"  # type: ignore[arg-type]
