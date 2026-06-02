"""``corpus-forge agents`` — corpus-grounded AGENTS.md synthesis.

This subpackage implements ``corpus-forge agents init`` — a CLI verb
that walks a project root, samples representative local patterns,
queries the indexed cross-corpus for the same idioms in the user's
other projects, then asks the configured LLM to fuse the three
signals into a **two-output** split:

- ``<project-root>/.corpus-agents/AGENTS.md`` — private, corpus-grounded,
  with chunk_id citations. Gitignored.
- ``<project-root>/.corpus-agents/shareable.md`` — sanitized subset,
  citation-free. Committable.
- ``<project-root>/.corpus-agents/{citations.json, meta.json}`` —
  structured artifacts.
- ``<project-root>/AGENTS.md`` — created with shareable content ONLY
  when absent. Never overwritten, not even with ``--force``.

The five modules are import-safe with no third-party dependencies
beyond the rest of corpus-forge:

- :mod:`detector` — non-recursive top-level project inspection.
- :mod:`sampler` — pure file-content sampling for local patterns.
- :mod:`cross_corpus` — language-scoped query battery against a
  :class:`corpus_forge.retrieval.retriever.Retriever`.
- :mod:`synthesizer` — two-pass LLM synthesis (private + shareable).
- :mod:`writer` — disk writes (``.corpus-agents/`` dir + conditional
  root ``AGENTS.md`` create + ``.gitignore`` append).
"""

from __future__ import annotations
