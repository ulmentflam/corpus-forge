"""Unit tests for :mod:`corpus_forge.setup.select`.

The arrow-key / checkbox / fuzzy selection helpers wrap ``questionary``
behind a lazy import + typed-prompt fallback. Both paths are covered for
every helper:

- **Rich path** — a stubbed ``questionary`` whose ``.ask()`` yields a
  known value, exercised by forcing the TTY check on. Asserts the
  wrapper returns / maps the questionary answer.
- **Fallback path** — forced via the wizard's ``stream_in``/``stream_out``
  seam (the same injection point the wizard tests use). Asserts the
  helper routes through ``_read_answer_interactive`` and returns the
  stream-provided answer.
- **Import-failure path** — ``questionary`` import monkeypatched to
  raise ``ImportError``; asserts a clean fall-through to the typed
  prompt even on a (faked) TTY.
"""

from __future__ import annotations

import builtins
import io
import sys
from types import SimpleNamespace

import pytest

from corpus_forge.setup import select

# ── helpers ───────────────────────────────────────────────────────────


class _Stub:
    """Stand-in for a questionary prompt object: ``.ask()`` -> value."""

    def __init__(self, value: object) -> None:
        self._value = value
        self.calls: list[tuple[tuple, dict]] = []

    def ask(self) -> object:
        return self._value


def _force_tty(monkeypatch: pytest.MonkeyPatch, *, on: bool = True) -> None:
    """Make ``_use_fallback`` see (or not see) an interactive terminal."""
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: on))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: on))


def _install_stub_questionary(
    monkeypatch: pytest.MonkeyPatch, **methods: object
) -> SimpleNamespace:
    """Install a fake ``questionary`` module into ``sys.modules``.

    ``methods`` maps e.g. ``select=<stub>`` to the object returned by
    ``questionary.select(...)``. ``Choice`` is provided as a tiny
    record so ``pick_many`` can build checkbox choices.
    """

    def _make_factory(stub: _Stub):
        def _factory(*args: object, **kwargs: object) -> _Stub:
            stub.calls.append((args, kwargs))
            return stub

        return _factory

    fake = SimpleNamespace(
        Choice=lambda title, value, checked=False: SimpleNamespace(
            title=title, value=value, checked=checked
        ),
    )
    for name, stub in methods.items():
        setattr(fake, name, _make_factory(stub))
    monkeypatch.setitem(sys.modules, "questionary", fake)
    return fake


# ── pick_one ──────────────────────────────────────────────────────────


class TestPickOne:
    def test_rich_path_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _install_stub_questionary(monkeypatch, select=_Stub("postgres"))
        out = select.pick_one(
            "Backend?",
            [("PostgreSQL", "postgres"), ("SQLite", "sqlite")],
            default="sqlite",
        )
        assert out == "postgres"

    def test_rich_path_maps_label_to_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # questionary returns the *label*; the wrapper maps it to value.
        _force_tty(monkeypatch)
        _install_stub_questionary(monkeypatch, select=_Stub("PostgreSQL"))
        out = select.pick_one(
            "Backend?",
            [("PostgreSQL", "postgres"), ("SQLite", "sqlite")],
        )
        assert out == "postgres"

    def test_fallback_via_stream_seam(self) -> None:
        out = select.pick_one(
            "Backend?",
            ["postgres", "sqlite"],
            default="sqlite",
            stream_in=io.StringIO("postgres\n"),
            stream_out=io.StringIO(),
        )
        assert out == "postgres"

    def test_fallback_empty_input_uses_default(self) -> None:
        out = select.pick_one(
            "Backend?",
            ["postgres", "sqlite"],
            default="sqlite",
            stream_in=io.StringIO("\n"),
            stream_out=io.StringIO(),
        )
        assert out == "sqlite"

    def test_import_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _raise_on_questionary_import(monkeypatch)
        # No stream seam → it WOULD take the rich path, but the import
        # raises, so it must fall back to the typed prompt (stdin).
        monkeypatch.setattr(sys, "stdin", io.StringIO("postgres\n"))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        out = select.pick_one("Backend?", ["postgres", "sqlite"], default="sqlite")
        assert out == "postgres"


# ── pick_many ─────────────────────────────────────────────────────────


class TestPickMany:
    def test_rich_path_returns_values_in_choice_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_tty(monkeypatch)
        # questionary returns values out of order; wrapper re-orders.
        _install_stub_questionary(monkeypatch, checkbox=_Stub(["whisper", "ocr"]))
        out = select.pick_many(
            "Extras?",
            ["ocr", "whisper", "code"],
            defaults=["ocr"],
        )
        assert out == ["ocr", "whisper"]

    def test_rich_path_empty_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _install_stub_questionary(monkeypatch, checkbox=_Stub([]))
        out = select.pick_many("Extras?", ["ocr", "whisper"])
        assert out == []

    def test_fallback_via_stream_seam(self) -> None:
        out = select.pick_many(
            "Extras?",
            ["ocr", "whisper", "code"],
            stream_in=io.StringIO("ocr whisper\n"),
            stream_out=io.StringIO(),
        )
        assert out == ["ocr", "whisper"]

    def test_fallback_comma_separated_and_drops_unknown(self) -> None:
        out = select.pick_many(
            "Extras?",
            ["ocr", "whisper", "code"],
            stream_in=io.StringIO("whisper, bogus, code\n"),
            stream_out=io.StringIO(),
        )
        assert out == ["whisper", "code"]

    def test_import_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _raise_on_questionary_import(monkeypatch)
        monkeypatch.setattr(sys, "stdin", io.StringIO("ocr\n"))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        out = select.pick_many("Extras?", ["ocr", "whisper"])
        assert out == ["ocr"]


# ── ask_text ──────────────────────────────────────────────────────────


class TestAskText:
    def test_rich_path_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _install_stub_questionary(monkeypatch, text=_Stub("postgresql://host/db"))
        out = select.ask_text("DSN?", default="postgresql://localhost/cf")
        assert out == "postgresql://host/db"

    def test_fallback_via_stream_seam(self) -> None:
        out = select.ask_text(
            "DSN?",
            default="postgresql://localhost/cf",
            stream_in=io.StringIO("postgresql://host/db\n"),
            stream_out=io.StringIO(),
        )
        assert out == "postgresql://host/db"

    def test_fallback_empty_uses_default(self) -> None:
        out = select.ask_text(
            "DSN?",
            default="postgresql://localhost/cf",
            stream_in=io.StringIO("\n"),
            stream_out=io.StringIO(),
        )
        assert out == "postgresql://localhost/cf"

    def test_import_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _raise_on_questionary_import(monkeypatch)
        monkeypatch.setattr(sys, "stdin", io.StringIO("typed\n"))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        out = select.ask_text("DSN?", default="default")
        assert out == "typed"


# ── confirm ───────────────────────────────────────────────────────────


class TestConfirm:
    def test_rich_path_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _install_stub_questionary(monkeypatch, confirm=_Stub(True))
        assert select.confirm("Enable MCP?", default=False) is True

    def test_rich_path_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _install_stub_questionary(monkeypatch, confirm=_Stub(False))
        assert select.confirm("Enable MCP?", default=True) is False

    def test_fallback_via_stream_seam_yes(self) -> None:
        out = select.confirm(
            "Enable MCP?",
            default=False,
            stream_in=io.StringIO("y\n"),
            stream_out=io.StringIO(),
        )
        assert out is True

    def test_fallback_empty_uses_default(self) -> None:
        out = select.confirm(
            "Enable MCP?",
            default=True,
            stream_in=io.StringIO("\n"),
            stream_out=io.StringIO(),
        )
        assert out is True

    def test_import_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch)
        _raise_on_questionary_import(monkeypatch)
        monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        assert select.confirm("Enable MCP?", default=True) is False


# ── fallback gating ───────────────────────────────────────────────────


class TestUseFallback:
    def test_stream_seam_forces_fallback(self) -> None:
        assert select._use_fallback(io.StringIO(), None) is True
        assert select._use_fallback(None, io.StringIO()) is True

    def test_non_tty_forces_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch, on=False)
        assert select._use_fallback(None, None) is True

    def test_tty_uses_rich(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_tty(monkeypatch, on=True)
        assert select._use_fallback(None, None) is False


# ── shared import-failure shim ────────────────────────────────────────


def _raise_on_questionary_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import questionary`` raise ImportError inside the helpers."""
    monkeypatch.delitem(sys.modules, "questionary", raising=False)
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object):
        if name == "questionary":
            raise ImportError("simulated missing questionary")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
