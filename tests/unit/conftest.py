"""Unit-test conftest — patches typer.testing.CliRunner to accept mix_stderr.

typer >= 0.21 removed the ``mix_stderr`` parameter (the underlying click 8.x
CliRunner no longer supports it).  Several SR-T8 tests construct
``CliRunner(mix_stderr=False)`` because they want separate stderr access;
the parameter is silently ignored here since click 8.x mixes by default and
the test helper ``_combined()`` already merges output+stderr defensively.
"""

from __future__ import annotations

import typer.testing as _typer_testing

if not getattr(_typer_testing.CliRunner, "_mix_stderr_patched", False):
    _orig_init = _typer_testing.CliRunner.__init__

    def _patched_init(self, *args, mix_stderr=None, **kwargs):  # type: ignore[override]
        _orig_init(self, *args, **kwargs)

    _typer_testing.CliRunner.__init__ = _patched_init  # type: ignore[method-assign]
    _typer_testing.CliRunner._mix_stderr_patched = True  # type: ignore[attr-defined]
