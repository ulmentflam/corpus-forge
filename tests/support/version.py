"""Canonical project version for packaging tests (RFC version-SSOT).

The single source of truth is ``corpus_forge.__init__.__version__``; the
build derives the wheel ``Version:`` from it via ``[tool.hatch.version]``.
Packaging tests import ``CANONICAL_VERSION`` from here so "the version" has
exactly one definition in test-land too — a release bump touches only
``corpus_forge/__init__.py`` and every test follows it automatically.
"""

from __future__ import annotations

import re

from corpus_forge import __version__ as CANONICAL_VERSION

# PEP 440 beta marker this project ships: 0.1.0b17, 1.2.3b4, … A bare regex
# (no ``packaging`` dependency) keeps the packaging tests dependency-light.
_BETA_RE = re.compile(r"^\d+\.\d+\.\d+b\d+$")


def is_beta_version(v: str) -> bool:
    """True iff ``v`` is an ``X.Y.Zb<N>`` PEP 440 beta string."""
    return bool(_BETA_RE.match(v))


__all__ = ["CANONICAL_VERSION", "is_beta_version"]
