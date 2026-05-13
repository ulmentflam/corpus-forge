"""CI-2 — ``CI_NO_DOCKER`` env var forces integration tests to skip.

When the env var is set the conftest hook must add a skip marker to every
test under ``tests/integration/*`` (Windows runners have no Docker daemon).
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest


def _reload_conftest():
    """Re-import the top-level tests conftest so the env var change re-reads."""
    # Ensure the project root is importable as a package context.
    mod_name = "tests.conftest"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


class TestCINoDockerEnvVar:
    """The env var ``CI_NO_DOCKER=1`` skips integration tests regardless of docker availability."""

    def test_env_var_triggers_skip_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When CI_NO_DOCKER=1, the conftest's _ci_no_docker() helper returns True."""
        monkeypatch.setenv("CI_NO_DOCKER", "1")
        conftest = _reload_conftest()
        # The helper may be named ``_ci_no_docker`` or wired directly into
        # pytest_collection_modifyitems; check for either contract.
        helper = getattr(conftest, "_ci_no_docker", None)
        assert helper is not None, (
            "tests/conftest.py must expose a ``_ci_no_docker()`` helper "
            "or the equivalent guard inside pytest_collection_modifyitems"
        )
        assert helper() is True

    def test_env_var_unset_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI_NO_DOCKER", raising=False)
        conftest = _reload_conftest()
        helper = getattr(conftest, "_ci_no_docker", None)
        assert helper is not None
        assert helper() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_env_var_truthy_values_trigger_skip(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CI_NO_DOCKER", value)
        conftest = _reload_conftest()
        assert conftest._ci_no_docker() is True


class TestCollectionHookSkipsIntegration(object):
    """The pytest_collection_modifyitems hook must mark integration items as skip.

    We do not actually run a sub-pytest; we exercise the hook directly with a
    fake config/items pair and assert the skip marker gets applied.
    """

    def test_integration_item_gets_skip_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI_NO_DOCKER", "1")
        conftest = _reload_conftest()

        class FakeItem:
            def __init__(self, module_name: str) -> None:
                self.module = type("M", (), {"__name__": module_name})
                self.keywords: set[str] = set()
                self._markers: list[pytest.MarkDecorator] = []

            def add_marker(self, marker) -> None:  # noqa: ANN001
                self._markers.append(marker)

        items: list = [FakeItem("tests.integration.test_chunk_reuse_e2e")]
        config = type("Cfg", (), {})()  # not actually inspected by the hook

        conftest.pytest_collection_modifyitems(config, items)  # type: ignore[arg-type]
        assert items[0]._markers, "Expected at least one skip marker on integration item"
        # The marker should mention CI_NO_DOCKER or 'Docker' in its reason.
        marker = items[0]._markers[0]
        reason = getattr(marker, "kwargs", {}).get("reason") or getattr(marker, "reason", "")
        assert "CI_NO_DOCKER" in str(reason) or "Docker" in str(reason), (
            f"skip reason should mention CI_NO_DOCKER or Docker; got: {reason!r}"
        )

    def test_unit_item_is_not_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting CI_NO_DOCKER must not skip unit tests."""
        monkeypatch.setenv("CI_NO_DOCKER", "1")
        conftest = _reload_conftest()

        class FakeItem:
            def __init__(self, module_name: str) -> None:
                self.module = type("M", (), {"__name__": module_name})
                self.keywords: set[str] = set()
                self._markers: list = []

            def add_marker(self, marker) -> None:  # noqa: ANN001
                self._markers.append(marker)

        items: list = [FakeItem("tests.unit.test_phase_ci2_yaml")]
        config = type("Cfg", (), {})()

        conftest.pytest_collection_modifyitems(config, items)  # type: ignore[arg-type]
        assert not items[0]._markers, (
            "CI_NO_DOCKER must NOT skip unit tests; "
            f"got markers: {items[0]._markers!r}"
        )


# Restore an unset state at module-teardown so imports later in the run
# pick up the default no-docker-skip code path.
@pytest.fixture(autouse=True)
def _restore_env():
    saved = os.environ.get("CI_NO_DOCKER")
    yield
    if saved is None:
        os.environ.pop("CI_NO_DOCKER", None)
    else:
        os.environ["CI_NO_DOCKER"] = saved
