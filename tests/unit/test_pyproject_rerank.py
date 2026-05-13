"""R4-03 — pyproject.toml must expose the `rerank` optional extra.

R3 deliberately left `rerank` out (scope-guard).  R4 lands it pinning
`sentence-transformers>=3.0`.  The dep is already a HARD dep of
`corpus-forge` (see `[project] dependencies`), so the extra is currently
documentational + a future-split anchor.  Tests pin:

- `[project.optional-dependencies].rerank` exists and contains `sentence-transformers`.
- The pinned floor is `>=3.0`.
- R5's `mcp` extra is still NOT declared (scope-guard preserved).
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


def test_rerank_extra_present():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "rerank" in extras, "`[rerank]` extra missing — R4-03 expects it landed"
    assert any("sentence-transformers" in dep for dep in extras["rerank"]), (
        "[rerank] extra must include sentence-transformers"
    )


def test_rerank_sentence_transformers_floor_at_least_3():
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    deps = extras.get("rerank", [])
    st_pin = next((d for d in deps if "sentence-transformers" in d), None)
    assert st_pin is not None, "[rerank] missing sentence-transformers pin"
    assert ">=3." in st_pin or ">=4." in st_pin, (
        f"[rerank] sentence-transformers floor must be >=3.0; got {st_pin!r}"
    )


def test_mcp_extra_still_scoped_out_for_r5():
    """R5 owns the [mcp] extra.  If it shows up in R4, the scope leaked."""
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "mcp" not in extras, "R4 must NOT declare the [mcp] extra; that belongs to Phase R5."


def test_existing_extras_unchanged():
    """R4-03 must NOT regress the R3-shipped `retrieval` / `eval` extras."""
    pp = _load_pyproject()
    extras = pp.get("project", {}).get("optional-dependencies", {})
    assert "retrieval" in extras, "regression: [retrieval] extra missing"
    assert "eval" in extras, "regression: [eval] extra missing"
