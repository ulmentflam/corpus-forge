"""Phase N Wave 1 — symbol-shaped query detection for the adaptive
lexical-weight bump.

Background
----------

Phase M Wave 5's semble investigation measured:

- semble (BM25 + tiny static embeddings) crushed identifier-search MRR
  (0.85) but lost ground on conversational queries.
- corpus-forge's :class:`HybridRetriever` is the reverse: strong on
  concept / error (0.72 / 0.83 aggregated) but stuck at 0.43 on the
  identifier category.

The Wave 1 decision: keep HybridRetriever, but lower the fusion alpha
on symbol-shaped queries so the lexical (BM25) signal contributes more
to the blend.  This module is the heuristic that gates that bump.

Heuristic (rule order)
----------------------

Returns ``True`` iff the WHOLE query "looks like" a code identifier or
accessor expression.  The rules, in evaluation order:

1. Empty / all-whitespace → ``False``.
2. Contains any whitespace → ``False`` (multi-word queries are natural
   language even when they contain one symbol — see
   ``"how does HybridRetriever.search dispatch fusion"``).
3. Contains an accessor (``.``, ``::``, ``->``, or ``/``) → ``True``.
4. Whole-query identifier shape (``[A-Za-z_][A-Za-z0-9_]*``):
     - starts with ``_`` (private convention) → ``True``;
     - contains an uppercase letter after position 0
       (CamelCase / camelCase shape — catches ``MyClass`` and ``setUp``)
       → ``True``;
     - contains an underscore anywhere (snake_case) → ``True``
       (catches ``manage_block_sentinels_not_found`` — function /
       variable name shape);
     - otherwise → ``False`` (all-lowercase, no underscore, no
       uppercase — looks like a normal English word: ``setup``,
       ``foo``, ``get``).
5. Anything else (non-identifier shape) → ``False``.

Trade-offs (documented for the heuristic-tuning audit trail)
------------------------------------------------------------

- **False positive** risk: snake_case English phrases of the form
  ``"manage_block_sentinels_not_found"`` are deliberately classified as
  symbol — the Wave 1 spec calls this out as an acknowledged risk.  The
  underlying assumption is that a user typing snake_case into the
  search box is asking about an identifier; if they wanted natural
  language, they'd use spaces.
- **False negative** risk: natural-language queries that wrap one
  symbol (``"what is HybridRetriever"``) are classified as natural
  language.  The Wave 1 spec explicitly picks this conservative bias
  so concept-category MRR stays unharmed.

The composite rule (uppercase OR underscore OR leading-underscore)
intentionally has no length floor.  Length-based filters were tried
during Wave 1 RED tuning and either rejected ``manage`` (length 6
all-lowercase, no underscore — correctly classified False here) or
admitted ``setup`` (length 5 — also correctly False here) — neither
behaviour was an improvement over the shape-based composite.
"""

from __future__ import annotations

import re

# Bare-identifier regex: ASCII identifier characters, leading char letter
# or underscore.  Tightening to ASCII (vs Unicode ``\w``) is deliberate —
# Unicode word chars include CJK / accents that would slip past the
# accessor check and produce false-positive natural-language matches.
_BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Accessor tokens.  Membership ("any of these substrings appears") is
# fine because we already short-circuited on whitespace; we don't need to
# tokenise to detect ``::`` distinct from a stray ``:``.
_ACCESSORS: tuple[str, ...] = ("::", "->", ".", "/")


def is_symbol_shaped(query: str) -> bool:
    """Return True iff the whole query looks like a code symbol / accessor.

    See module docstring for the rule order and trade-offs.

    Args:
        query: the user query string.  Not normalised — a query that
            contains internal whitespace is short-circuited to False
            (see Rule 2 in the module docstring).

    Returns:
        ``True`` when the query should trip the adaptive lexical-weight
        bump; ``False`` for natural-language / error-string / short-
        keyword queries.
    """
    if not query:
        return False
    # Rule 1 / 2: short-circuit on whitespace.  Empty-after-strip catches
    # the all-whitespace case; ``any(c.isspace())`` catches embedded
    # whitespace.  Two checks are cheap and keep the rule order clear.
    if not query.strip():
        return False
    if any(c.isspace() for c in query):
        return False

    # Rule 3: accessor → symbol.  Cheap O(len(query) * len(_ACCESSORS)).
    for tok in _ACCESSORS:
        if tok in query:
            return True

    # Rule 4: bare identifier — must carry at least one shape signal
    # (leading underscore, snake_case underscore, or CamelCase uppercase
    # post-pos-0).  All-lowercase bare identifiers are classified as
    # natural-language tokens (English words like ``setup`` / ``get``).
    if _BARE_IDENT_RE.match(query) is None:
        return False

    if query.startswith("_"):
        return True

    if any(c.isupper() for c in query[1:]):
        return True

    return "_" in query
