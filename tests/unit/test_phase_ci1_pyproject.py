"""Phase CI-1 pyproject.toml pins.

Verifies that the CI-1 stability harness is wired into `pyproject.toml`:

- `[dependency-groups].dev` declares the new stability plugins.
- `[tool.pytest.ini_options].addopts` carries `--timeout=60` and
  `--timeout-method=thread`.
- `[tool.pytest.ini_options].xfail_strict` is enabled.
- `[tool.pytest.ini_options].markers` declares `requires_unix` and
  `requires_docker` (so `--strict-markers` accepts them without a
  programmatic registration roundtrip).
- `[tool.coverage.report].fail_under` is 85 (reconciled with the Makefile).

These tests will be RED until the coder lands the slice.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def dev_group(pyproject: dict) -> list[str]:
    groups = pyproject.get("dependency-groups", {})
    dev = groups.get("dev", [])
    assert isinstance(dev, list), "dependency-groups.dev must be a list"
    return [str(d) for d in dev]


@pytest.fixture(scope="module")
def pytest_opts(pyproject: dict) -> dict:
    tool = pyproject.get("tool", {})
    return tool.get("pytest", {}).get("ini_options", {})


@pytest.fixture(scope="module")
def coverage_report(pyproject: dict) -> dict:
    tool = pyproject.get("tool", {})
    return tool.get("coverage", {}).get("report", {})


# ── dev dependency group additions ───────────────────────────────────────────


class TestDevDepsCI1:
    """The CI-1 stability plugins must be declared in dependency-groups.dev."""

    @pytest.mark.parametrize(
        ("pkg", "min_version"),
        [
            ("pytest-timeout", "2.3"),
            ("pytest-randomly", "3.15"),
            ("pytest-xdist", "3.6"),
            ("pytest-rerunfailures", "14.0"),
            ("build", "1.2"),
        ],
    )
    def test_dev_dep_present(self, dev_group: list[str], pkg: str, min_version: str) -> None:
        pattern = re.compile(rf"^{re.escape(pkg)}\s*(>=|==|~=)\s*([\d.]+)")
        matches = [d for d in dev_group if pattern.match(d.split(";")[0].strip())]
        assert matches, (
            f"Expected dev dep '{pkg}>={min_version}' (or equiv); found dev group: {dev_group}"
        )
        # Lower bound must satisfy min_version (lexicographic on dotted parts ok for these specs).
        match = pattern.match(matches[0].split(";")[0].strip())
        assert match is not None
        found_ver = match.group(2)
        # Compare as tuples of ints for safety.
        wanted = tuple(int(x) for x in min_version.split("."))
        got = tuple(int(x) for x in found_ver.split("."))
        assert got >= wanted, f"{pkg}: lower bound {found_ver} is below required {min_version}"


# ── pytest ini_options additions ─────────────────────────────────────────────


class TestPytestIniOptionsCI1:
    """`[tool.pytest.ini_options]` must carry the timeout + strict-xfail wiring."""

    def test_addopts_present(self, pytest_opts: dict) -> None:
        addopts = pytest_opts.get("addopts", "")
        assert isinstance(addopts, str) and addopts, "addopts must be a non-empty string"

    def test_addopts_includes_timeout(self, pytest_opts: dict) -> None:
        addopts = pytest_opts.get("addopts", "")
        assert "--timeout=60" in addopts, f"Expected '--timeout=60' in addopts; got: {addopts!r}"

    def test_addopts_includes_timeout_method(self, pytest_opts: dict) -> None:
        addopts = pytest_opts.get("addopts", "")
        assert "--timeout-method=thread" in addopts, (
            f"Expected '--timeout-method=thread' in addopts; got: {addopts!r}"
        )

    def test_addopts_preserves_strict_markers(self, pytest_opts: dict) -> None:
        """Don't lose the existing --strict-markers / --strict-config from B-01."""
        addopts = pytest_opts.get("addopts", "")
        assert "--strict-markers" in addopts
        assert "--strict-config" in addopts

    def test_xfail_strict_enabled(self, pytest_opts: dict) -> None:
        assert pytest_opts.get("xfail_strict") is True, (
            f"Expected xfail_strict=True; got {pytest_opts.get('xfail_strict')!r}"
        )

    def test_markers_includes_requires_unix(self, pytest_opts: dict) -> None:
        markers = pytest_opts.get("markers", [])
        assert any(str(m).startswith("requires_unix") for m in markers), (
            f"Expected 'requires_unix' marker declared; got: {markers}"
        )

    def test_markers_includes_requires_docker(self, pytest_opts: dict) -> None:
        markers = pytest_opts.get("markers", [])
        assert any(str(m).startswith("requires_docker") for m in markers), (
            f"Expected 'requires_docker' marker declared in TOML; got: {markers}"
        )

    def test_existing_markers_preserved(self, pytest_opts: dict) -> None:
        """Don't drop the pre-existing markers (integration / smoke / fuzz)."""
        markers = pytest_opts.get("markers", [])
        names = {str(m).split(":")[0].strip() for m in markers}
        for required in ("integration", "smoke", "fuzz"):
            assert required in names, f"Existing marker '{required}' missing; got: {names}"


# ── coverage report threshold ────────────────────────────────────────────────


class TestCoverageGate:
    """Coverage gate must be reconciled to 85 between Makefile and pyproject."""

    def test_fail_under_is_85(self, coverage_report: dict) -> None:
        fail_under = coverage_report.get("fail_under")
        assert fail_under == 85, (
            f"Expected [tool.coverage.report].fail_under == 85; got {fail_under!r}"
        )
