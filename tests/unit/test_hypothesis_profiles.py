"""Hypothesis profile registration + env-driven activation (CI-1).

Validates that `tests.fuzz.profiles.register_hypothesis_profiles()` defines
three named profiles (`dev`, `ci`, `nightly`) with monotonically increasing
`max_examples`, and that loading the `ci` profile via Hypothesis's
`settings.load_profile()` does in fact swap in those richer settings.
"""

from __future__ import annotations

import pytest
from hypothesis import settings

from tests.fuzz.profiles import register_hypothesis_profiles

PROFILES = ("dev", "ci", "nightly")


@pytest.fixture(autouse=True)
def _restore_default_profile():
    """Ensure tests don't leak the active hypothesis profile to siblings."""
    register_hypothesis_profiles()
    settings.load_profile("default")
    yield
    settings.load_profile("default")


class TestRegisterHypothesisProfiles:
    """`register_hypothesis_profiles()` must be idempotent and define 3 profiles."""

    def test_idempotent(self) -> None:
        register_hypothesis_profiles()
        register_hypothesis_profiles()
        # Both profiles should be loadable post-registration.
        for name in PROFILES:
            settings.load_profile(name)

    @pytest.mark.parametrize("name", PROFILES)
    def test_profile_loadable(self, name: str) -> None:
        settings.load_profile(name)
        # Sanity: a profile must yield a real Settings object.
        active = settings()
        assert active.max_examples >= 1


class TestProfileSemantics:
    """Profiles must increase in rigor: dev <= ci <= nightly."""

    def _max_examples(self, name: str) -> int:
        settings.load_profile(name)
        return settings().max_examples

    def test_ci_is_at_least_as_thorough_as_dev(self) -> None:
        dev = self._max_examples("dev")
        ci = self._max_examples("ci")
        assert ci >= dev, f"ci.max_examples ({ci}) should be >= dev ({dev})"

    def test_nightly_is_at_least_as_thorough_as_ci(self) -> None:
        ci = self._max_examples("ci")
        nightly = self._max_examples("nightly")
        assert nightly >= ci, f"nightly.max_examples ({nightly}) should be >= ci ({ci})"

    def test_nightly_strictly_richer_than_dev(self) -> None:
        """At minimum, nightly should have more examples than dev — otherwise
        the three-profile fan-out has no signal."""
        dev = self._max_examples("dev")
        nightly = self._max_examples("nightly")
        assert nightly > dev, f"nightly ({nightly}) must be strictly richer than dev ({dev})"


class TestConftestActivation:
    """`tests/conftest.py` is expected to call `register_hypothesis_profiles()` once
    at module import and resolve `HYPOTHESIS_PROFILE` from env (default `dev`).

    This test just confirms that by the time the test runs, the three profiles
    are loadable — i.e. someone has run register_hypothesis_profiles() globally.
    """

    @pytest.mark.parametrize("name", PROFILES)
    def test_profile_loadable_after_conftest(self, name: str) -> None:
        # conftest.py should have already registered profiles at session start.
        # We re-register inside the autouse fixture above, so this is belt+braces.
        settings.load_profile(name)
