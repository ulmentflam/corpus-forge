"""``corpus-forge agents`` — corpus-grounded AGENTS.md synthesis.

This subpackage implements ``corpus-forge agents init`` — a CLI verb
that walks a project root, samples representative local patterns,
queries the indexed cross-corpus for the same idioms in the user's
other projects, then asks the configured LLM to fuse the three
signals into a single ``AGENTS.md`` (with an auto-generated
``CLAUDE.md`` pointer).

The four modules are import-safe with no third-party dependencies
beyond the rest of corpus-forge:

- :mod:`detector` — non-recursive top-level project inspection.
- :mod:`sampler` — pure file-content sampling for local patterns.
- :mod:`cross_corpus` — language-scoped query battery against a
  :class:`corpus_forge.retrieval.retriever.Retriever`.
- :mod:`synthesizer` — LLM prompt + structured ``SynthesisResult``.
- :mod:`writer` — disk writes (force vs draft + CLAUDE.md pointer).
"""

from __future__ import annotations
