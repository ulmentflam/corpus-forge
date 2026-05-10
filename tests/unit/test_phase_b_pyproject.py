"""Tests that pyproject.toml carries the B-01 requirements — Phase B pin.

Reads pyproject.toml with stdlib tomllib and asserts:
- [project.optional-dependencies] has a 'sqlite' key.
- The 'sqlite' list contains an entry that pins sqlite-vec >= 0.1.

This test will be RED until the coder adds the extra to pyproject.toml.
"""

import re
import tomllib
from pathlib import Path

import pytest

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    """Load pyproject.toml once per module."""
    with PYPROJECT_PATH.open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def optional_deps(pyproject: dict) -> dict:
    """Extract [project.optional-dependencies]."""
    project = pyproject.get("project", {})
    return project.get("optional-dependencies", {})


# ---------------------------------------------------------------------------
# Existence of pyproject.toml
# ---------------------------------------------------------------------------


class TestPyprojectExists:
    """Guard that the file is readable before deeper assertions."""

    def test_pyproject_exists(self):
        """pyproject.toml must exist at the repo root."""
        assert PYPROJECT_PATH.exists(), f"pyproject.toml not found at {PYPROJECT_PATH}"

    def test_pyproject_parseable(self, pyproject):
        """pyproject.toml must be valid TOML (fixture load would fail otherwise)."""
        assert isinstance(pyproject, dict)


# ---------------------------------------------------------------------------
# [project.optional-dependencies] shape
# ---------------------------------------------------------------------------


class TestOptionalDependencies:
    """Validate the optional-deps table structure."""

    def test_optional_dependencies_table_exists(self, pyproject):
        """[project.optional-dependencies] table must be present."""
        project = pyproject.get("project", {})
        assert "optional-dependencies" in project, (
            "pyproject.toml is missing [project.optional-dependencies]"
        )

    def test_existing_extras_preserved(self, optional_deps):
        """Existing extras (openai, hf, tokens) must still be present after B-01 edit."""
        for expected in ("openai", "hf", "tokens"):
            assert expected in optional_deps, (
                f"Expected existing extra '{expected}' in [project.optional-dependencies]; "
                f"found keys: {list(optional_deps)}"
            )


# ---------------------------------------------------------------------------
# 'sqlite' extra presence and content
# ---------------------------------------------------------------------------


class TestSqliteExtra:
    """The 'sqlite' optional-dependency group must exist and pin sqlite-vec."""

    def test_sqlite_extra_key_exists(self, optional_deps):
        """[project.optional-dependencies] must have a 'sqlite' key."""
        assert "sqlite" in optional_deps, (
            f"'sqlite' extra missing from [project.optional-dependencies]. "
            f"Found extras: {list(optional_deps)}"
        )

    def test_sqlite_extra_is_a_list(self, optional_deps):
        """The 'sqlite' extra must be a list of requirement strings."""
        sqlite_deps = optional_deps.get("sqlite", None)
        assert isinstance(sqlite_deps, list), (
            f"Expected 'sqlite' extra to be a list, got {type(sqlite_deps)}"
        )

    def test_sqlite_extra_is_nonempty(self, optional_deps):
        """The 'sqlite' extra list must have at least one entry."""
        sqlite_deps = optional_deps.get("sqlite", [])
        assert len(sqlite_deps) >= 1, "The 'sqlite' extra list is empty"

    def test_sqlite_extra_contains_sqlite_vec(self, optional_deps):
        """At least one entry in the 'sqlite' extra must reference 'sqlite-vec'."""
        sqlite_deps = optional_deps.get("sqlite", [])
        names = [str(dep) for dep in sqlite_deps]
        matched = [d for d in names if re.search(r"sqlite[-_]vec", d, re.IGNORECASE)]
        assert matched, f"No 'sqlite-vec' requirement found in sqlite extra deps: {names}"

    def test_sqlite_vec_version_pin_gte_0_1(self, optional_deps):
        """The sqlite-vec entry must carry a '>=0.1' version constraint."""
        sqlite_deps = optional_deps.get("sqlite", [])
        matched = [
            d
            for d in (str(dep) for dep in sqlite_deps)
            if re.search(r"sqlite[-_]vec", d, re.IGNORECASE)
        ]
        assert matched, "sqlite-vec entry not found (checked in prior test)"
        dep_str = matched[0]
        # Require >=0.1 (or a higher lower-bound that satisfies the constraint)
        assert re.search(r">=\s*0\.1", dep_str), (
            f"sqlite-vec entry '{dep_str}' does not pin '>=0.1'. "
            "Expected something like: sqlite-vec>=0.1"
        )

    def test_sqlite_vec_entry_is_valid_pep508(self, optional_deps):
        """The sqlite-vec entry must match the accepted PEP 508 package-name pattern."""
        sqlite_deps = optional_deps.get("sqlite", [])
        matched = [
            d
            for d in (str(dep) for dep in sqlite_deps)
            if re.search(r"sqlite[-_]vec", d, re.IGNORECASE)
        ]
        assert matched, "sqlite-vec entry not found"
        dep_str = matched[0].strip()
        # PEP 508: package name, optional extras, optional version specifier
        pep508_pattern = (
            r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(\[.*\])?\s*(>=|==|~=|!=|<=|<|>)\s*[\d.]+"
        )
        assert re.match(pep508_pattern, dep_str), (
            f"sqlite-vec entry '{dep_str}' does not look like a valid PEP 508 requirement"
        )
