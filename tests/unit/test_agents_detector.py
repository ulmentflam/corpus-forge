"""Unit tests for ``corpus_forge.agents.detector`` — T1 of the
``feat/corpus-agents-init`` planning board.

Verifies :func:`detect_project_context` shape against three fixture
trees: Python-only, Rust-only, mixed Python+TypeScript. The detector
is a non-recursive top-level scan + a one-level descent into ``src/``
and ``tests/`` — by design it never walks the whole tree.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from corpus_forge.agents.detector import ProjectContext, detect_project_context

# ─────────────────────────────────────────────────────────────────────────
# Fixtures — synthetic project trees
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    """Minimal Python project with pyproject.toml, tests/, LICENSE, README."""

    root = tmp_path / "py-proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        dedent(
            """\
            [project]
            name = "py-proj"
            version = "0.1.0"

            [tool.pytest.ini_options]
            testpaths = ["tests"]
            """
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text("# py-proj\n", encoding="utf-8")
    (root / "LICENSE").write_text(
        dedent(
            """\
            MIT License

            Copyright (c) 2025 Example

            Permission is hereby granted, free of charge, to any person obtaining a copy
            of this software and associated documentation files (the "Software"), to deal
            in the Software without restriction, including without limitation the rights
            to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
            """
        ),
        encoding="utf-8",
    )
    src = root / "src" / "py_proj"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text("def hello() -> str:\n    return 'hi'\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text("def test_hello():\n    assert True\n", encoding="utf-8")
    return root


@pytest.fixture
def rust_project(tmp_path: Path) -> Path:
    """Minimal Rust project with Cargo.toml + src/main.rs."""

    root = tmp_path / "rs-proj"
    root.mkdir()
    (root / "Cargo.toml").write_text(
        dedent(
            """\
            [package]
            name = "rs-proj"
            version = "0.1.0"
            edition = "2021"
            license = "Apache-2.0"
            """
        ),
        encoding="utf-8",
    )
    (root / "LICENSE").write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "main.rs").write_text('fn main() {\n    println!("hi");\n}\n', encoding="utf-8")
    return root


@pytest.fixture
def mixed_project(tmp_path: Path) -> Path:
    """Project with both Python and TypeScript code."""

    root = tmp_path / "mixed-proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "mixed"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "package.json").write_text('{"name": "mixed", "version": "0.1.0"}\n', encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (src / "main.ts").write_text("console.log('hi');\n", encoding="utf-8")
    (src / "util.ts").write_text("export const X = 1;\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    return root


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────


def test_python_project_detection(python_project: Path) -> None:
    """Python project: counts py files, pyproject build tool, pytest framework, MIT license."""

    ctx = detect_project_context(python_project)

    assert isinstance(ctx, ProjectContext)
    assert ctx.languages.get("python", 0) >= 1
    assert (
        "pyproject" in ctx.package_managers
        or "uv" in ctx.package_managers
        or "pip" in ctx.package_managers
    )
    assert ctx.test_framework == "pytest"
    assert ctx.build_tool in {"pyproject", "uv", "hatch", "setuptools"}
    assert ctx.existing_readme is not None
    assert ctx.existing_readme.name == "README.md"
    assert ctx.existing_agents_md is None
    assert ctx.existing_claude_md is None
    assert ctx.license is not None and "MIT" in ctx.license
    assert ctx.license_header_sample is not None
    assert "MIT" in ctx.license_header_sample


def test_rust_project_detection(rust_project: Path) -> None:
    """Rust project: counts rs files, detects cargo + Apache license."""

    ctx = detect_project_context(rust_project)

    assert ctx.languages.get("rust", 0) >= 1
    assert "cargo" in ctx.package_managers
    assert ctx.build_tool == "cargo"
    # No tests/ dir + no pytest config → no test framework
    assert ctx.test_framework is None
    assert ctx.license is not None
    assert "Apache" in ctx.license


def test_mixed_project_detection(mixed_project: Path) -> None:
    """Mixed Py+TS: both languages appear, both package managers detected."""

    ctx = detect_project_context(mixed_project)

    assert ctx.languages.get("python", 0) >= 1
    assert ctx.languages.get("typescript", 0) >= 1
    # Both ecosystems should be detected
    pkg = set(ctx.package_managers)
    assert any(p in pkg for p in ("pyproject", "uv", "pip"))
    assert any(p in pkg for p in ("npm", "pnpm", "bun", "yarn"))
    # Tests dir present with python tests → pytest
    assert ctx.test_framework == "pytest"


def test_detects_existing_agents_md(python_project: Path) -> None:
    """When AGENTS.md / CLAUDE.md exist at the root, they are referenced."""

    agents = python_project / "AGENTS.md"
    agents.write_text("# Agents\n", encoding="utf-8")
    claude = python_project / "CLAUDE.md"
    claude.write_text("See AGENTS.md\n", encoding="utf-8")

    ctx = detect_project_context(python_project)
    assert ctx.existing_agents_md == agents
    assert ctx.existing_claude_md == claude


def test_empty_project_returns_safe_defaults(tmp_path: Path) -> None:
    """An empty directory yields a ProjectContext with empty/None fields, not an exception."""

    empty = tmp_path / "empty"
    empty.mkdir()

    ctx = detect_project_context(empty)
    assert ctx.languages == {} or sum(ctx.languages.values()) == 0
    assert ctx.package_managers == []
    assert ctx.test_framework is None
    assert ctx.existing_readme is None
    assert ctx.license is None
    assert ctx.license_header_sample is None
