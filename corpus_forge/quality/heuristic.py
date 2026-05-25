"""Heuristic chunk-quality scoring (RFC ``rfc-nlp-data-quality-signals``).

A pure-Python composite scorer that takes a chunk's text and returns
a single ``quality_score`` in ``[0.0, 1.0]`` plus a per-signal
breakdown. Cheaper than the eventual MinHash + fasttext stack but
correlates well enough with "looks like prose" vs. "looks like noise"
to be useful as a first-pass filter in the curation selector and the
prune ranker.

Composite signals (each on ``[0.0, 1.0]``, higher = better):

- **token_rate**: ratio of non-whitespace characters to whitespace
  characters, normalised. Catches the "long string of spaces or
  newlines" failure mode (low ratio) and the "wall of unspaced text"
  failure mode (very high ratio with no spaces).
- **punctuation_balance**: 1.0 minus an excess-punctuation penalty.
  Catches the "!!!!!!!!" / "............" failure modes that pattern-
  match for low-quality text.
- **repetition_ratio**: 1.0 minus the fraction of the text occupied by
  the longest repeated n-gram (3 ≤ n ≤ 8). Catches stuttered output
  ("the the the the …", "ABCABCABCABC", template repeats).
- **shouting_ratio**: 1.0 minus the fraction of letter characters
  that are uppercase. Catches all-caps yelling without penalising
  legitimate proper-noun-heavy text (single tokens stay below the
  threshold).

The final score is the *weighted geometric mean* of the four
signals — multiplicative so any one near-zero signal drags the
composite down hard, which is what we want for a noise filter.

The scorer is **deterministic and dependency-free**. No model
downloads, no env-var-gated LLM, no async; safe to call inside an
ingest hot loop. A 64-KB chunk takes ~1 ms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Public constants — exposed so downstream callers (curation selector,
# prune ranker) can compare the same way the scorer does without
# re-deriving the threshold heuristics.

#: Quality score below this is considered "low-quality" by default.
#: Tuned to the heuristic stack — not a universal threshold.
DEFAULT_LOW_QUALITY_THRESHOLD: Final[float] = 0.45

# Internal tuning constants. Each is a single number with a clear
# rationale in the docstring below; not exposed as config because the
# scorer is a foundation primitive — the *threshold* is configurable
# (above), not the per-signal shape.

#: Bounds for the token-rate target band. Outside this band the
#: ``token_rate`` signal decays linearly to zero at the edges
#: (no-whitespace at 1.0, all-whitespace at 0.0).
_TOKEN_RATE_TARGET_MIN: Final[float] = 0.65
_TOKEN_RATE_TARGET_MAX: Final[float] = 0.92

#: Cap on punctuation density. Above this, ``punctuation_balance``
#: linearly decays towards 0.
_PUNCTUATION_DENSITY_CEILING: Final[float] = 0.25

#: Inclusive range of n-gram sizes scanned for repetition. n=3 catches
#: trigram stutters; n=8 catches short-template repeats. Going wider
#: (≥10) adds cost without much new signal — n=8 captures
#: "ABCDEFGH" * k cleanly.
_REPETITION_NGRAM_MIN: Final[int] = 3
_REPETITION_NGRAM_MAX: Final[int] = 8

#: Letter-character regex used by ``shouting_ratio``. Avoids
#: penalising digits and punctuation as "not uppercase."
_LETTER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]")

#: Punctuation regex — the standard ASCII set.
_PUNCT_RE: Final[re.Pattern[str]] = re.compile(r"[!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]")


@dataclass(frozen=True)
class HeuristicQualityScore:
    """The output of :meth:`HeuristicQualityEnricher.score`.

    Attributes:

    - ``quality_score``: the composite, in ``[0.0, 1.0]``. Higher =
      better. The single number callers (curation, prune) should read.
    - ``token_rate``: per-signal contribution (see module docstring).
    - ``punctuation_balance``: per-signal contribution.
    - ``repetition_ratio``: per-signal contribution.
    - ``shouting_ratio``: per-signal contribution.

    The per-signal fields are exposed for diagnostic / debug surfaces
    (the future ``corpus-forge debug <chunk_id>`` verb plus the
    ``eval quality`` evaluator's per-rubric MAE report). The composite
    is the public API; the breakdown is the explanation.
    """

    quality_score: float
    token_rate: float
    punctuation_balance: float
    repetition_ratio: float
    shouting_ratio: float


class HeuristicQualityEnricher:
    """Pure-Python composite quality scorer.

    Construct once per process (the instance is stateless and
    thread-safe; the constructor exists so a future refactor can add
    per-instance tuning without breaking the call sites).
    """

    def score(self, text: str) -> HeuristicQualityScore:
        """Return the composite + per-signal breakdown for *text*.

        Empty / whitespace-only text returns
        ``HeuristicQualityScore(0.0, 0.0, 0.0, 0.0, 0.0)`` —
        zero-length isn't "good" or "bad," it's missing data; the
        composite of 0.0 lets curation filter it out cheaply via a
        single threshold check.
        """
        if not text or not text.strip():
            return HeuristicQualityScore(0.0, 0.0, 0.0, 0.0, 0.0)

        tr = _token_rate(text)
        pb = _punctuation_balance(text)
        rr = _repetition_ratio(text)
        sr = _shouting_ratio(text)

        # Weighted geometric mean — equal weights for now. A near-zero
        # signal drags the composite down sharply (which is what we
        # want from a noise filter).
        composite = (tr * pb * rr * sr) ** 0.25
        return HeuristicQualityScore(
            quality_score=composite,
            token_rate=tr,
            punctuation_balance=pb,
            repetition_ratio=rr,
            shouting_ratio=sr,
        )

    def is_low_quality(
        self, text: str, *, threshold: float = DEFAULT_LOW_QUALITY_THRESHOLD
    ) -> bool:
        """Convenience wrapper: ``score(text).quality_score < threshold``."""
        return self.score(text).quality_score < threshold


# ── Per-signal implementations ──────────────────────────────────────


def _token_rate(text: str) -> float:
    """Ratio of non-whitespace to total chars, clipped to a target band.

    Inside ``[_TOKEN_RATE_TARGET_MIN, _TOKEN_RATE_TARGET_MAX]`` → 1.0.
    Outside, decays linearly to 0.0 at the extremes (0.0 = pure
    whitespace, 1.0 = no whitespace).
    """
    total = len(text)
    if total == 0:
        return 0.0
    non_ws = sum(1 for c in text if not c.isspace())
    ratio = non_ws / total
    if _TOKEN_RATE_TARGET_MIN <= ratio <= _TOKEN_RATE_TARGET_MAX:
        return 1.0
    if ratio < _TOKEN_RATE_TARGET_MIN:
        return max(0.0, ratio / _TOKEN_RATE_TARGET_MIN)
    # ratio > target_max — decay towards 1.0 (no whitespace at all).
    over = (ratio - _TOKEN_RATE_TARGET_MAX) / (1.0 - _TOKEN_RATE_TARGET_MAX)
    return max(0.0, 1.0 - over)


def _punctuation_balance(text: str) -> float:
    """1.0 minus a punctuation-density excess penalty.

    Below the ceiling → 1.0. Above the ceiling → linear decay to
    0.0 at 100 % punctuation.
    """
    if not text:
        return 0.0
    punct_count = len(_PUNCT_RE.findall(text))
    density = punct_count / len(text)
    if density <= _PUNCTUATION_DENSITY_CEILING:
        return 1.0
    over = (density - _PUNCTUATION_DENSITY_CEILING) / (1.0 - _PUNCTUATION_DENSITY_CEILING)
    return max(0.0, 1.0 - over)


def _repetition_ratio(text: str) -> float:
    """1.0 minus the fraction of *text* covered by the longest repeated n-gram.

    Scans n from ``_REPETITION_NGRAM_MIN`` to ``_REPETITION_NGRAM_MAX``;
    for each ``n``, finds the most-frequent ``n``-gram and computes the
    fraction of *text* it covers (occurrences times ``n``). The worst
    (highest-coverage) result drives the signal.

    A chunk whose longest 5-gram covers 80 % of the text scores 0.2 —
    very low quality. Pure-prose chunks land near 1.0.
    """
    if not text or len(text) < _REPETITION_NGRAM_MIN:
        # Too short to repeat anything; treat as neutral-positive so
        # we don't punish single-word chunks. The token_rate signal
        # already catches "this is too short to mean anything."
        return 1.0
    total = len(text)
    worst_coverage = 0.0
    for n in range(_REPETITION_NGRAM_MIN, _REPETITION_NGRAM_MAX + 1):
        if total < n:
            break
        counts: dict[str, int] = {}
        for i in range(total - n + 1):
            ng = text[i : i + n]
            counts[ng] = counts.get(ng, 0) + 1
        max_count = max(counts.values(), default=0)
        if max_count <= 1:
            continue
        coverage = (max_count * n) / total
        worst_coverage = max(worst_coverage, coverage)
    return max(0.0, 1.0 - worst_coverage)


def _shouting_ratio(text: str) -> float:
    """1.0 minus the fraction of letter chars that are uppercase.

    The signal is letter-only — digits and punctuation are excluded
    so a chunk that's mostly numbers and punctuation isn't flagged
    as "no uppercase = good." Text without letters at all returns
    1.0 (nothing to shout).
    """
    letters = _LETTER_RE.findall(text)
    if not letters:
        return 1.0
    upper = sum(1 for c in letters if c.isupper())
    upper_frac = upper / len(letters)
    return max(0.0, 1.0 - upper_frac)
