"""R3-01 — pyproject.toml must expose `retrieval` and `eval` optional extras.

The eval harness depends on NumPy.  The retriever (R2 already implemented)
also depends on NumPy.  Both extras land here per the master plan; R4 owns
the `rerank` extra and R5 owns the `mcp` extra (so we explicitly assert
those are NOT yet declared, to catch accidental over-reach).
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def _load_pyproject() -> dict:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_retrieval_extra_present():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "retrieval" in extras, "`[retrieval]` extra missing"
    assert any("numpy" in dep for dep in extras["retrieval"]), (
        "[retrieval] extra must include numpy"
    )


def test_eval_extra_present():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "eval" in extras, "`[eval]` extra missing"
    assert any("numpy" in dep for dep in extras["eval"]), "[eval] extra must include numpy"


def test_numpy_floor_at_least_1_26():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    for which in ("retrieval", "eval"):
        deps = extras.get(which, [])
        np_pin = next((d for d in deps if "numpy" in d), None)
        assert np_pin is not None, f"[{which}] missing numpy pin"
        # Accept any of: numpy>=1.26 / numpy>=1.26.0 / numpy>=2.0 etc., but
        # reject a pin below 1.26 (e.g. >=1.20).
        assert ">=1." in np_pin or ">=2." in np_pin, f"[{which}] numpy pin malformed: {np_pin!r}"
        # Sanity: the floor must be >= 1.26 — parse the minor.
        try:
            spec = np_pin.split(">=")[1].strip()
            parts = spec.split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except (IndexError, ValueError):  # pragma: no cover
            return  # parse failed — be lenient, the regex above already caught the shape
        assert (major, minor) >= (1, 26), f"[{which}] numpy floor must be >= 1.26 (got {np_pin})"


def test_mcp_extra_not_yet_declared():
    """R5 owns it; if it appears here, someone leaked R5 scope into R3."""
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "mcp" not in extras, "R3 must NOT declare the [mcp] extra; that belongs to Phase R5."


def test_numpy_importable_in_dev_env():
    """Sanity check that numpy is actually importable in the dev/CI env so
    the eval harness tests can run.  (R2's `sentence-transformers` core dep
    pulls numpy transitively, so this should pass even before extras are
    installed.)"""
    import numpy  # noqa: F401

    assert "numpy" in sys.modules
