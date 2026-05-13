"""CI-2 — ``requires_unix`` marker wiring.

Pins that the conftest hook adds a skip marker to ``requires_unix`` items
when running on Windows (and leaves them alone elsewhere).  We don't
actually run pytest in a subprocess; we monkey-patch ``sys.platform`` and
call the hook with fake items.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_conftest():
    mod_name = "tests.conftest"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


class _FakeItem:
    def __init__(self, module_name: str, *keywords: str) -> None:
        fake_mod = type("FakeMod", (), {})
        fake_mod.__name__ = module_name
        self.module = fake_mod
        self.keywords = set(keywords)
        self._markers: list = []

    def add_marker(self, marker) -> None:
        self._markers.append(marker)


class TestRequiresUnixGate:
    """Items keyworded ``requires_unix`` get skipped only on Windows."""

    def test_skips_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        conftest = _reload_conftest()

        item = _FakeItem("tests.unit.test_x", "requires_unix")
        items = [item]
        conftest.pytest_collection_modifyitems(None, items)  # type: ignore[arg-type]

        assert item._markers, "Expected requires_unix item to be skipped on Windows"
        marker = item._markers[0]
        reason = getattr(marker, "kwargs", {}).get("reason") or getattr(marker, "reason", "")
        assert "requires_unix" in str(reason) or "POSIX" in str(reason), (
            f"skip reason should mention requires_unix or POSIX; got {reason!r}"
        )

    def test_runs_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        conftest = _reload_conftest()

        item = _FakeItem("tests.unit.test_x", "requires_unix")
        items = [item]
        conftest.pytest_collection_modifyitems(None, items)  # type: ignore[arg-type]

        assert not any(
            "requires_unix" in str(getattr(m, "kwargs", {}).get("reason", ""))
            or "POSIX" in str(getattr(m, "kwargs", {}).get("reason", ""))
            for m in item._markers
        ), "Linux runner must not skip requires_unix items"

    def test_runs_on_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        conftest = _reload_conftest()

        item = _FakeItem("tests.unit.test_x", "requires_unix")
        items = [item]
        conftest.pytest_collection_modifyitems(None, items)  # type: ignore[arg-type]

        assert not any(
            "requires_unix" in str(getattr(m, "kwargs", {}).get("reason", ""))
            or "POSIX" in str(getattr(m, "kwargs", {}).get("reason", ""))
            for m in item._markers
        ), "macOS runner must not skip requires_unix items"

    def test_unmarked_item_not_skipped_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An item without the marker is not affected by the Windows gate."""
        monkeypatch.setattr(sys, "platform", "win32")
        conftest = _reload_conftest()

        item = _FakeItem("tests.unit.test_y")  # no keywords
        items = [item]
        conftest.pytest_collection_modifyitems(None, items)  # type: ignore[arg-type]

        # The only legitimate skip would be CI_NO_DOCKER for integration
        # items, which this isn't.  Markers list must be empty.
        assert not item._markers, f"Unmarked unit item should not be skipped; got {item._markers}"
