"""R5-02 — pyproject.toml must expose the ``mcp`` optional extra.

R3 / R4 deliberately scope-guarded it out.  R5 lands it pinning
``mcp>=1.0,<2.0`` (the upper-bound brackets the v1 API surface so a v2
release does not silently break the in-process MCP server).

Tests pin:

- ``[project.optional-dependencies].mcp`` exists and contains ``mcp``.
- The pinned floor is ``>=1.0`` (any 1.x release works); the cap is ``<2.0``
  to prevent surprise major-version drift in CI.
- The R4 scope-guards have been retired (they're flipped to "mcp present"
  in this PR).
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def _load_pyproject() -> dict:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_mcp_extra_present():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "mcp" in extras, "`[mcp]` extra missing — R5-02 expects it landed"
    assert any(
        dep.split(">", 1)[0].split("<", 1)[0].split(";", 1)[0].strip() == "mcp"
        for dep in extras["mcp"]
    ), "[mcp] extra must include the `mcp` package itself"


def test_mcp_floor_at_least_1_0():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    deps = extras.get("mcp", [])
    mcp_pin = next(
        (d for d in deps if d.split(">", 1)[0].split("<", 1)[0].split(";", 1)[0].strip() == "mcp"),
        None,
    )
    assert mcp_pin is not None, "[mcp] missing mcp pin"
    assert ">=1." in mcp_pin or ">=2." in mcp_pin, f"[mcp] mcp floor must be >=1.0; got {mcp_pin!r}"


def test_mcp_upper_bound_caps_v2():
    """``mcp<2.0`` upper bound — protect against surprise v2 API breakage."""
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    deps = extras.get("mcp", [])
    mcp_pin = next(
        (d for d in deps if d.split(">", 1)[0].split("<", 1)[0].split(";", 1)[0].strip() == "mcp"),
        None,
    )
    assert mcp_pin is not None
    assert "<2." in mcp_pin or "<2,0" in mcp_pin or "<2.0" in mcp_pin, (
        f"[mcp] mcp must cap below 2.0 to bracket v1 API surface; got {mcp_pin!r}"
    )


def test_existing_extras_unchanged():
    """R5-02 must NOT regress the R3 (retrieval/eval) / R4 (rerank) extras."""
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    for required in ("retrieval", "eval", "rerank"):
        assert required in extras, f"regression: [{required}] extra missing"
