"""corpus_forge.analyze.language — Language detection with dispatcher.

Phase O Wave 2 (O2-G2).

Public surface
--------------
- ``detect_language(text, *, detector)`` — returns ``(iso_code, confidence)``.
- ``detect_language_batch(texts, *, detector)`` — per-item wrapper preserving
  input order.

Lazy-import contract
--------------------
Neither ``fasttext`` nor ``langdetect`` is imported at module top level.
All such imports live inside the function body of each dispatch branch so
``corpus-forge --help`` cold-start budget is unaffected.

detector=None path
------------------
Resolves ``detector`` from ``Config.load().analyze.language_detector``.
Callers that want zero config coupling MUST pass ``detector`` explicitly.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O2.
"""

from __future__ import annotations

from typing import Literal


def detect_language(
    text: str,
    *,
    detector: Literal["fasttext", "langdetect"] | None = None,
) -> tuple[str, float]:
    """Detect the language of *text*.

    Parameters
    ----------
    text:
        Input text.  Empty or whitespace-only strings return
        ``("und", 0.0)`` without raising.
    detector:
        Which backend to use.  ``None`` (default) resolves the value from
        ``Config.load().analyze.language_detector``.  When config is not
        available, falls back to ``"langdetect"``.

    Returns
    -------
    tuple[str, float]
        ``(iso_code, confidence)`` where *iso_code* is a 2-letter ISO 639-1
        code (e.g. ``"en"``, ``"fr"``) or ``"und"`` for undetermined, and
        *confidence* is a float in ``[0.0, 1.0]``.
    """
    # Resolve detector from config when not supplied explicitly.
    if detector is None:
        try:
            from corpus_forge.config import Config  # lazy — keeps module import cheap

            cfg = Config.load()
            detector = cfg.analyze.language_detector
        except Exception:
            detector = "langdetect"

    # Empty / whitespace-only: undetermined, no exception.
    if not text or not text.strip():
        return ("und", 0.0)

    if detector == "fasttext":
        return _detect_fasttext(text)
    else:
        return _detect_langdetect(text)


def detect_language_batch(
    texts: list[str],
    *,
    detector: Literal["fasttext", "langdetect"] | None = None,
) -> list[tuple[str, float]]:
    """Detect language for each item in *texts*, preserving input order.

    Parameters
    ----------
    texts:
        List of input strings.  An empty list returns ``[]``.
    detector:
        Forwarded to :func:`detect_language` for every item.

    Returns
    -------
    list[tuple[str, float]]
        One ``(iso_code, confidence)`` result per input text, in the same
        order as *texts*.
    """
    return [detect_language(t, detector=detector) for t in texts]


# ---------------------------------------------------------------------------
# Private dispatch helpers — imports happen HERE, not at module top.
# ---------------------------------------------------------------------------


def _detect_langdetect(text: str) -> tuple[str, float]:
    """Dispatch to the langdetect backend (pure-Python)."""
    try:
        from langdetect import detect_langs  # lazy

        try:
            from langdetect.lang_detect_exception import LangDetectException
        except ImportError:
            # Fallback: the module may expose it directly.
            try:
                from langdetect import LangDetectException  # type: ignore[no-redef]
            except ImportError:
                LangDetectException = Exception  # type: ignore[misc,assignment]

        try:
            results = detect_langs(text)
            if not results:
                return ("und", 0.0)
            top = results[0]
            return (top.lang, float(top.prob))
        except LangDetectException:
            return ("und", 0.0)
    except Exception:
        return ("und", 0.0)


def _detect_fasttext(text: str) -> tuple[str, float]:
    """Dispatch to the fasttext_langdetect backend."""
    try:
        import fasttext_langdetect as _ft_mod  # lazy
    except ImportError:
        # Also try the bare fasttext package name.
        try:
            import fasttext as _ft_mod  # type: ignore[no-redef]  # lazy
        except ImportError:
            raise RuntimeError(
                "fasttext is not installed; install with: pip install fasttext-langdetect"
            ) from None

    result = _ft_mod.fasttext.detect(text)
    iso_code = result["lang"]
    confidence = float(result["score"])
    return (iso_code, confidence)
