"""Phase CI-3 pyproject.toml metadata pins.

Locks the packaging metadata so a future merge that strips a classifier,
flips the license back to MIT, or drops the build-system is caught early.

Reference: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md` (CI-3).
User override: license is Apache-2.0 (not MIT).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def project_table(pyproject: dict) -> dict:
    return pyproject.get("project", {})


@pytest.fixture(scope="module")
def build_system(pyproject: dict) -> dict:
    return pyproject.get("build-system", {})


@pytest.fixture(scope="module")
def pytest_opts(pyproject: dict) -> dict:
    return pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})


# ── build-system ────────────────────────────────────────────────────────────


class TestBuildSystem:
    """Phase CI-3 introduces a real build-system block."""

    def test_build_system_block_exists(self, build_system: dict) -> None:
        assert build_system, "pyproject must declare [build-system] in CI-3"

    def test_build_backend_is_hatchling(self, build_system: dict) -> None:
        assert build_system.get("build-backend") == "hatchling.build", (
            f"Expected hatchling.build backend; got {build_system.get('build-backend')!r}"
        )

    def test_build_requires_hatchling(self, build_system: dict) -> None:
        requires = build_system.get("requires", [])
        assert any(str(r).startswith("hatchling") for r in requires), (
            f"Expected hatchling in build requires; got {requires!r}"
        )

    def test_build_requires_hatchling_min(self, build_system: dict) -> None:
        requires = build_system.get("requires", [])
        match = next((str(r) for r in requires if str(r).startswith("hatchling")), None)
        assert match is not None
        # Accept any of: hatchling>=1.25, hatchling>=1.25.0
        assert ">=1.25" in match, f"Expected hatchling>=1.25; got {match!r}"


# ── core project metadata ───────────────────────────────────────────────────


class TestProjectMetadata:
    """Locks down version, license, requires-python."""

    def test_name(self, project_table: dict) -> None:
        assert project_table.get("name") == "corpus-forge"

    def test_version_is_beta(self, project_table: dict) -> None:
        assert project_table.get("version") == "0.1.0b1", (
            f"Expected version 0.1.0b1 (PEP 440 beta marker); got {project_table.get('version')!r}"
        )

    def test_license_is_apache2_spdx(self, project_table: dict) -> None:
        """User override: Apache-2.0 SPDX, NOT MIT, NOT legacy {text=...}."""
        lic = project_table.get("license")
        assert lic == "Apache-2.0", (
            f"Expected license = 'Apache-2.0' (PEP 639 SPDX string); got {lic!r}. "
            f"This was overridden from the plan's MIT default."
        )

    def test_license_files(self, project_table: dict) -> None:
        files = project_table.get("license-files")
        assert files == ["LICENSE"], f"Expected license-files = ['LICENSE']; got {files!r}"

    def test_requires_python_has_upper_bound(self, project_table: dict) -> None:
        rp = project_table.get("requires-python", "")
        assert rp == ">=3.11,<3.14", (
            f"Expected requires-python = '>=3.11,<3.14' (honest upper bound); got {rp!r}"
        )

    def test_authors(self, project_table: dict) -> None:
        authors = project_table.get("authors", [])
        assert authors == [{"name": "Evan Owen", "email": "evan@jwo3.io"}], (
            f"Expected single Evan Owen author with evan@jwo3.io; got {authors!r}"
        )

    def test_description_present(self, project_table: dict) -> None:
        desc = project_table.get("description", "")
        assert isinstance(desc, str) and len(desc) >= 20, (
            f"Description must be a non-trivial string; got {desc!r}"
        )

    def test_readme(self, project_table: dict) -> None:
        assert project_table.get("readme") == "README.md"


# ── classifiers ─────────────────────────────────────────────────────────────


REQUIRED_CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Operating System :: Microsoft :: Windows",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Database",
    "Typing :: Typed",
]


class TestClassifiers:
    """Lock all 14 classifiers down."""

    def test_classifiers_present(self, project_table: dict) -> None:
        classifiers = project_table.get("classifiers", [])
        assert isinstance(classifiers, list)
        assert len(classifiers) >= len(REQUIRED_CLASSIFIERS), (
            f"Expected ≥{len(REQUIRED_CLASSIFIERS)} classifiers; got {len(classifiers)}"
        )

    @pytest.mark.parametrize("classifier", REQUIRED_CLASSIFIERS)
    def test_classifier(self, project_table: dict, classifier: str) -> None:
        classifiers = project_table.get("classifiers", [])
        assert classifier in classifiers, f"Classifier {classifier!r} missing; got {classifiers}"

    def test_license_classifier_is_apache(self, project_table: dict) -> None:
        """Belt-and-suspenders: no MIT classifier should ever land here."""
        classifiers = project_table.get("classifiers", [])
        for c in classifiers:
            assert "MIT License" not in c, f"Saw forbidden MIT License classifier: {c!r}"


# ── keywords ────────────────────────────────────────────────────────────────


REQUIRED_KEYWORDS = [
    "huggingface",
    "datasets",
    "fine-tuning",
    "training-corpus",
    "embeddings",
    "pgvector",
    "sqlite-vec",
    "obsidian",
    "claude-code",
    "rag",
    "personal-knowledge",
]


class TestKeywords:
    def test_keywords_complete(self, project_table: dict) -> None:
        keywords = project_table.get("keywords", [])
        assert isinstance(keywords, list)
        for kw in REQUIRED_KEYWORDS:
            assert kw in keywords, f"Keyword {kw!r} missing; got {keywords}"

    def test_keywords_count(self, project_table: dict) -> None:
        keywords = project_table.get("keywords", [])
        assert len(keywords) == len(REQUIRED_KEYWORDS), (
            f"Expected exactly {len(REQUIRED_KEYWORDS)} keywords; got {len(keywords)}"
        )


# ── project.urls ────────────────────────────────────────────────────────────


REQUIRED_URLS = {
    "Homepage": "https://github.com/ulmentflam/corpus-forge",
    "Documentation": "https://github.com/ulmentflam/corpus-forge#readme",
    "Repository": "https://github.com/ulmentflam/corpus-forge",
    "Issues": "https://github.com/ulmentflam/corpus-forge/issues",
    "Changelog": "https://github.com/ulmentflam/corpus-forge/blob/main/CHANGELOG.md",
}


class TestProjectURLs:
    @pytest.mark.parametrize(("key", "value"), list(REQUIRED_URLS.items()))
    def test_url(self, project_table: dict, key: str, value: str) -> None:
        urls = project_table.get("urls", {})
        assert urls.get(key) == value, f"Expected urls[{key!r}] == {value!r}; got {urls.get(key)!r}"


# ── pytest options: pythonpath hack removed ─────────────────────────────────


class TestPythonPathRemoved:
    """The CI-3 plan: editable install replaces `pythonpath = ['.']`."""

    def test_pythonpath_absent(self, pytest_opts: dict) -> None:
        assert "pythonpath" not in pytest_opts, (
            f"`pythonpath` must be removed in CI-3 once editable install works; "
            f"got pythonpath = {pytest_opts.get('pythonpath')!r}"
        )

    def test_addopts_still_intact(self, pytest_opts: dict) -> None:
        """Don't lose CI-1 / CI-2 wiring while ripping out pythonpath."""
        addopts = pytest_opts.get("addopts", "")
        assert "--strict-markers" in addopts
        assert "--strict-config" in addopts
        assert "--timeout=60" in addopts

    def test_existing_markers_preserved(self, pytest_opts: dict) -> None:
        markers = pytest_opts.get("markers", [])
        names = {str(m).split(":")[0].strip() for m in markers}
        for required in ("integration", "smoke", "fuzz", "requires_unix", "requires_docker"):
            assert required in names, f"Existing marker '{required}' missing; got: {names}"


# ── scripts entry point preserved ───────────────────────────────────────────


class TestScripts:
    def test_corpus_forge_entry_point(self, project_table: dict) -> None:
        scripts = project_table.get("scripts", {})
        assert scripts.get("corpus-forge") == "corpus_forge.cli:app"
