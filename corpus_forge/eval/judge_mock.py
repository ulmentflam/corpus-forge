"""corpus_forge.eval.judge_mock — Deterministic mock LLM judge.

Used as a drop-in for ``JudgeClient`` when ``--judge-endpoint=mock``.

The mock scores are derived by hashing the prompt text with SHA-256 so
that:

- Two calls with identical prompts always return identical scores.
- Different prompts (probably) return different scores.
- No network or model is required.

Returned dict shape matches ``JudgeClient.score``::

    {
        "faithfulness":       float in [0.0, 1.0],
        "answer_relevance":   float in [0.0, 1.0],
        "context_precision":  float in [0.0, 1.0],
        "context_recall":     float in [0.0, 1.0],
    }
"""

from __future__ import annotations

import hashlib


def score(prompt: str) -> dict[str, float]:
    """Return deterministic judge scores for *prompt*.

    Each of the four judge dimensions is derived from a different 2-byte
    slice of the SHA-256 digest so they are independent (though all are a
    deterministic function of the prompt text).

    Parameters
    ----------
    prompt:
        The full prompt string that would be sent to the LLM judge.

    Returns
    -------
    dict[str, float]
        Keys: ``faithfulness``, ``answer_relevance``, ``context_precision``,
        ``context_recall``.  Each value is a float in ``[0.0, 1.0]``.
    """
    digest = hashlib.sha256(prompt.encode()).digest()

    def _byte_pair_to_float(b1: int, b2: int) -> float:
        """Map two bytes (0-255 each) to a float in [0.0, 1.0]."""
        raw = (b1 << 8) | b2  # 0..65535
        return raw / 65535.0

    return {
        "faithfulness": _byte_pair_to_float(digest[0], digest[1]),
        "answer_relevance": _byte_pair_to_float(digest[2], digest[3]),
        "context_precision": _byte_pair_to_float(digest[4], digest[5]),
        "context_recall": _byte_pair_to_float(digest[6], digest[7]),
    }
