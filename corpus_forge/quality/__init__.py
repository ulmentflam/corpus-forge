"""Chunk-quality scoring (RFC ``rfc-nlp-data-quality-signals``).

This package houses heuristic and (future) learned scorers that emit
per-chunk quality signals consumed by curation, pruning, and retrieval.
Distinct from :mod:`corpus_forge.enrichers` (which is the Phase H
code-enricher pipeline) so the two enrichment surfaces don't entangle.

Public surface (current):

- :class:`~corpus_forge.quality.heuristic.HeuristicQualityEnricher` —
  pure-Python composite scorer (token-rate + punctuation balance +
  repetition ratio + shouting ratio).

Future surface (RFC tasks not yet shipped):

- ``LangDetectEnricher`` (fasttext/langdetect backend).
- ``MinHashDedupEnricher`` (datasketch-backed near-dup clustering).
- ``BoilerplateEnricher`` (rule-based + optional LLM).
"""

from corpus_forge.quality.heuristic import (
    HeuristicQualityEnricher,
    HeuristicQualityScore,
)

__all__ = ["HeuristicQualityEnricher", "HeuristicQualityScore"]
