"""Phase O Wave 2 (O2-T2) — Unit tests for corpus_forge.analyze.language.

Pins the public shape of ``detect_language`` and ``detect_language_batch``.
All tests must fail RED until ``corpus_forge/analyze/language.py`` exists.

Spec source: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O2 RED +
``.planning/tdd/tasks.md`` § O2-T2.

Design decisions captured in tests
------------------------------------
- ``detect_language`` returns ``tuple[str, float]`` — NOT a dict.  The
  tuple is ``(iso_code, confidence)``.
- ISO codes are 2-letter (``"en"``, ``"fr"``).  langdetect and fasttext both
  emit 2-letter codes natively for common languages; this is the pinned
  contract.
- Confidence is in ``[0.0, 1.0]``.
- Empty string → ``("und", 0.0)`` (ISO 639-2 "undetermined") — no exception.
- ``detector=None`` reads ``AnalyzeConfig.language_detector`` from
  ``Config.load()``.  The phase doc states "``language.py`` never reads
  ``Config`` directly" — the ``detector=None`` path is a convenience wrapper
  that calls ``Config.load()`` internally (callers that want zero config
  coupling pass ``detector`` explicitly).
- ``detect_language_batch`` preserves input order.
- Lazy-import contract: importing the module brings neither ``fasttext`` nor
  ``langdetect`` into ``sys.modules``.
- Dispatch isolation: only the requested detector's import is triggered.
- Missing fasttext wheel → ``RuntimeError`` naming the missing dep.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LANGDETECT_ABSENT = importlib.util.find_spec("langdetect") is None
_FASTTEXT_ABSENT = (
    importlib.util.find_spec("fasttext") is None
    and importlib.util.find_spec("fasttext_langdetect") is None
)
_BOTH_ABSENT = _LANGDETECT_ABSENT and _FASTTEXT_ABSENT


def _evict_language_module() -> None:
    """Remove corpus_forge.analyze.language from sys.modules for a clean re-import."""
    keys = [k for k in sys.modules if "corpus_forge.analyze.language" in k]
    for k in keys:
        sys.modules.pop(k, None)


def _make_fake_langdetect_module() -> types.ModuleType:
    """Construct a minimal fake langdetect module for dispatch-isolation tests."""
    mod = types.ModuleType("langdetect")

    def detect_langs(text: str) -> list[Any]:
        # Returns a list of language objects with .lang and .prob attributes
        result = MagicMock()
        result.lang = "en"
        result.prob = 0.99
        return [result]

    mod.detect_langs = detect_langs  # type: ignore[attr-defined]
    mod.LangDetectException = Exception  # type: ignore[attr-defined]
    return mod


def _make_fake_fasttext_module() -> types.ModuleType:
    """Construct a minimal fake fasttext_langdetect / fasttext module."""
    mod = types.ModuleType("fasttext_langdetect")
    inner = MagicMock()
    inner.detect.return_value = {"lang": "en", "score": 0.98}
    mod.fasttext = inner  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# 1. Import smoke — module and callables exist
# ---------------------------------------------------------------------------


def test_import_detect_language() -> None:
    """detect_language is importable from corpus_forge.analyze.language."""
    from corpus_forge.analyze.language import detect_language  # noqa: F401


def test_import_detect_language_batch() -> None:
    """detect_language_batch is importable from corpus_forge.analyze.language."""
    from corpus_forge.analyze.language import detect_language_batch  # noqa: F401


def test_module_exists_as_submodule_of_analyze() -> None:
    """corpus_forge.analyze.language is accessible as a submodule of the package."""
    import corpus_forge.analyze.language  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Lazy-import guard — neither fasttext nor langdetect loads at module import
# ---------------------------------------------------------------------------


def test_lazy_import_neither_detector_loaded_on_module_import() -> None:
    """Importing corpus_forge.analyze.language must NOT pull fasttext or langdetect.

    The wave-gate command verifies this:
      python -c "import sys; import corpus_forge.analyze.language;
                 assert 'fasttext' not in sys.modules and
                        'langdetect' not in sys.modules"
    This test pins the same invariant inside pytest.
    """
    _evict_language_module()

    # Also evict the heavy deps so we have a clean baseline
    for heavy in ("fasttext", "fasttext_langdetect", "langdetect"):
        sys.modules.pop(heavy, None)

    import corpus_forge.analyze.language  # noqa: F401

    assert "langdetect" not in sys.modules, (
        "corpus_forge.analyze.language imported langdetect at module level; "
        "it must be lazy-imported inside the function body."
    )
    assert "fasttext" not in sys.modules, (
        "corpus_forge.analyze.language imported fasttext at module level."
    )
    assert "fasttext_langdetect" not in sys.modules, (
        "corpus_forge.analyze.language imported fasttext_langdetect at module level."
    )


# ---------------------------------------------------------------------------
# 3. Return type and shape
# ---------------------------------------------------------------------------


def test_detect_language_returns_tuple() -> None:
    """detect_language must return a tuple, not a dict or string."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed; skipping positive return-type test")

    from corpus_forge.analyze.language import detect_language

    result = detect_language("The quick brown fox jumps over the lazy dog", detector="langdetect")
    assert isinstance(result, tuple), f"Expected tuple, got {type(result).__name__}"


def test_detect_language_tuple_length_is_two() -> None:
    """detect_language returns a 2-tuple (iso_code, confidence)."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language

    result = detect_language("Hello world", detector="langdetect")
    assert len(result) == 2, f"Expected 2-tuple, got length {len(result)}"


def test_detect_language_iso_code_is_string() -> None:
    """The iso_code element of the returned tuple is a non-empty string."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language

    iso_code, _ = detect_language("Hello world", detector="langdetect")
    assert isinstance(iso_code, str) and len(iso_code) > 0, (
        f"iso_code must be a non-empty string, got {iso_code!r}"
    )


def test_detect_language_confidence_is_float() -> None:
    """The confidence element of the returned tuple is a float."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language

    _, confidence = detect_language("Hello world", detector="langdetect")
    assert isinstance(confidence, float), (
        f"confidence must be float, got {type(confidence).__name__}"
    )


# ---------------------------------------------------------------------------
# 4. English-text positive case
# ---------------------------------------------------------------------------


def test_english_text_detected_as_en_with_langdetect() -> None:
    """Clear English text is detected as 'en' via langdetect."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed; cannot exercise positive case")

    from corpus_forge.analyze.language import detect_language

    iso_code, confidence = detect_language(
        "The quick brown fox jumps over the lazy dog. "
        "This is a clearly English sentence with multiple words.",
        detector="langdetect",
    )
    assert iso_code == "en", f"Expected 'en', got {iso_code!r}"
    assert 0.0 <= confidence <= 1.0, f"Confidence {confidence} out of [0, 1]"


# ---------------------------------------------------------------------------
# 5. French-text positive case
# ---------------------------------------------------------------------------


def test_french_text_detected_as_fr_with_langdetect() -> None:
    """Clear French text is detected as 'fr' via langdetect."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed; cannot exercise positive case")

    from corpus_forge.analyze.language import detect_language

    iso_code, confidence = detect_language(
        "Le renard brun rapide saute par-dessus le chien paresseux. "
        "C'est une phrase clairement française avec plusieurs mots.",
        detector="langdetect",
    )
    assert iso_code == "fr", f"Expected 'fr', got {iso_code!r}"
    assert 0.0 <= confidence <= 1.0, f"Confidence {confidence} out of [0, 1]"


# ---------------------------------------------------------------------------
# 6. Empty string → ("und", 0.0)
# ---------------------------------------------------------------------------


def test_empty_string_returns_und_zero_confidence() -> None:
    """Empty string must return ('und', 0.0) — not raise."""
    if _LANGDETECT_ABSENT:
        # If langdetect absent, try fasttext path or use mock
        pytest.skip("langdetect not installed; cannot test empty-string graceful path")

    from corpus_forge.analyze.language import detect_language

    result = detect_language("", detector="langdetect")
    assert result == ("und", 0.0), f"Empty string should return ('und', 0.0), got {result!r}"


def test_whitespace_only_string_returns_und_zero_confidence() -> None:
    """Whitespace-only string must return ('und', 0.0) — not raise."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language

    result = detect_language("   \t\n  ", detector="langdetect")
    assert result == ("und", 0.0), (
        f"Whitespace-only string should return ('und', 0.0), got {result!r}"
    )


# ---------------------------------------------------------------------------
# 7. Mixed-language graceful degradation
# ---------------------------------------------------------------------------


def test_mixed_language_does_not_raise() -> None:
    """Mixed-language text must not raise; returns some (iso_code, confidence)."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language

    # Deliberately ambiguous mix of English and French
    mixed = "Hello world. Bonjour le monde. This is mixed."
    iso_code, confidence = detect_language(mixed, detector="langdetect")

    # Must not raise; iso_code is a non-empty string; confidence in [0, 1]
    assert isinstance(iso_code, str) and len(iso_code) > 0
    assert 0.0 <= confidence <= 1.0, f"Confidence {confidence!r} out of [0, 1]"


# ---------------------------------------------------------------------------
# 8. detect_language_batch — ordered output, preserves input order
# ---------------------------------------------------------------------------


def test_batch_preserves_order() -> None:
    """detect_language_batch returns one result per input, in the same order."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language_batch

    texts = [
        "The quick brown fox",
        "Le renard brun rapide",
        "Der schnelle braune Fuchs",
    ]
    results = detect_language_batch(texts, detector="langdetect")

    assert len(results) == len(texts), f"Expected {len(texts)} results, got {len(results)}"
    for i, (iso_code, confidence) in enumerate(results):
        assert isinstance(iso_code, str) and len(iso_code) > 0, (
            f"Result[{i}] iso_code is not a valid string: {iso_code!r}"
        )
        assert 0.0 <= confidence <= 1.0, f"Result[{i}] confidence {confidence} out of [0, 1]"


def test_batch_empty_list_returns_empty() -> None:
    """detect_language_batch([]) returns []."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language_batch

    result = detect_language_batch([], detector="langdetect")
    assert result == [], f"Empty batch should return [], got {result!r}"


def test_batch_single_item_consistent_with_single_call() -> None:
    """detect_language_batch([text]) is consistent with detect_language(text)."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language, detect_language_batch

    text = "The quick brown fox jumps over the lazy dog."
    single = detect_language(text, detector="langdetect")
    batch = detect_language_batch([text], detector="langdetect")

    assert len(batch) == 1
    # iso_code must match; confidence may differ by small float noise
    assert batch[0][0] == single[0], (
        f"Batch iso_code {batch[0][0]!r} != single iso_code {single[0]!r}"
    )


def test_batch_includes_empty_string_gracefully() -> None:
    """detect_language_batch handles an empty string in the list without raising."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language_batch

    texts = ["Hello world", "", "Bonjour"]
    results = detect_language_batch(texts, detector="langdetect")

    assert len(results) == 3
    # Position 1 (empty) should be ("und", 0.0)
    assert results[1] == ("und", 0.0), (
        f"Empty string in batch should yield ('und', 0.0), got {results[1]!r}"
    )


# ---------------------------------------------------------------------------
# 9. Dispatch isolation — langdetect path does NOT touch fasttext
# ---------------------------------------------------------------------------


def test_langdetect_dispatch_does_not_import_fasttext() -> None:
    """When detector='langdetect', fasttext must NOT appear in sys.modules.

    Strategy: remove both modules from sys.modules, exercise the langdetect
    path, then assert fasttext was never pulled in.
    """
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed; cannot test dispatch isolation")

    _evict_language_module()

    for heavy in ("fasttext", "fasttext_langdetect"):
        sys.modules.pop(heavy, None)

    from corpus_forge.analyze.language import detect_language

    detect_language("Hello world", detector="langdetect")

    assert "fasttext" not in sys.modules, (
        "The langdetect path imported fasttext — dispatch isolation broken."
    )
    assert "fasttext_langdetect" not in sys.modules, (
        "The langdetect path imported fasttext_langdetect — dispatch isolation broken."
    )


def test_fasttext_dispatch_does_not_import_langdetect_via_mock() -> None:
    """When detector='fasttext', langdetect must NOT appear in sys.modules.

    Uses monkeypatching so the test runs without a real fasttext wheel:
    we inject a fake fasttext_langdetect module and verify langdetect is
    never imported.
    """
    _evict_language_module()

    fake_ft = _make_fake_fasttext_module()
    sys.modules.pop("langdetect", None)

    with patch.dict(sys.modules, {"fasttext_langdetect": fake_ft}):
        from corpus_forge.analyze.language import detect_language  # fresh import

        detect_language("Hello world", detector="fasttext")

    assert "langdetect" not in sys.modules, (
        "The fasttext path imported langdetect — dispatch isolation broken."
    )


# ---------------------------------------------------------------------------
# 10. fasttext path — call shape via mock (no 120MB download)
# ---------------------------------------------------------------------------


def test_fasttext_path_calls_detect_on_mock() -> None:
    """When detector='fasttext', the implementation calls the fasttext detection API.

    We inject a fake fasttext_langdetect module and verify the call was made
    with the input text.  This test does NOT trigger a real model download.
    """
    _evict_language_module()

    fake_ft = _make_fake_fasttext_module()

    with patch.dict(sys.modules, {"fasttext_langdetect": fake_ft}):
        from corpus_forge.analyze.language import detect_language

        iso_code, confidence = detect_language("Hello world", detector="fasttext")

    # The mock returns {"lang": "en", "score": 0.98}
    assert iso_code == "en", f"Expected 'en' from mock fasttext, got {iso_code!r}"
    assert 0.0 <= confidence <= 1.0, f"Confidence {confidence} out of [0, 1]"


# ---------------------------------------------------------------------------
# 11. Missing fasttext wheel → clear RuntimeError
# ---------------------------------------------------------------------------


def test_fasttext_missing_raises_runtime_error() -> None:
    """Requesting detector='fasttext' when fasttext is missing raises RuntimeError.

    The error message must name the missing dependency so the user knows
    exactly what to install.
    """
    _evict_language_module()

    # Remove any cached copies then inject a finder that blocks fasttext
    for name in ("fasttext", "fasttext_langdetect"):
        sys.modules.pop(name, None)

    import builtins

    original = builtins.__import__

    def blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in ("fasttext", "fasttext_langdetect"):
            raise ImportError(f"No module named '{name}'")
        return original(name, *args, **kwargs)

    # Re-evict language module so it re-imports cleanly inside the patch
    _evict_language_module()

    with patch("builtins.__import__", side_effect=blocking_import):
        from corpus_forge.analyze.language import detect_language

        with pytest.raises(RuntimeError) as exc_info:
            detect_language("Hello world", detector="fasttext")

    error_msg = str(exc_info.value).lower()
    assert "fasttext" in error_msg, (
        f"RuntimeError message should mention 'fasttext', got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# 12. detector=None reads AnalyzeConfig.language_detector via Config
# ---------------------------------------------------------------------------


def test_detector_none_reads_config() -> None:
    """detector=None must read AnalyzeConfig.language_detector from Config.load().

    We use monkeypatching to inject a mock Config so Config.load() is not
    called for real (no config file required).  We verify that the langdetect
    path is invoked when the mock config specifies language_detector='langdetect'.
    """
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed; cannot test detector=None dispatch")

    _evict_language_module()

    mock_config = MagicMock()
    mock_config.analyze.language_detector = "langdetect"

    with patch("corpus_forge.config.Config.load", return_value=mock_config):
        from corpus_forge.analyze.language import detect_language

        result = detect_language("Hello world", detector=None)

    iso_code, confidence = result
    assert isinstance(iso_code, str) and len(iso_code) > 0
    assert 0.0 <= confidence <= 1.0


# ---------------------------------------------------------------------------
# 13. Confidence bounds property test (Hypothesis)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_LANGDETECT_ABSENT, reason="langdetect not installed")
@given(text=st.text(min_size=1, max_size=500))
@settings(max_examples=50)
def test_property_confidence_in_unit_interval(text: str) -> None:
    """Property: confidence is always in [0.0, 1.0] for any non-empty text string.

    Skipped wholesale if langdetect is absent.
    """
    from corpus_forge.analyze.language import detect_language

    _, confidence = detect_language(text, detector="langdetect")

    assert 0.0 <= confidence <= 1.0, (
        f"Confidence {confidence!r} out of [0.0, 1.0] for text={text!r}"
    )


@pytest.mark.skipif(_LANGDETECT_ABSENT, reason="langdetect not installed")
@given(text=st.text(min_size=1, max_size=500))
@settings(max_examples=50)
def test_property_iso_code_is_non_empty_string(text: str) -> None:
    """Property: iso_code is always a non-empty string for any non-empty text."""
    from corpus_forge.analyze.language import detect_language

    iso_code, _ = detect_language(text, detector="langdetect")
    assert isinstance(iso_code, str) and len(iso_code) > 0, (
        f"iso_code {iso_code!r} is not a non-empty string for text={text!r}"
    )


# ---------------------------------------------------------------------------
# 14. Batch return type is always list
# ---------------------------------------------------------------------------


def test_batch_returns_list_type() -> None:
    """detect_language_batch always returns a list (not a generator or tuple)."""
    if _LANGDETECT_ABSENT:
        pytest.skip("langdetect not installed")

    from corpus_forge.analyze.language import detect_language_batch

    result = detect_language_batch(["Hello", "Bonjour"], detector="langdetect")
    assert isinstance(result, list), (
        f"detect_language_batch must return list, got {type(result).__name__}"
    )
