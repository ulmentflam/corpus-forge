"""CI-1 pytest-timeout wiring.

The repo configures `--timeout=60 --timeout-method=thread` globally. This
test uses pytester to run a deliberately deadlocked test under a tight
2-second timeout and confirms pytest-timeout kills it.

We do NOT install a hanging test into the main suite — that would block CI
forever. The hanging test lives inline in a pytester sub-process and is
bounded by a 2s timeout, not the default 60s.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


def test_pytest_timeout_kills_deadlocked_test(pytester: pytest.Pytester) -> None:
    """A deliberately deadlocked test under timeout=2 must fail (not hang)."""
    pytester.makepyfile(
        test_hang=(
            "import threading\n"
            "\n"
            "def test_deadlock():\n"
            "    # An event that's never set deadlocks .wait() forever, which is\n"
            "    # exactly what pytest-timeout should kill via the thread method.\n"
            "    threading.Event().wait()\n"
        )
    )
    pytester.makeini(
        "[pytest]\n"
        "addopts = --timeout=2 --timeout-method=thread --strict-markers\n"
    )
    # 30s outer cap on the subprocess so a misconfig still fails the
    # principal test instead of hanging the suite indefinitely.
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", timeout=30)
    # The hanging test should be reported as a failure (timeout = failure).
    result.assert_outcomes(failed=1)


def test_pytest_timeout_plugin_is_importable() -> None:
    """Smoke: pytest-timeout is installed as a dev dependency."""
    import pytest_timeout  # noqa: F401
