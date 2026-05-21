"""Phase R4 — `OllamaReranker` (score-via-completion fallback).

Ollama does NOT expose a native rerank API.  This implementation asks a
chat/completion model to score each ``(query, passage)`` pair on a 0-10
scale, then parses the score from the response.  Slow but works for
local-first stacks that already have Ollama running.

Mirror of the ``scripts/qwen3_via_ollama.py`` pattern: wraps the
``openai.OpenAI`` client pointed at Ollama's OpenAI-compat ``/v1`` base
URL.  The chosen Ollama model MUST be a chat/completion model — embedding
models like ``qwen3-embedding:8b`` are NOT rerankers.  There is
intentionally NO default ``model_id``; the caller must specify.

The implementation is intentionally minimal:

- Single-prompt scoring per ``(query, passage)`` pair (no batching).
  Reranking N hits costs N completions, so this is a debug / parity
  fixture, not a production path.  Use ``CrossEncoderReranker`` for real
  workloads.
- Score parsing falls back to ``0.0`` on malformed responses.  Failures
  are silent (no raise) so a flaky local Ollama doesn't poison the
  whole eval run.

The `openai` Python SDK is imported lazily inside :meth:`_get_client` so
``from corpus_forge.retrieval.rerank import OllamaReranker`` does not
pull `openai` at package-import time.
"""

from __future__ import annotations

import re
from typing import Any

from corpus_forge.retrieval.types import Hit

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_NAME = "ollama-reranker"

# Scoring prompt — kept terse to maximise the chance a small local model
# emits a single number.  The post-processing regex tolerates surrounding
# prose ("score: 7.5", "I'd give this an 8", etc.).
_SCORING_PROMPT_TEMPLATE = (
    "On a scale of 0 to 10, how relevant is the following passage to the query? "
    "Return ONLY a number; no prose, no units.\n\n"
    "Query: {query}\n"
    "Passage: {passage}\n"
    "Score: "
)

_SCORE_REGEX = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_score(text: str) -> float:
    """Extract a 0-10 score from the model's response text.

    On parse failure returns ``0.0``.  The score is clipped to ``[0, 10]``.
    """
    if not text:
        return 0.0
    match = _SCORE_REGEX.search(text)
    if not match:
        return 0.0
    try:
        score = float(match.group(1))
    except ValueError:  # pragma: no cover - regex already guarantees float-parseable
        return 0.0
    return max(0.0, min(10.0, score))


class OllamaReranker:
    """Score-via-completion reranker over an Ollama-served chat model.

    Args:
        model_id: Ollama tag of a CHAT model (e.g. ``"llama3.1:8b"``).
            No default — the caller must specify because embedding-only
            tags (``qwen3-embedding:8b``) silently produce garbage.
        base_url: OpenAI-compat endpoint (Ollama default
            ``http://localhost:11434/v1``).
        name: Short human-readable label.

    Notes:
        Reranking N hits costs N completions; this is a debug / parity
        path, not a production reranker.  Use ``CrossEncoderReranker``
        for real workloads.
    """

    name: str
    model_id: str

    def __init__(
        self,
        model_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        name: str = DEFAULT_NAME,
    ) -> None:
        self.model_id = model_id
        self.base_url = base_url
        # The OpenAI SDK insists on a non-empty api_key even when the
        # upstream (a local open Ollama) ignores it; the placeholder is
        # used when the caller hasn't supplied one. Pass a real key to
        # authenticate against hosted Ollama / OpenAI-compatible proxies.
        self.api_key = api_key or "ollama-no-auth"
        self.name = name
        # Memoised OpenAI client; instantiated lazily.
        self._client: Any | None = None

    # ── lazy client ────────────────────────────────────────────────────────

    def _get_client(self) -> Any:
        """Construct (and cache) the OpenAI-compat client pointed at Ollama."""
        if self._client is not None:
            return self._client
        from openai import OpenAI  # noqa: PLC0415

        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    # ── public API ─────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Construct the client (does NOT round-trip to Ollama)."""
        self._get_client()

    def rerank(
        self,
        query: str,
        hits: list[Hit],
        *,
        top_n: int | None = None,
    ) -> list[Hit]:
        """Score each candidate hit via the chat completion endpoint.

        Empty input short-circuits without contacting Ollama.
        """
        if not hits:
            return []

        candidates = hits[:top_n] if top_n is not None else list(hits)

        client = self._get_client()
        scores: list[float] = []
        for h in candidates:
            prompt = _SCORING_PROMPT_TEMPLATE.format(query=query, passage=h.text)
            try:
                resp = client.chat.completions.create(
                    model=self.model_id,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                # OpenAI-compat: resp.choices[0].message.content
                content = resp.choices[0].message.content or ""
            except Exception:
                # Network / parse error → score 0 so the hit sinks but
                # doesn't crash the whole rerank pass.
                content = ""
            scores.append(_parse_score(content))

        indexed = list(zip(candidates, scores, strict=True))
        indexed.sort(key=lambda pair: (-pair[1], -pair[0].score, pair[0].chunk_id))

        out: list[Hit] = []
        for hit, new_score in indexed:
            out.append(
                Hit(
                    chunk_id=hit.chunk_id,
                    score=new_score,
                    text=hit.text,
                    document_id=hit.document_id,
                    source_uri=hit.source_uri,
                    title=hit.title,
                    dataset_id=hit.dataset_id,
                    metadata=hit.metadata,
                    source="reranked",
                )
            )

        if top_n is not None:
            return out[:top_n]
        return out
