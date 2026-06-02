"""Local-pattern sampler — T2.

Pure file scan over representative source + test files. Pattern
extraction is regex-based; no third-party parsers, no shell-outs to
git or external tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.agents.detector import ProjectContext


# ─────────────────────────────────────────────────────────────────────────
# Public dataclass
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LocalPatterns:
    """Summary of local code conventions extracted via regex sampling."""

    import_style: str = ""
    docstring_style: str = ""
    error_handling_examples: list[str] = field(default_factory=list)
    type_hint_density: float = 0.0
    test_naming_pattern: str | None = None
    notable_comments: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Configuration knobs
# ─────────────────────────────────────────────────────────────────────────


_MAX_FILES_PER_DIR = 8
_MAX_TOTAL_FILES = 24
_MAX_ERROR_HANDLING_EXAMPLES = 5
_MAX_NOTABLE_COMMENTS = 10
_NOTABLE_COMMENT_RX = re.compile(r"#\s*(?:NOTE|TODO|FIXME|XXX|HACK)\b[:\s].*", re.IGNORECASE)
_PY_DEF_RX = re.compile(
    r"^(\s*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(->\s*[^:]+)?:", re.MULTILINE
)
_PY_FROM_IMPORT_RX = re.compile(r"^from\s+\S+\s+import\s+", re.MULTILINE)
_PY_IMPORT_RX = re.compile(r"^import\s+\S+", re.MULTILINE)
_PY_RAISE_RX = re.compile(r"^\s*raise\s+[A-Z][A-Za-z0-9_]*(?:\([^)]*\))?", re.MULTILINE)
_TEST_FN_RX = re.compile(r"^def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)


# ─────────────────────────────────────────────────────────────────────────
# Sampling helpers
# ─────────────────────────────────────────────────────────────────────────


def _representative_files(root: Path, context: ProjectContext) -> list[Path]:
    """Pick a small, representative sample of project files.

    Strategy:
    - Top-level files with a known language extension.
    - Up to ``_MAX_FILES_PER_DIR`` from ``src/`` (recursively, one level
      deep at first; expand if we haven't filled the quota).
    - Up to ``_MAX_FILES_PER_DIR`` from ``tests/``.
    - Cap the total at ``_MAX_TOTAL_FILES``.

    ``context`` is retained for signature symmetry / future per-language
    quotas; intentionally unused today.
    """

    _ = context

    from corpus_forge.agents.detector import _LANG_BY_EXT  # noqa: PLC0415

    if not root.is_dir():
        return []

    picked: list[Path] = []
    skip_dirs = {".git", ".venv", "venv", "node_modules", "target", "dist", "build", "__pycache__"}

    def add_from(directory: Path, limit: int) -> None:
        count = 0
        if not directory.is_dir():
            return
        for path in sorted(directory.rglob("*")):
            if count >= limit:
                break
            if any(part in skip_dirs for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() in _LANG_BY_EXT:
                picked.append(path)
                count += 1

    # Top-level files first
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _LANG_BY_EXT:
            picked.append(entry)

    add_from(root / "src", _MAX_FILES_PER_DIR)
    add_from(root / "tests", _MAX_FILES_PER_DIR)
    add_from(root / "lib", _MAX_FILES_PER_DIR // 2)

    return picked[:_MAX_TOTAL_FILES]


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _import_style(py_files: list[str]) -> str:
    """Compare ``from X import Y`` vs ``import X`` ratio."""

    if not py_files:
        return ""
    from_count = 0
    import_count = 0
    for text in py_files:
        from_count += len(_PY_FROM_IMPORT_RX.findall(text))
        import_count += len(_PY_IMPORT_RX.findall(text))
    if from_count == 0 and import_count == 0:
        return ""
    if from_count >= import_count:
        return (
            f"primarily `from X import Y` ({from_count} from-imports "
            f"vs {import_count} bare imports)"
        )
    return f"primarily bare `import X` ({import_count} bare imports vs {from_count} from-imports)"


def _docstring_style(py_files: list[str]) -> str:
    """Heuristic: pick the dominant docstring shape among module/function docstrings.

    Returns one of ``google``, ``numpy``, ``rest``, ``plain``, or ``""``.
    """

    if not py_files:
        return ""
    joined = "\n".join(py_files)
    has_google = bool(re.search(r"\n\s+(?:Args|Returns|Raises|Yields):\s*\n", joined))
    has_numpy = bool(re.search(r"\n\s+(?:Parameters|Returns|Raises)\s*\n\s+-{3,}", joined))
    has_rest = bool(re.search(r":(?:param|returns?|raises?)\s+[A-Za-z_]+:", joined))
    has_any_docstring = '"""' in joined or "'''" in joined
    if has_google:
        return "google"
    if has_numpy:
        return "numpy"
    if has_rest:
        return "rest"
    if has_any_docstring:
        return "plain"
    return ""


def _error_handling_examples(py_files: list[str]) -> list[str]:
    """Extract up to 5 raise/raise-from snippets verbatim."""

    examples: list[str] = []
    for text in py_files:
        for match in _PY_RAISE_RX.finditer(text):
            snippet = match.group(0).strip()
            if snippet and snippet not in examples:
                examples.append(snippet)
            if len(examples) >= _MAX_ERROR_HANDLING_EXAMPLES:
                return examples
    return examples


def _type_hint_density(py_files: list[str]) -> float:
    """Fraction of ``def`` declarations whose signature has at least one
    annotation (parameter colon or ``-> ret``)."""

    total = 0
    typed = 0
    for text in py_files:
        for match in _PY_DEF_RX.finditer(text):
            params = match.group(3) or ""
            ret = match.group(4) or ""
            total += 1
            has_param_annot = ":" in params and not params.strip().startswith(",")
            if has_param_annot or ret.strip():
                typed += 1
    if total == 0:
        return 0.0
    return typed / total


def _test_naming_pattern(test_files: list[str]) -> str | None:
    """Return a short description of the dominant test-function naming pattern."""

    names: list[str] = []
    for text in test_files:
        for match in _TEST_FN_RX.finditer(text):
            names.append(match.group(1))
    if not names:
        return None
    # Sample first 3 names
    sample = ", ".join(names[:3])
    return f"`test_<snake_case>` (e.g. {sample})"


def _notable_comments(py_files: list[str]) -> list[str]:
    out: list[str] = []
    for text in py_files:
        for match in _NOTABLE_COMMENT_RX.finditer(text):
            line = match.group(0).strip()
            if line and line not in out:
                out.append(line)
            if len(out) >= _MAX_NOTABLE_COMMENTS:
                return out
    return out


# ─────────────────────────────────────────────────────────────────────────
# Public function
# ─────────────────────────────────────────────────────────────────────────


def sample_local_patterns(context: ProjectContext, root: Path) -> LocalPatterns:
    """Open representative files under ``root`` and extract patterns.

    Args:
        context: from :func:`corpus_forge.agents.detector.detect_project_context`.
        root: project root.
    """

    root = Path(root)
    files = _representative_files(root, context)

    py_texts: list[str] = []
    test_texts: list[str] = []
    for path in files:
        text = _safe_read(path)
        if not text:
            continue
        if path.suffix.lower() in {".py", ".pyi"}:
            py_texts.append(text)
            if "tests" in path.parts or path.name.startswith("test_"):
                test_texts.append(text)
        else:
            # Non-Python files contribute only to language-agnostic signals
            # (comments) — keep them out of the Python-only heuristics.
            pass

    return LocalPatterns(
        import_style=_import_style(py_texts),
        docstring_style=_docstring_style(py_texts),
        error_handling_examples=_error_handling_examples(py_texts),
        type_hint_density=_type_hint_density(py_texts),
        test_naming_pattern=_test_naming_pattern(test_texts),
        notable_comments=_notable_comments(py_texts),
    )
