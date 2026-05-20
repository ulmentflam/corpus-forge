"""corpus_forge.analyze — EDA and corpus-cleaning utilities (Phase O).

This package surfaces four MCP tools (landing in Wave O4):

- ``analyze_corpus``   — quality signal sweep across a dataset.
- ``find_duplicates``  — near-duplicate detection via MinHash LSH (datasketch).
- ``cluster_topics``   — HDBSCAN/BERTopic topic clustering.
- ``score_quality``    — per-chunk quality scoring via an LLM judge.

Lazy-import contract
--------------------
Heavy deps (scikit-learn, hdbscan, umap-learn, bertopic, datasketch,
fasttext-langdetect, langdetect) are imported inside function bodies, not
at module top level.  This keeps ``corpus-forge --help`` cold-start budget
unaffected on a plain ``pip install corpus-forge`` with no extras.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O1.
"""

from __future__ import annotations

from corpus_forge.analyze.stats import (
    compute_length_distribution,
    compute_token_stats,
)

__all__ = [
    "compute_length_distribution",
    "compute_token_stats",
]
