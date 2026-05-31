"""Pin the ``[llama-cpp]`` optional-dependencies entry in pyproject.toml.

The extra installs ``llama-cpp-python``, which the
:class:`LlamaCppEmbedder` lazy-imports as ``llama_cpp``. CI must NOT
require the extra to be installed (the in-process embedder is opt-in
for the qwen3-embedding NaN-from-Ollama workaround), so this test
validates ONLY that the extra is declared — not that the package is
installed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def _load_pyproject() -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    with (repo_root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_llama_cpp_extra_declared() -> None:
    """``[project.optional-dependencies] llama-cpp`` must exist."""
    cfg = _load_pyproject()
    extras = cfg["project"]["optional-dependencies"]
    assert "llama-cpp" in extras, (
        f"Missing optional-dependencies['llama-cpp']; declared: {sorted(extras.keys())!r}"
    )


def test_llama_cpp_extra_pulls_llama_cpp_python() -> None:
    """The extra must install ``llama-cpp-python``."""
    cfg = _load_pyproject()
    entries = cfg["project"]["optional-dependencies"]["llama-cpp"]
    assert any("llama-cpp-python" in e.lower() for e in entries), (
        f"[llama-cpp] extra must declare llama-cpp-python; got: {entries!r}"
    )
