"""corpus_forge.eval.judge — LLM-judge client.

Public API
----------
JudgeClient(endpoint, model="qwen2.5:7b-instruct", timeout=60.0)
    Thin client that sends a prompt to an Ollama/OpenAI-compatible LLM judge
    and parses the four RAGAS-style dimensions from the response.

    When ``endpoint == "mock"`` the client delegates to
    :func:`corpus_forge.eval.judge_mock.score` — no network call is made.

    When the real endpoint is unreachable, raises :exc:`JudgeUnavailable`
    rather than an unhandled exception so callers can handle it gracefully.

    Reads ``CF_JUDGE_ENDPOINT`` from the environment as a fallback if the
    caller does not pass an explicit endpoint.

score_prompt(endpoint, model, timeout, prompt) -> dict[str, float]
    Module-level helper; exposed for tests and the CLI layer.

JudgeUnavailable
    Exception raised when the real judge endpoint cannot be contacted.
"""

from __future__ import annotations

import os
from typing import Any


class JudgeUnavailable(RuntimeError):
    """Raised when the judge endpoint is not reachable or returns an error."""


def score_prompt(
    endpoint: str,
    model: str,
    timeout: float,
    prompt: str,
) -> dict[str, float]:
    """Score *prompt* using the configured judge backend.

    Parameters
    ----------
    endpoint:
        Either ``"mock"`` (deterministic hash-based scorer) or a URL such
        as ``"http://localhost:11434"`` (Ollama-compatible).
    model:
        Model identifier forwarded to the Ollama API.
    timeout:
        Request timeout in seconds.
    prompt:
        The full prompt string to score.

    Returns
    -------
    dict[str, float]
        Keys: ``faithfulness``, ``answer_relevance``, ``context_precision``,
        ``context_recall``.  Each value is in ``[0.0, 1.0]``.

    Raises
    ------
    JudgeUnavailable
        If the real endpoint is unreachable or returns an HTTP error.
    """
    if endpoint == "mock":
        from corpus_forge.eval.judge_mock import score  # noqa: PLC0415

        return score(prompt)

    # Real Ollama/OpenAI-compat path — lazy import so CLI cold-start is cheap.
    try:
        import json  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        url = endpoint.rstrip("/") + "/api/generate"
        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        response_text: str = body.get("response", "")
        return _parse_judge_response(response_text)
    except OSError as exc:
        raise JudgeUnavailable(f"Judge endpoint unreachable: {endpoint!r}: {exc}") from exc
    except Exception as exc:
        raise JudgeUnavailable(f"Judge error from {endpoint!r}: {exc}") from exc


def _parse_judge_response(text: str) -> dict[str, float]:
    """Parse a judge LLM response into the four-key score dict.

    Falls back to 0.5 for any dimension the model did not produce a
    parseable value for.
    """
    import json  # noqa: PLC0415
    import re  # noqa: PLC0415

    # Try to extract JSON block from the response.
    json_match = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if json_match:
        try:
            data: dict[str, Any] = json.loads(json_match.group())
            result: dict[str, float] = {}
            for key in ("faithfulness", "answer_relevance", "context_precision", "context_recall"):
                val = data.get(key, 0.5)
                result[key] = float(max(0.0, min(1.0, float(val))))
            return result
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Fallback: return neutral scores.
    return {
        "faithfulness": 0.5,
        "answer_relevance": 0.5,
        "context_precision": 0.5,
        "context_recall": 0.5,
    }


class JudgeClient:
    """LLM judge client.

    Parameters
    ----------
    endpoint:
        Judge URL (e.g. ``"http://localhost:11434"``) or ``"mock"``.
        Falls back to the ``CF_JUDGE_ENDPOINT`` environment variable when
        the caller does not supply an explicit value.
    model:
        Ollama model identifier.  Default: ``"qwen2.5:7b-instruct"``.
    timeout:
        Request timeout in seconds.  Default: ``60.0``.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        model: str = "qwen2.5:7b-instruct",
        timeout: float = 60.0,
    ) -> None:
        if endpoint is None:
            endpoint = os.environ.get("CF_JUDGE_ENDPOINT", "mock")
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout

    def score(self, prompt: str) -> dict[str, float]:
        """Score *prompt* and return the four judge dimensions.

        Returns
        -------
        dict[str, float]
            ``faithfulness``, ``answer_relevance``, ``context_precision``,
            ``context_recall`` — each in ``[0.0, 1.0]``.

        Raises
        ------
        JudgeUnavailable
            If the real endpoint is unreachable.
        """
        return score_prompt(self.endpoint, self.model, self.timeout, prompt)
