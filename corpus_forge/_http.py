"""Shared HTTP transport for remote model-backend clients.

Every remote model integration in corpus-forge (VLM, Whisper, code
enricher, LLM classifier, multi-modal embedder) speaks to a JSON HTTP
endpoint and maps the same set of failure modes onto a family-specific
error triad — ``<Family>UnavailableError`` / ``TimeoutError`` /
``ResponseError``.

This module owns the mapping in one place. Each family declares the
triad with an :class:`HttpErrors` bundle and calls :func:`request_json`,
which:

- catches the standard ``requests`` exception ladder (``Timeout`` /
  ``ConnectionError`` / ``RequestException``) and raises the matching
  family-typed error;
- optionally promotes ``401``/``403`` to the family's "unavailable"
  bucket (API-key rejection is a configuration failure, not a flake);
- treats non-2xx HTTP, malformed JSON, non-object JSON, and missing
  required keys as response errors with a truncated body snippet in the
  message.

Tests mock ``requests.post`` / ``requests.get`` directly. This module
calls them by name (not via ``requests.request``) so existing
``patch("requests.post", ...)`` contracts continue to work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["HttpErrors", "bearer_headers", "request_json"]

# Snippet length for response bodies inside error messages.  Long enough
# to be informative, short enough to keep audit logs scannable.
_BODY_SNIPPET = 200

Method = Literal["GET", "POST"]


@dataclass(frozen=True)
class HttpErrors:
    """The three discriminable HTTP-transport error classes for a family.

    Declared once at module scope per family (e.g.
    ``_ERR = HttpErrors(VLMUnavailableError, VLMTimeoutError,
    VLMResponseError)``) and threaded through :func:`request_json` so
    the shared transport raises the right family-typed exception.
    """

    unavailable: type[BaseException]
    timeout: type[BaseException]
    response: type[BaseException]


def bearer_headers(
    api_key: str | None, *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Build a header dict with optional ``Authorization: Bearer <key>``.

    Returns an empty dict (plus any ``extra`` overrides) when ``api_key``
    is falsy — matches the "open hosted Ollama" case where the header is
    omitted entirely.
    """
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        headers.update(extra)
    return headers


def _snippet(text: str | None) -> str:
    return (text or "")[:_BODY_SNIPPET]


def request_json(
    method: Method,
    url: str,
    *,
    timeout_s: float,
    errors: HttpErrors,
    label: str,
    base_url: str | None = None,
    json_body: Mapping[str, Any] | None = None,
    files: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    api_key: str | None = None,
    required_keys: Sequence[str] = (),
    auth_to_unavailable: bool = True,
    health_check: bool = False,
) -> dict[str, Any]:
    """Issue an HTTP request and return the parsed JSON object.

    Args:
        method: ``"GET"`` or ``"POST"``. POST is dispatched to
            ``requests.post``; GET to ``requests.get`` — by name so test
            ``patch("requests.post", ...)`` contracts survive.
        url: Fully composed request URL.
        timeout_s: Per-request HTTP budget.
        errors: Family-specific :class:`HttpErrors` triad.
        label: Human-readable name used in error messages
            (e.g. ``"Ollama generate"``, ``"Mistral OCR"``).
        base_url: Base URL shown in "Cannot connect to <label> at
            <base_url>" — defaults to ``url`` when omitted.
        json_body: Mapping to serialise as the JSON request body.
        files: Multipart upload files (POST only).
        data: Multipart form-data fields (POST only).
        headers: Extra request headers. ``Authorization`` is set
            automatically when ``api_key`` is provided.
        api_key: Bearer token. ``None`` / empty omits the header.
        required_keys: Top-level keys that MUST appear in the parsed
            JSON; missing keys raise ``errors.response`` (or
            ``errors.unavailable`` when ``health_check=True``).
        auth_to_unavailable: When True (default), 401/403 responses are
            raised as ``errors.unavailable`` ("API key rejected"). Set
            False for endpoints without auth (local Ollama daemons).
        health_check: Probe-mode toggle. When True, every non-success
            failure — Timeout, non-2xx, malformed JSON, missing required
            key — is raised as ``errors.unavailable`` ("not reachable" /
            "unhealthy"). Use this for warmup probes; leave False for
            body calls where Timeout vs Response is a meaningful
            distinction for retry/back-off callers.

    Returns:
        The parsed top-level JSON object (always a ``dict``).

    Raises:
        errors.unavailable: connect refused / DNS failure / 401 / 403 /
            generic ``RequestException``, or — with ``health_check=True``
            — any other non-success.
        errors.timeout: ``requests.Timeout`` on a body call
            (``health_check=False``).
        errors.response: non-2xx HTTP, malformed JSON, non-object JSON,
            or a missing ``required_keys`` entry (``health_check=False``).
    """
    import requests  # noqa: PLC0415 — lazy: every model backend keeps `requests` optional

    request_headers = bearer_headers(api_key, extra=headers)

    kwargs: dict[str, Any] = {"headers": request_headers, "timeout": timeout_s}
    if json_body is not None:
        kwargs["json"] = dict(json_body)
    if files is not None:
        kwargs["files"] = dict(files)
    if data is not None:
        kwargs["data"] = dict(data)

    base = base_url if base_url is not None else url
    fn = requests.post if method == "POST" else requests.get

    # In health-check mode, response/timeout failures collapse to the
    # unavailable bucket. We pick the response-error class once and the
    # body-validation branches reuse it.
    body_error = errors.unavailable if health_check else errors.response

    try:
        resp = fn(url, **kwargs)
    except requests.Timeout as exc:
        if health_check:
            raise errors.unavailable(
                f"{label} at {base} did not respond within {timeout_s}s — is it reachable?"
            ) from exc
        raise errors.timeout(f"{label} exceeded {timeout_s}s budget at {url}") from exc
    except requests.ConnectionError as exc:
        raise errors.unavailable(f"Cannot connect to {label} at {base}: {exc}") from exc
    except requests.RequestException as exc:
        raise errors.unavailable(f"{label} request failed: {exc}") from exc

    if auth_to_unavailable and resp.status_code in (401, 403):
        raise errors.unavailable(
            f"{label} API key rejected (HTTP {resp.status_code}): {_snippet(resp.text)}"
        )
    if not resp.ok:
        raise body_error(f"HTTP {resp.status_code}: {_snippet(resp.text)}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise body_error(f"Malformed JSON from {label}: {_snippet(resp.text)}") from exc

    if not isinstance(payload, dict):
        raise body_error(f"{label} returned non-object JSON: {str(payload)[:_BODY_SNIPPET]}")

    for key in required_keys:
        if key not in payload:
            raise body_error(
                f"{label} response missing {key!r} key: {str(payload)[:_BODY_SNIPPET]}"
            )

    return payload
