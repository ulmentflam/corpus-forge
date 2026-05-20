"""O1-T3 — pyproject.toml must expose an `analyze` optional-dependency extra.

The `[analyze]` extra brings the EDA + corpus-cleaning ML stack used by
Phase O (Wave O1+).  All deps in this extra must be opt-in (NOT in core
`[project.dependencies]`) so that a plain `pip install corpus-forge`
keeps its install footprint light.

Expected extra (after O1-G1 GREEN):

    analyze = [
      "scikit-learn>=1.4",
      "hdbscan>=0.8",
      "umap-learn>=0.5",
      "bertopic>=0.16",
      "datasketch>=1.6",
      "fasttext-langdetect>=1.0",
      "langdetect>=1.0",
    ]

RED invariant: the `analyze` key is absent from `pyproject.toml` until
O1-G1 (GREEN) lands.  Every test in this file therefore MUST fail with a
KeyError or AssertionError until O1-G1 is committed.
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # Python 3.11+  (project requires-python = ">=3.11")
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Canonical package names the `analyze` extra MUST contain.
# Verified against Wave O1 spec in `.planning/tdd/phase_o_eda_cleaning.md`
# § Wave O1 GREEN and the task acceptance in `tasks.md` § O1-T3.
# Both fasttext-langdetect (primary) AND langdetect (fallback) must be present.
_REQUIRED_PACKAGES: list[str] = [
    "scikit-learn",
    "hdbscan",
    "umap-learn",
    "bertopic",
    "datasketch",
    "fasttext-langdetect",
    "langdetect",
]

# Packages that must NOT appear in `[project.dependencies]` (core install).
# These are the same seven — they are opt-in via the extra, not core deps.
_MUST_NOT_BE_CORE = _REQUIRED_PACKAGES


def _load_pyproject() -> dict:
    """Parse the repo-root pyproject.toml with tomllib."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _get_extras(pyproject: dict) -> dict[str, list[str]]:
    return pyproject.get("project", {}).get("optional-dependencies", {})


def _get_core_deps(pyproject: dict) -> list[str]:
    return pyproject.get("project", {}).get("dependencies", [])


def _dep_contains_name(dep_string: str, package_name: str) -> bool:
    """Return True when *dep_string* refers to *package_name*.

    Case-insensitive.  Normalises hyphens and underscores (PEP 508 treats
    them interchangeably for distribution names).
    """
    # Normalise both sides: lower-case, replace hyphens with underscores.
    norm_dep = dep_string.lower().replace("-", "_")
    norm_pkg = package_name.lower().replace("-", "_")
    # A dep string like "scikit_learn>=1.4" starts with the normalised name.
    return norm_dep.startswith(norm_pkg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_analyze_extra_exists():
    """The `analyze` key must be present in [project.optional-dependencies]."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    assert "analyze" in extras, (
        "`[project.optional-dependencies].analyze` is missing from pyproject.toml. "
        "O1-G1 (GREEN) must add this extra."
    )


def test_analyze_extra_is_a_list():
    """The value of `analyze` must be a non-empty list."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]  # KeyError → acceptable RED
    assert isinstance(deps, list), f"analyze extra must be a list, got {type(deps)}"
    assert len(deps) > 0, "analyze extra must not be empty"


def test_scikit_learn_in_analyze():
    """scikit-learn must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]  # KeyError → acceptable RED
    assert any(_dep_contains_name(d, "scikit-learn") for d in deps), (
        f"scikit-learn not found in [analyze] extra. Current deps: {deps}"
    )


def test_hdbscan_in_analyze():
    """hdbscan must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert any(_dep_contains_name(d, "hdbscan") for d in deps), (
        f"hdbscan not found in [analyze] extra. Current deps: {deps}"
    )


def test_umap_learn_in_analyze():
    """umap-learn must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert any(_dep_contains_name(d, "umap-learn") for d in deps), (
        f"umap-learn not found in [analyze] extra. Current deps: {deps}"
    )


def test_bertopic_in_analyze():
    """bertopic must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert any(_dep_contains_name(d, "bertopic") for d in deps), (
        f"bertopic not found in [analyze] extra. Current deps: {deps}"
    )


def test_datasketch_in_analyze():
    """datasketch must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert any(_dep_contains_name(d, "datasketch") for d in deps), (
        f"datasketch not found in [analyze] extra. Current deps: {deps}"
    )


def test_fasttext_langdetect_in_analyze():
    """fasttext-langdetect (primary language detector) must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert any(_dep_contains_name(d, "fasttext-langdetect") for d in deps), (
        "fasttext-langdetect not found in [analyze] extra. "
        "Both fasttext-langdetect (primary) and langdetect (fallback) are required. "
        f"Current deps: {deps}"
    )


def test_langdetect_in_analyze():
    """langdetect (fallback language detector) must be listed in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert any(
        _dep_contains_name(d, "langdetect") and not _dep_contains_name(d, "fasttext-langdetect")
        for d in deps
    ), (
        "langdetect (standalone fallback) not found in [analyze] extra. "
        "fasttext-langdetect alone is insufficient — the fallback package must also be present. "
        f"Current deps: {deps}"
    )


def test_analyze_extra_has_exactly_seven_entries():
    """The analyze extra must contain exactly the seven specified packages.

    This test is a drift gate: if someone adds or removes a package without
    updating this test, the suite fails and forces a deliberate review.

    Expected seven: scikit-learn, hdbscan, umap-learn, bertopic, datasketch,
    fasttext-langdetect, langdetect.
    """
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    assert len(deps) == 7, (
        f"[analyze] extra must have exactly 7 entries (got {len(deps)}). "
        f"Current deps: {deps}. "
        "Update this test AND the phase doc if the package list legitimately changed."
    )


def test_all_required_packages_present_in_analyze():
    """Bulk check: every required package name appears in the analyze extra."""
    pp = _load_pyproject()
    extras = _get_extras(pp)
    deps = extras["analyze"]
    missing = [
        pkg for pkg in _REQUIRED_PACKAGES if not any(_dep_contains_name(d, pkg) for d in deps)
    ]
    assert not missing, (
        f"The following packages are missing from [analyze] extra: {missing}. Current deps: {deps}"
    )


def test_analyze_packages_not_in_core_dependencies():
    """None of the analyze packages must appear in [project.dependencies].

    They are opt-in extras — adding them to core would impose heavy ML deps
    on every `pip install corpus-forge` user.
    """
    pp = _load_pyproject()
    core_deps = _get_core_deps(pp)
    in_core = [
        pkg for pkg in _MUST_NOT_BE_CORE if any(_dep_contains_name(d, pkg) for d in core_deps)
    ]
    assert not in_core, (
        f"The following packages MUST NOT be in core [project.dependencies]: {in_core}. "
        "They are Phase O opt-in ML deps and belong exclusively in [analyze]."
    )
