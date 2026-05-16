"""Integration-suite-local pytest fixtures and skip plumbing.

Wave 6 / Phase D P1 introduced two marker-gated test suites that need a
*live* dependency to run:

- ``requires_ollama`` — needs the local Ollama daemon reachable and the
  ``qwen2.5vl`` model pulled (any tag — ``qwen2.5vl:7b`` is the default,
  ``:32b`` is acceptable too).
- ``requires_mistral_api`` — needs ``MISTRAL_API_KEY`` set in the
  environment.

Phase E P1 (C-12) adds:

- ``requires_ollama_text`` — needs the local Ollama daemon reachable
  and a ``qwen2.5:*-instruct`` text model pulled (default
  ``qwen2.5:7b-instruct``). Distinct from ``requires_ollama`` because
  the VLM model (``qwen2.5vl:7b``) and the text model
  (``qwen2.5:7b-instruct``) are separate downloads — a machine may
  have one but not the other.

Tests carrying those markers must skip cleanly when the dependency is
absent so CI (which has none of them) stays green. The skip decision
is made once at *collection* time so the skipped tests show as
``SKIPPED`` in the report rather than erroring inside fixtures.

This conftest is intentionally additive — the root ``tests/conftest.py``
still owns the Postgres / testcontainers fixtures (``pg_dsn`` et al.)
and the global FORCE_COLOR / NO_COLOR scrubs.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# ── Public fixtures (importable from tests) ──────────────────────────────


@pytest.fixture(scope="session")
def ollama_ready() -> bool:
    """Return True if the Ollama daemon is reachable AND a qwen2.5vl model is present.

    Probe is a single ``GET /api/tags`` with a 2-second timeout. Any
    non-2xx response, connection error, or missing-model condition
    returns False — the marker-based skip below interprets that as
    "skip every ``requires_ollama`` test".

    Tests can depend on this fixture explicitly if they need to read
    "did the daemon answer?" inside the test body, but the common case
    (auto-skip via marker) does not require an explicit dependency.
    """
    return _probe_ollama()


@pytest.fixture(scope="session")
def ollama_text_ready() -> bool:
    """Return True iff Ollama is reachable AND a qwen2.5:*-instruct model is pulled.

    Mirrors :func:`ollama_ready` but probes for a text-instruct model
    rather than the vision model. Used by Phase E P1 (the LLM
    classifier) live integration tests.
    """
    return _probe_ollama_text()


@pytest.fixture(scope="session")
def whisper_local_ready() -> bool:
    """Return True iff ``faster-whisper`` imports cleanly AND a tiny model can load.

    Phase G (G-08). Used by :data:`requires_whisper_local`. The probe
    is best-effort: any ImportError or load-time exception is treated
    as "skip".
    """
    return _probe_whisper_local()


@pytest.fixture(scope="session")
def clip_local_ready() -> bool:
    """Return True iff ``sentence-transformers`` can load ``clip-ViT-B-32``.

    Phase G (G-16). Used by :data:`requires_clip_local`.
    """
    return _probe_clip_local()


@pytest.fixture(scope="session")
def mistral_ready() -> bool:
    """Return True iff ``MISTRAL_API_KEY`` is set to a non-empty value.

    Live API roundtrips are *not* probed — the marker-gated tests own
    their own HTTP error handling. The collection-time skip below uses
    the same predicate to flip every ``requires_mistral_api`` test to
    SKIPPED when no key is configured.
    """
    return bool(os.environ.get("MISTRAL_API_KEY"))


# ── Collection-time skip plumbing ────────────────────────────────────────


def _probe_ollama() -> bool:
    """Best-effort probe for the VLM tag; returns False on any error."""
    return _probe_ollama_for("qwen2.5vl")


def _probe_ollama_text() -> bool:
    """Best-effort probe for a ``qwen2.5:*-instruct`` text model.

    Phase E P1 (C-12). Accepts any ``qwen2.5:`` tag whose suffix
    contains the ``instruct`` substring — covers ``qwen2.5:7b-instruct``
    (project default), ``qwen2.5:3b-instruct``, ``qwen2.5:14b-instruct``,
    etc.
    """
    try:
        import requests  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2.0)
    except Exception:
        return False
    if resp.status_code != 200:
        return False
    try:
        payload: dict[str, Any] = resp.json()
    except ValueError:
        return False
    models = payload.get("models") or []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model") or ""
        if not isinstance(name, str):
            continue
        # Strict prefix on the family AND substring match on the
        # tag suffix: ``qwen2.5:7b-instruct`` matches, ``qwen2.5vl:7b``
        # does NOT.
        if name.startswith("qwen2.5:") and "instruct" in name:
            return True
    return False


def _probe_whisper_local() -> bool:
    """Phase G (G-08). Return True iff faster-whisper can be imported AND a
    tiny model loads cleanly. We don't actually transcribe in the probe —
    just verify the model machinery is reachable.

    Caches at the first call via :func:`functools.lru_cache` semantics
    (one probe per session through the fixture's scope), but the function
    is intentionally idempotent so a direct call from
    ``pytest_collection_modifyitems`` also works.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        # int8 / cpu — minimal install footprint; the model is downloaded
        # to ~/.cache on first run. If the download fails (offline), this
        # raises and we skip.
        WhisperModel("tiny", device="cpu", compute_type="int8")
    except Exception:
        return False
    return True


def _probe_clip_local() -> bool:
    """Phase G (G-16). Return True iff sentence-transformers can load
    ``clip-ViT-B-32``. The model is ~150 MB; first call downloads it.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        SentenceTransformer("clip-ViT-B-32")
    except Exception:
        return False
    return True


def _probe_ollama_for(prefix: str) -> bool:
    """Internal helper: True iff any tag begins with ``prefix``."""
    try:
        import requests  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2.0)
    except Exception:
        return False
    if resp.status_code != 200:
        return False
    try:
        payload: dict[str, Any] = resp.json()
    except ValueError:
        return False
    models = payload.get("models") or []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model") or ""
        if isinstance(name, str) and name.startswith(prefix):
            return True
    return False


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-skip marker-gated tests when their dependency is absent.

    Runs once after collection. Each marker is checked against a cached
    boolean so the probe (HTTP GET in the Ollama cases, env-var lookup
    in the Mistral case) happens at most once per session.
    """
    ollama_skip: pytest.MarkDecorator | None = None
    if not _probe_ollama():
        ollama_skip = pytest.mark.skip(
            reason="Ollama daemon at http://localhost:11434 unreachable or "
            "qwen2.5vl model not pulled (run: ollama pull qwen2.5vl:7b)"
        )

    ollama_text_skip: pytest.MarkDecorator | None = None
    if not _probe_ollama_text():
        ollama_text_skip = pytest.mark.skip(
            reason="Ollama daemon at http://localhost:11434 unreachable or "
            "qwen2.5:*-instruct model not pulled "
            "(run: ollama pull qwen2.5:7b-instruct)"
        )

    mistral_skip: pytest.MarkDecorator | None = None
    if not os.environ.get("MISTRAL_API_KEY"):
        mistral_skip = pytest.mark.skip(
            reason="MISTRAL_API_KEY not set in environment (see secrets.env.example)"
        )

    whisper_local_skip: pytest.MarkDecorator | None = None
    # Only probe if the marker is in use — saves a model-load probe in
    # the common case (CI without the [whisper] extra).
    _need_whisper_probe = any("requires_whisper_local" in i.keywords for i in items)
    if _need_whisper_probe and not _probe_whisper_local():
        whisper_local_skip = pytest.mark.skip(
            reason="faster-whisper unavailable or model download failed "
            "(run: uv sync --extra whisper)"
        )

    clip_local_skip: pytest.MarkDecorator | None = None
    _need_clip_probe = any("requires_clip_local" in i.keywords for i in items)
    if _need_clip_probe and not _probe_clip_local():
        clip_local_skip = pytest.mark.skip(
            reason="sentence-transformers + clip-ViT-B-32 unavailable "
            "(model download failed or sentence-transformers not installed)"
        )

    for item in items:
        if ollama_skip is not None and "requires_ollama" in item.keywords:
            item.add_marker(ollama_skip)
        if ollama_text_skip is not None and "requires_ollama_text" in item.keywords:
            item.add_marker(ollama_text_skip)
        if mistral_skip is not None and "requires_mistral_api" in item.keywords:
            item.add_marker(mistral_skip)
        if whisper_local_skip is not None and "requires_whisper_local" in item.keywords:
            item.add_marker(whisper_local_skip)
        if clip_local_skip is not None and "requires_clip_local" in item.keywords:
            item.add_marker(clip_local_skip)
