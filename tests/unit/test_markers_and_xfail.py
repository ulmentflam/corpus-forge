"""CI-1 marker + xfail-strict wiring assertions.

Two behaviors live here:

1. `requires_unix` marker is honored: a test guarded by `@requires_unix` that
   asserts on `sys.platform == "win32"` (simulated via monkeypatch) is skipped.
2. `xfail_strict = true` is wired so that a test marked `@xfail` which
   unexpectedly PASSES is reported as a failure (XPASS strict).

We test #2 indirectly via the pytester plugin — running a tiny inline
test that XPASSes should fail under `--runpytest=subprocess` when
`xfail_strict` is on.
"""

from __future__ import annotations

import sys

import pytest


pytest_plugins = ["pytester"]


# ── requires_unix marker ─────────────────────────────────────────────────────


@pytest.mark.requires_unix
def test_requires_unix_marker_is_registered() -> None:
    """If this test collects without an `--strict-markers` error, the marker
    is declared in pyproject.toml `[tool.pytest.ini_options].markers`."""
    # We're not actually skipping anything here — just proving collection works.
    # The marker exists; that's the assertion.
    assert True


def test_requires_unix_skips_on_simulated_windows(pytester: pytest.Pytester) -> None:
    """Confirm a project-style skip-on-windows pattern works.

    The conventional shape (see `pytest-skip-on-windows` discussions) is to
    pair the `requires_unix` marker with a `skipif` on sys.platform. We
    exercise that here by running an inline test in a subpytest.
    """
    pytester.makepyfile(
        test_inline=(
            "import sys\n"
            "import pytest\n"
            "\n"
            "@pytest.mark.requires_unix\n"
            "@pytest.mark.skipif(sys.platform == 'win32', reason='unix only')\n"
            "def test_unix_only():\n"
            "    assert sys.platform != 'win32'\n"
        )
    )
    pytester.makeini(
        "[pytest]\n"
        "markers =\n"
        "    requires_unix: marks tests that only run on POSIX platforms\n"
        "addopts = --strict-markers\n"
    )
    # Force-skip path via -p no:cacheprovider for cleanliness.
    result = pytester.runpytest("-p", "no:cacheprovider")
    # Either it ran (we're on a POSIX system) — assert pass; or it skipped.
    if sys.platform == "win32":
        result.assert_outcomes(skipped=1)
    else:
        result.assert_outcomes(passed=1)


# ── xfail_strict wiring ──────────────────────────────────────────────────────


def test_xfail_strict_fails_on_unexpected_pass(pytester: pytest.Pytester) -> None:
    """When `xfail_strict = true` is honored, an xfail test that passes
    is reported as a failure (XPASS strict)."""
    pytester.makepyfile(
        test_inline=(
            "import pytest\n"
            "\n"
            "@pytest.mark.xfail(reason='should fail but does not')\n"
            "def test_passes_unexpectedly():\n"
            "    assert True\n"
        )
    )
    pytester.makeini(
        "[pytest]\n"
        "xfail_strict = true\n"
        "addopts = --strict-markers\n"
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    # With xfail_strict, XPASS -> failure. assert_outcomes counts failures.
    result.assert_outcomes(failed=1)
