"""Admin (CRUD) command surface for corpus-forge (Phase L Wave 7).

The ``admin`` package backs the ``config`` / ``embedder`` / ``ollama`` /
``dataset`` / ``source`` Typer sub-apps registered on the CLI root.  The
modules here are intentionally side-effect-free at import time — the CLI
wires them via ``app.add_typer(...)`` so importing
``corpus_forge.admin`` does not boot any subprocess, hit any network,
or open any backend.

Surface map:

- :mod:`.foreground` — ``run_attached`` wrapper that decides between
  attached / detached child execution + the pid-file helpers used by
  every long-op verb.
- :mod:`._path` — dotted-path resolver (``a.b[0].c``) + Pydantic-aware
  string-to-typed coercion for the ``config set`` verb.
- :mod:`.config` — Typer sub-app for ``corpus-forge config ...``.
- :mod:`.ollama` — Typer sub-app for ``corpus-forge ollama ...`` plus a
  thin HTTP client over Ollama's REST surface.
- :mod:`.embedder` — Typer sub-app for ``corpus-forge embedder ...``.
- :mod:`.dataset` / :mod:`.source` — Typer sub-apps for the dataset and
  per-dataset source CRUD.
"""

from __future__ import annotations

__all__: list[str] = []
