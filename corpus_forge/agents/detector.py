"""Project-context detector — T1.

Non-recursive top-level scan + one-level descent into ``src/`` and
``tests/``. Pure filesystem reads; no third-party parsers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────
# Language / package-manager detection tables
# ─────────────────────────────────────────────────────────────────────────

# Extension → canonical language name. Lowercase only.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".swift": "swift",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
}


_PACKAGE_MANAGER_FILES: dict[str, str] = {
    "pyproject.toml": "pyproject",
    "uv.lock": "uv",
    "poetry.lock": "poetry",
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "package.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
    "Cargo.toml": "cargo",
    "go.mod": "go",
    "Gemfile": "bundler",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "pom.xml": "maven",
    "Package.swift": "swiftpm",
}


# ─────────────────────────────────────────────────────────────────────────
# Public dataclass
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectContext:
    """Snapshot of a project's surface features.

    Frozen so downstream consumers can pass it around without worrying
    about mutation; instantiated only by :func:`detect_project_context`.
    """

    languages: dict[str, int] = field(default_factory=dict)
    package_managers: list[str] = field(default_factory=list)
    test_framework: str | None = None
    build_tool: str | None = None
    existing_agents_md: Path | None = None
    existing_claude_md: Path | None = None
    existing_readme: Path | None = None
    license: str | None = None
    license_header_sample: str | None = None


# ─────────────────────────────────────────────────────────────────────────
# Detector
# ─────────────────────────────────────────────────────────────────────────


def _count_languages(paths: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in paths:
        if not p.is_file():
            continue
        lang = _LANG_BY_EXT.get(p.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def _iter_scan_paths(root: Path) -> list[Path]:
    """Top-level + one-level descent into ``src/`` and ``tests/``.

    Skips hidden entries (``.git``, ``.venv``) and ``node_modules`` /
    ``target`` / ``build`` style cruft so the count reflects the user's
    own code.
    """

    skip_dirs = {".git", ".venv", "venv", "node_modules", "target", "dist", "build", "__pycache__"}
    results: list[Path] = []
    if not root.is_dir():
        return results
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name.startswith(".") and name not in {".github"}:
            continue
        if name in skip_dirs:
            continue
        if entry.is_file():
            results.append(entry)
        elif entry.is_dir() and name in {"src", "tests", "lib", "app"}:
            for sub in sorted(entry.rglob("*")):
                if any(part in skip_dirs for part in sub.parts):
                    continue
                if sub.is_file():
                    results.append(sub)
    return results


def _detect_package_managers(root: Path) -> list[str]:
    found: list[str] = []
    for fname, pm in _PACKAGE_MANAGER_FILES.items():
        if (root / fname).exists() and pm not in found:
            found.append(pm)
    # Glob-style: ``requirements*.txt``
    if any(root.glob("requirements*.txt")) and "pip" not in found:
        found.append("pip")
    return found


def _detect_test_framework(root: Path) -> str | None:
    """Detect the dominant test runner.

    Order of preference (first hit wins):

    1. ``pytest.ini`` or ``[tool.pytest.ini_options]`` in pyproject.toml.
    2. A ``tests/`` directory containing one or more ``test_*.py`` files.
    3. ``jest.config.*`` / ``vitest.config.*``.
    4. ``Cargo.toml`` (cargo test is built-in, but only when there's no
       Python signal — Python+Rust dual repos prefer pytest).
    """

    if (root / "pytest.ini").exists():
        return "pytest"
    pyproj = root / "pyproject.toml"
    if pyproj.exists():
        try:
            text = pyproj.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        if "[tool.pytest" in text:
            return "pytest"
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        for candidate in tests_dir.iterdir():
            if (
                candidate.is_file()
                and candidate.name.startswith("test_")
                and candidate.suffix == ".py"
            ):
                return "pytest"
            if candidate.is_dir():
                # one-level nested
                for inner in candidate.iterdir():
                    if inner.is_file() and inner.name.startswith("test_") and inner.suffix == ".py":
                        return "pytest"
                    break
    if any(root.glob("jest.config.*")):
        return "jest"
    if any(root.glob("vitest.config.*")):
        return "vitest"
    return None


def _detect_build_tool(root: Path, package_managers: list[str]) -> str | None:
    """Pick the canonical build tool from the package-manager set.

    ``root`` is retained in the signature for symmetry with the other
    ``_detect_*`` helpers and for future heuristics (e.g. peeking at
    a build script). It is intentionally unused today.
    """

    _ = root
    if not package_managers:
        return None
    # Priority order: Cargo > pyproject > npm-like > others
    priority = [
        "cargo",
        "pyproject",
        "uv",
        "poetry",
        "npm",
        "pnpm",
        "bun",
        "yarn",
        "go",
        "maven",
        "gradle",
    ]
    for choice in priority:
        if choice in package_managers:
            return choice
    return package_managers[0]


def _detect_license(root: Path) -> tuple[str | None, str | None]:
    """Read ``LICENSE`` / ``LICENSE.md`` first ~20 lines.

    Returns ``(license_name, header_sample)``. ``license_name`` is a
    one-word identifier (``MIT``, ``Apache-2.0``, etc.) when we can
    recognise the body; falls back to the file's first non-empty line.
    """

    candidates = [
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "LICENCE",
        "LICENCE.md",
        "COPYING",
    ]
    for fname in candidates:
        path = root / fname
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        head = "\n".join(text.splitlines()[:20]).strip()
        if not head:
            continue
        lowered = head.lower()
        license_name: str | None = None
        if "mit license" in lowered:
            license_name = "MIT"
        elif "apache license" in lowered and "2.0" in lowered:
            license_name = "Apache-2.0"
        elif "apache license" in lowered:
            license_name = "Apache"
        elif "gnu general public license" in lowered:
            if "version 3" in lowered:
                license_name = "GPL-3.0"
            elif "version 2" in lowered:
                license_name = "GPL-2.0"
            else:
                license_name = "GPL"
        elif "bsd 3-clause" in lowered or "3-clause bsd" in lowered:
            license_name = "BSD-3-Clause"
        elif "bsd 2-clause" in lowered:
            license_name = "BSD-2-Clause"
        elif "mozilla public license" in lowered:
            license_name = "MPL-2.0"
        if license_name is None:
            # Fall back to the first non-empty line as the identifier
            for raw_line in head.splitlines():
                stripped = raw_line.strip()
                if stripped:
                    license_name = stripped[:80]
                    break
        return license_name, head
    return None, None


def _find_first_existing(root: Path, names: list[str]) -> Path | None:
    for name in names:
        path = root / name
        if path.exists() and path.is_file():
            return path
    return None


def detect_project_context(root: Path) -> ProjectContext:
    """Inspect ``root`` and return a :class:`ProjectContext`.

    Non-recursive top-level + one-level descent into ``src/`` /
    ``tests/`` / ``lib/`` / ``app/``. Skips ``.git`` / ``.venv`` /
    ``node_modules`` / ``target`` / etc. so the language count
    reflects the user's own code.
    """

    root = Path(root)

    scan_paths = _iter_scan_paths(root)
    languages = _count_languages(scan_paths)
    package_managers = _detect_package_managers(root)
    test_framework = _detect_test_framework(root)
    build_tool = _detect_build_tool(root, package_managers)
    license_name, license_sample = _detect_license(root)
    existing_agents_md = _find_first_existing(root, ["AGENTS.md", "AGENTS.MD"])
    existing_claude_md = _find_first_existing(root, ["CLAUDE.md", "CLAUDE.MD"])
    existing_readme = _find_first_existing(
        root, ["README.md", "README.rst", "README.txt", "README"]
    )

    return ProjectContext(
        languages=languages,
        package_managers=package_managers,
        test_framework=test_framework,
        build_tool=build_tool,
        existing_agents_md=existing_agents_md,
        existing_claude_md=existing_claude_md,
        existing_readme=existing_readme,
        license=license_name,
        license_header_sample=license_sample,
    )
