"""Editable-install smoke test for Phase CI-3.

With `[build-system] requires = ['hatchling>=1.25']` in pyproject.toml,
`uv sync` installs `corpus_forge` in editable mode. If editable install
works, `import corpus_forge` resolves without the now-removed
`pythonpath = ['.']` hack.

Pinned-by-name so CI3 QA can run it via `pytest -k test_dummy_import_corpus_forge`.
"""

from __future__ import annotations


def test_dummy_import_corpus_forge() -> None:
    """Editable install gate."""
    import corpus_forge

    assert corpus_forge is not None
    # Sanity-check a known attribute so a broken stub install fails.
    assert hasattr(corpus_forge, "__version__"), "corpus_forge.__version__ missing"
