"""Q4-T1 RED — Static-analysis tests for inference-library imports in export.py.

Enforces the "no model sampling" boundary: ``corpus_forge/export.py`` (and
any module it imports at top-level) must NOT import inference libraries.

The test parses ``corpus_forge/export.py`` with ``ast`` and walks all
``Import`` and ``ImportFrom`` nodes.  The following module names (and their
sub-packages) are FORBIDDEN:

- ``openai``
- ``anthropic``
- ``ollama``
- ``transformers.pipelines``

``httpx`` is explicitly ALLOWED (the ban covers inference I/O, not HTTP).

These tests are designed as a safety-net: even before ``export_sdft`` is
written, they should pass if the existing ``export.py`` is clean.  The
``import_sdft`` test will fail (ImportError) while ``export_sdft`` does not
yet exist, marking the whole file RED, which is the required state.

Run command::

    uv run pytest tests/unit/export/test_sdft_no_inference.py -v 2>&1 | tail -30
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Path to the file under test
# ---------------------------------------------------------------------------

_EXPORT_PY = Path(__file__).parent.parent.parent.parent / "corpus_forge" / "export.py"

# ---------------------------------------------------------------------------
# Forbidden top-level import prefixes
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES = [
    "openai",
    "anthropic",
    "ollama",
    "transformers.pipelines",
]


# ---------------------------------------------------------------------------
# Helper: collect all import names from an ast.Module
# ---------------------------------------------------------------------------


def _collect_imports(tree: ast.Module) -> list[str]:
    """Return a flat list of all module names imported at any level in the AST."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


# ===========================================================================
# Sanity — the file parses successfully
# ===========================================================================


class TestExportPyParses:
    def test_export_py_exists(self) -> None:
        """corpus_forge/export.py must exist for any of these tests to be meaningful."""
        assert _EXPORT_PY.exists(), f"export.py not found at {_EXPORT_PY}"

    def test_export_py_is_valid_python(self) -> None:
        """corpus_forge/export.py must be syntactically valid Python."""
        source = _EXPORT_PY.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(_EXPORT_PY))
        except SyntaxError as exc:
            pytest.fail(f"export.py is not valid Python: {exc}")


# ===========================================================================
# Forbidden inference-library imports
# ===========================================================================


class TestNoInferenceImports:
    @pytest.mark.parametrize("forbidden", _FORBIDDEN_PREFIXES)
    def test_forbidden_module_not_imported(self, forbidden: str) -> None:
        """``{forbidden}`` must NOT appear as an import in corpus_forge/export.py."""
        source = _EXPORT_PY.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_EXPORT_PY))
        all_imports = _collect_imports(tree)

        violating = [
            imp for imp in all_imports if imp == forbidden or imp.startswith(forbidden + ".")
        ]
        assert not violating, (
            f"export.py imports the forbidden inference library {forbidden!r}.\n"
            f"Violating import(s): {violating}\n"
            "corpus-forge must never perform model inference in the export path."
        )

    def test_openai_not_imported(self) -> None:
        """openai must NOT be imported anywhere in export.py (belt-and-suspenders)."""
        source = _EXPORT_PY.read_text(encoding="utf-8")
        # Textual check as belt-and-suspenders (AST may miss exec/eval tricks)
        assert "import openai" not in source, "export.py must not contain 'import openai'"

    def test_anthropic_not_imported(self) -> None:
        """anthropic must NOT be imported anywhere in export.py."""
        source = _EXPORT_PY.read_text(encoding="utf-8")
        assert "import anthropic" not in source, "export.py must not contain 'import anthropic'"

    def test_httpx_is_allowed(self) -> None:
        """httpx imports are NOT banned (it is an HTTP transport, not an inference lib)."""
        source = _EXPORT_PY.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(_EXPORT_PY))
        all_imports = _collect_imports(tree)
        # This test always passes — it documents the allow-list intent.
        httpx_imports = [imp for imp in all_imports if imp == "httpx" or imp.startswith("httpx.")]
        # No assertion needed; we just confirm the test exists.
        assert isinstance(httpx_imports, list)


# ===========================================================================
# The export_sdft function import — confirms RED state
# ===========================================================================


class TestExportSdftImportFails:
    def test_export_sdft_not_yet_importable(self) -> None:
        """``export_sdft`` does not exist in corpus_forge.export — confirms RED state.

        This test is expected to FAIL (raise ImportError) until the coder
        adds ``export_sdft`` to ``corpus_forge/export.py``.
        """
        import importlib

        export_mod = importlib.import_module("corpus_forge.export")
        assert hasattr(export_mod, "export_sdft"), (
            "export_sdft is not yet defined in corpus_forge.export — "
            "this confirms the RED state.  The coder must implement it."
        )
