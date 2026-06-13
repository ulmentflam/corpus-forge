"""Phase CI-3 wheel build + METADATA inspection.

The strongest end-to-end gate. We actually build the wheel via
`python -m build --wheel` and assert that the resulting METADATA file
matches the metadata we declared in pyproject.toml. Catches drift between
pyproject and the wheel-on-disk reality (e.g. mis-mapped license, missing
classifier, wrong Requires-Python).

Marked slow/optional via `pytest.importorskip` because the build is
~30-60s. Skipped if `build` isn't on the path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest

from corpus_forge import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CLASSIFIERS = [
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

EXPECTED_KEYWORDS = [
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


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel once for the module and return its path.

    Skip if `build` module isn't importable or the wheel build fails — the
    rest of the suite stays runnable.
    """
    pytest.importorskip("build")
    out_dir = tmp_path_factory.mktemp("dist-test")
    env = dict(os.environ)
    # Avoid recursive `uv sync` side-effects during the in-process build.
    env.pop("VIRTUAL_ENV", None)
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir), str(REPO_ROOT)],
            check=True,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=300,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on host env
        pytest.skip(f"python -m build unavailable: {exc}")
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"python -m build failed:\nSTDOUT:\n{exc.stdout}\nSTDERR:\n{exc.stderr}")
    wheels = list(out_dir.glob("*.whl"))
    assert wheels, f"No wheel produced in {out_dir}"
    assert len(wheels) == 1, f"Expected exactly one wheel; got {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def metadata(built_wheel: Path) -> dict:
    """Parse METADATA out of the built wheel."""
    with zipfile.ZipFile(built_wheel) as zf:
        metadata_files = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        assert metadata_files, f"No METADATA in {built_wheel.name}"
        with zf.open(metadata_files[0]) as fh:
            raw = fh.read().decode("utf-8")
    msg = Parser().parsestr(raw)
    # Email-style headers, but multi-valued ones (Classifier, License-File,
    # Project-URL) need get_all.
    return {
        "raw": raw,
        "msg": msg,
        "Name": msg.get("Name"),
        "Version": msg.get("Version"),
        "Requires-Python": msg.get("Requires-Python"),
        "License": msg.get("License"),
        "License-Expression": msg.get("License-Expression"),
        "License-File": msg.get_all("License-File") or [],
        "Classifier": msg.get_all("Classifier") or [],
        "Project-URL": msg.get_all("Project-URL") or [],
        "Keywords": msg.get("Keywords"),
        "Author": msg.get("Author"),
        "Author-email": msg.get("Author-email"),
    }


# ── basic identity ──────────────────────────────────────────────────────────


class TestWheelIdentity:
    def test_wheel_filename(self, built_wheel: Path) -> None:
        # Either underscore or hyphen normalisation depending on hatchling.
        # Single source of truth: derive from corpus_forge.__version__
        # (rfc-version-single-source-of-truth) so a bump touches one literal.
        assert built_wheel.name.startswith(f"corpus_forge-{__version__}"), (
            f"Expected wheel name to start with corpus_forge-{__version__}; got {built_wheel.name}"
        )
        assert built_wheel.name.endswith("-py3-none-any.whl"), (
            f"Expected universal py3 wheel; got {built_wheel.name}"
        )

    def test_metadata_name(self, metadata: dict) -> None:
        assert metadata["Name"] == "corpus-forge"

    def test_metadata_version(self, metadata: dict) -> None:
        assert metadata["Version"] == __version__

    def test_requires_python(self, metadata: dict) -> None:
        # Hatchling re-canonicalises the spec; the two parts may swap order
        # (`<3.14,>=3.11` ↔ `>=3.11,<3.14`). Both are equivalent — assert on
        # the parts, not the literal pin order.
        rp = metadata["Requires-Python"] or ""
        assert ">=3.11" in rp, f"Got Requires-Python: {rp!r}"
        assert "<3.14" in rp, f"Got Requires-Python: {rp!r}"


# ── license expression ──────────────────────────────────────────────────────


class TestLicense:
    """Hatchling >=1.25 with PEP 639 emits License-Expression: Apache-2.0."""

    def test_license_apache(self, metadata: dict) -> None:
        # Accept either modern (License-Expression) or legacy (License) form
        # since hatchling versions emit different shapes; both must say Apache-2.0.
        expr = metadata["License-Expression"]
        legacy = metadata["License"]
        candidates = [v for v in (expr, legacy) if v]
        assert candidates, (
            f"Wheel METADATA has neither License-Expression nor License; raw:\n{metadata['raw']}"
        )
        assert any("Apache-2.0" in v for v in candidates), (
            f"Expected Apache-2.0 license in wheel METADATA; "
            f"License-Expression={expr!r}, License={legacy!r}"
        )

    def test_license_file_reference(self, metadata: dict) -> None:
        files = metadata["License-File"]
        assert any(f.endswith("LICENSE") or f == "LICENSE" for f in files), (
            f"Expected LICENSE in License-File entries; got {files!r}"
        )

    def test_no_mit_anywhere(self, metadata: dict) -> None:
        """Belt-and-suspenders: no MIT artifact should sneak in."""
        raw = metadata["raw"]
        # 'MIT' appears in unrelated phrases sometimes (e.g. "submit"), so use
        # surrounding boundaries.
        forbidden = ["MIT License", "License: MIT", "License-Expression: MIT"]
        for token in forbidden:
            assert token not in raw, f"Forbidden MIT token {token!r} in wheel METADATA"


# ── classifiers ─────────────────────────────────────────────────────────────


class TestClassifiers:
    @pytest.mark.parametrize("classifier", EXPECTED_CLASSIFIERS)
    def test_classifier(self, metadata: dict, classifier: str) -> None:
        assert classifier in metadata["Classifier"], (
            f"Classifier {classifier!r} missing from wheel METADATA; got {metadata['Classifier']}"
        )

    def test_classifier_count(self, metadata: dict) -> None:
        # Allow drift upward, but every required one must be present (above).
        assert len(metadata["Classifier"]) >= len(EXPECTED_CLASSIFIERS)


# ── keywords ────────────────────────────────────────────────────────────────


class TestKeywords:
    def test_keywords_complete(self, metadata: dict) -> None:
        kw_field = metadata["Keywords"] or ""
        # Keywords may be either comma- or space-separated depending on
        # hatchling version. Accept both by normalizing to a token set.
        tokens = {tok.strip() for tok in kw_field.replace(",", " ").split() if tok.strip()}
        for kw in EXPECTED_KEYWORDS:
            assert kw in tokens, (
                f"Keyword {kw!r} missing from wheel METADATA keywords field "
                f"({kw_field!r}); parsed tokens={tokens}"
            )


# ── project urls ────────────────────────────────────────────────────────────


_EXPECTED_PROJECT_URLS = {
    "Homepage": "https://github.com/ulmentflam/corpus-forge",
    "Documentation": "https://github.com/ulmentflam/corpus-forge#readme",
    "Repository": "https://github.com/ulmentflam/corpus-forge",
    "Issues": "https://github.com/ulmentflam/corpus-forge/issues",
    "Changelog": "https://github.com/ulmentflam/corpus-forge/blob/main/CHANGELOG.md",
}


class TestProjectURLs:
    @pytest.mark.parametrize(("label", "target"), list(_EXPECTED_PROJECT_URLS.items()))
    def test_url(self, metadata: dict, label: str, target: str) -> None:
        entries = metadata["Project-URL"]
        # Each entry is `Label, URL`.
        parsed = {}
        for line in entries:
            if "," in line:
                k, v = line.split(",", 1)
                parsed[k.strip()] = v.strip()
        assert parsed.get(label) == target, (
            f"Project-URL {label} mismatch: expected {target!r}, got {parsed.get(label)!r}"
        )


# ── author ──────────────────────────────────────────────────────────────────


class TestAuthor:
    def test_author_email(self, metadata: dict) -> None:
        # Hatchling may emit either Author-email: 'Evan Owen <evan@jwo3.io>'
        # or split Author / Author-email fields. Accept either.
        ae = metadata["Author-email"] or ""
        a = metadata["Author"] or ""
        combined = f"{ae} {a}"
        assert "evan@jwo3.io" in combined, (
            f"Expected evan@jwo3.io in Author/Author-email; got {combined!r}"
        )
        assert "Evan Owen" in combined, (
            f"Expected 'Evan Owen' in Author/Author-email; got {combined!r}"
        )


# ── pip install round-trip (optional, gated by env) ─────────────────────────


class TestPipInstall:
    """Optional: pip install into a throwaway venv and run --help.

    Skipped unless `CI3_FULL_INSTALL=1` because creating a venv +
    installing torch deps is slow. Coder runs it via QA gate.
    """

    @pytest.mark.skipif(
        os.environ.get("CI3_FULL_INSTALL") != "1",
        reason="Set CI3_FULL_INSTALL=1 to run wheel install round-trip",
    )
    def test_corpus_forge_help(self, built_wheel: Path, tmp_path: Path) -> None:
        venv_dir = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        bin_dir = "Scripts" if sys.platform.startswith("win") else "bin"
        py_name = "python.exe" if sys.platform.startswith("win") else "python"
        cli_name = "corpus-forge.exe" if sys.platform.startswith("win") else "corpus-forge"
        py = venv_dir / bin_dir / py_name
        subprocess.run([str(py), "-m", "pip", "install", str(built_wheel)], check=True)
        cli = venv_dir / bin_dir / cli_name
        out = subprocess.run([str(cli), "--help"], check=True, capture_output=True, text=True)
        assert out.returncode == 0
        # Don't pin exact text; just confirm Typer banner.
        assert "Usage" in out.stdout or "Usage" in out.stderr

    def test_helper_for_skipif_does_not_break_collection(self) -> None:
        # Sentinel test to ensure the class is collected even when CI3_FULL_INSTALL is unset.
        assert shutil.which(sys.executable) is not None
