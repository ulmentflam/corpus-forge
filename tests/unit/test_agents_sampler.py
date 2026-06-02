"""Unit tests for ``corpus_forge.agents.sampler`` — T2.

Pure file scan — pattern extraction uses regex / tokenize only; no
third-party parsers, no git shell-outs.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from corpus_forge.agents.detector import detect_project_context
from corpus_forge.agents.sampler import LocalPatterns, sample_local_patterns


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    """Python project with typed functions + docstrings + pytest tests."""

    root = tmp_path / "py-proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "py-proj"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    src = root / "src" / "py_proj"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        dedent(
            '''
            """Core module."""
            from __future__ import annotations

            import logging
            from pathlib import Path
            from typing import Any

            logger = logging.getLogger(__name__)


            def load(path: Path) -> str:
                """Read a file from disk.

                Args:
                    path: file to read.
                Returns:
                    file contents.
                """
                if not path.exists():
                    raise FileNotFoundError(path)
                return path.read_text(encoding="utf-8")


            def transform(value: Any) -> Any:
                """Identity for now."""
                return value
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        dedent(
            """
            from pathlib import Path

            from py_proj.core import load


            def test_load_reads_file(tmp_path):
                p = tmp_path / "x.txt"
                p.write_text("hi", encoding="utf-8")
                assert load(p) == "hi"


            def test_load_raises_on_missing(tmp_path):
                import pytest
                with pytest.raises(FileNotFoundError):
                    load(tmp_path / "no.txt")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return root


def test_returns_local_patterns_dataclass(python_project: Path) -> None:
    ctx = detect_project_context(python_project)
    patterns = sample_local_patterns(ctx, python_project)
    assert isinstance(patterns, LocalPatterns)


def test_detects_import_style_python(python_project: Path) -> None:
    ctx = detect_project_context(python_project)
    patterns = sample_local_patterns(ctx, python_project)
    # Python project should report something import-related.
    assert "import" in patterns.import_style.lower() or "from" in patterns.import_style.lower()


def test_docstring_style_detected(python_project: Path) -> None:
    ctx = detect_project_context(python_project)
    patterns = sample_local_patterns(ctx, python_project)
    # Google or numpy or plain — anything non-empty.
    assert patterns.docstring_style != ""


def test_error_handling_examples_non_empty(python_project: Path) -> None:
    ctx = detect_project_context(python_project)
    patterns = sample_local_patterns(ctx, python_project)
    assert isinstance(patterns.error_handling_examples, list)
    # At least one snippet referencing the FileNotFoundError raise we put in
    assert any(
        "raise" in s.lower() or "filenotfound" in s.lower()
        for s in patterns.error_handling_examples
    )


def test_type_hint_density_python_high(python_project: Path) -> None:
    """All functions in the fixture are typed → density should be near 1.0."""

    ctx = detect_project_context(python_project)
    patterns = sample_local_patterns(ctx, python_project)
    assert 0.0 <= patterns.type_hint_density <= 1.0
    assert patterns.type_hint_density >= 0.5


def test_test_naming_pattern_detected(python_project: Path) -> None:
    ctx = detect_project_context(python_project)
    patterns = sample_local_patterns(ctx, python_project)
    assert patterns.test_naming_pattern is not None
    assert "test_" in patterns.test_naming_pattern or "test" in patterns.test_naming_pattern


def test_empty_project_returns_safe_defaults(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    ctx = detect_project_context(empty)
    patterns = sample_local_patterns(ctx, empty)
    assert isinstance(patterns, LocalPatterns)
    # No files → no type-hint density measurable
    assert patterns.type_hint_density == 0.0
    assert patterns.test_naming_pattern is None
    assert patterns.error_handling_examples == []


def test_notable_comments_collected(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0.1"\n', encoding="utf-8"
    )
    src = root / "src"
    src.mkdir()
    (src / "m.py").write_text(
        dedent(
            """
            # NOTE: this is important
            # TODO: implement properly
            def f() -> int:
                return 1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    ctx = detect_project_context(root)
    patterns = sample_local_patterns(ctx, root)
    text = " ".join(patterns.notable_comments).lower()
    # Should pick up at least one of the marker comments
    assert "note" in text or "todo" in text
