"""Phase Q Wave 1 — SDFT (Supervised Demo Fine-Tuning) package.

Provides the infrastructure for capturing teacher→student demonstration pairs
from corpus-forge's curation and retrieval workflows.

Public surface:
- :class:`SDFTSource` — enumeration of source signal types.
- :func:`record_demonstration` — low-level insert with dedup.
- :func:`_should_capture_curation` — predicate for the commit_curation hook.
"""

from __future__ import annotations

from corpus_forge.sdft.capture import _should_capture_curation, record_demonstration
from corpus_forge.sdft.sources import SDFTSource

__all__ = [
    "SDFTSource",
    "_should_capture_curation",
    "record_demonstration",
]
