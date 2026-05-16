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


@pytest.mark.requires_unix
def test_pytest_timeout_kills_deadlocked_test(pytester: pytest.Pytester) -> None:
    """A deliberately deadlocked test under timeout=2 must fail (not hang).

    We do NOT use ``--timeout-method=thread`` here: that method dumps stacks
    and then calls ``os._exit()`` on the worker process, which prevents
    pytest from printing the terminal summary line that ``assert_outcomes``
    parses. ``signal`` is the inverse — it raises ``Failed`` inside the
    deadlocked test and pytest produces a real terminal summary with
    ``failed=1``. The repo-level addopts pin remains ``thread`` (safer
    for I/O-blocked tests in the real suite); this isolated subprocess
    intentionally uses ``signal`` purely so we can ASSERT the failure.

    Marked ``requires_unix``: pytest-timeout's ``signal`` method needs
    ``signal.SIGALRM``, which is POSIX-only. The repo-level addopts use
    ``--timeout-method=thread`` so the real suite still runs on Windows;
    only this isolated meta-test is signal-gated.
    """
    pytester.makepyfile(
        test_hang=(
            "import threading\n"
            "\n"
            "def test_deadlock():\n"
            "    # An event that's never set deadlocks .wait() forever, which is\n"
            "    # exactly what pytest-timeout should kill.\n"
            "    threading.Event().wait()\n"
        )
    )
    pytester.makeini("[pytest]\naddopts = --timeout=2 --timeout-method=signal --strict-markers\n")
    # 30s outer cap on the subprocess so a misconfig still fails the
    # principal test instead of hanging the suite indefinitely.
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", timeout=30)
    # The hanging test should be reported as a failure (timeout = failure).
    result.assert_outcomes(failed=1)


def test_pytest_timeout_thread_method_kills_process(pytester: pytest.Pytester) -> None:
    """Cover the ``thread`` method too: it kills the process (non-zero exit).

    The ``thread`` method dumps stacks and ``os._exit(1)``s the worker, so
    we can't use ``assert_outcomes`` here — we just confirm the subprocess
    didn't hang and exited non-zero in well under the 30s wall clock cap.
    """
    pytester.makepyfile(
        test_hang=("import threading\n\ndef test_deadlock():\n    threading.Event().wait()\n")
    )
    pytester.makeini("[pytest]\naddopts = --timeout=2 --timeout-method=thread --strict-markers\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", timeout=30)
    assert result.ret != 0, (
        f"Expected non-zero exit when --timeout-method=thread fires; got ret={result.ret}"
    )


def test_pytest_timeout_plugin_is_importable() -> None:
    """Smoke: pytest-timeout is installed as a dev dependency."""
    import pytest_timeout  # noqa: F401
