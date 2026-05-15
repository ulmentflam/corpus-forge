"""Unit tests for `ClassifiableDocument`, `ClassLabel`, `Classifier`
protocol, and `ClassifierRegistry`.

Phase E / Wave 0 — C-01.

Tests cover:
- Dataclass invariants on `ClassLabel` (value in 9-enum, confidence in
  [0, 1], frozen semantics).
- `Classifier` protocol is runtime-checkable.
- `ClassifierRegistry` ordered dispatch:
    * empty registry returns `None`
    * single-classifier chain returns its result
    * high-confidence early classifier short-circuits later ones
    * low-confidence early result is overridden by a later
      high-confidence result (escalation)
    * if every classifier scores below the threshold, the *last*
      non-None result is returned (so callers still get something)
    * `register` is last-write-wins on `name` collisions
- `register_default_classifiers` boots the rule classifier when
  ``config.chain == ["rule"]`` and raises `ValueError` on unknown names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import pytest

from corpus_forge.classifiers import register_default_classifiers
from corpus_forge.classifiers.base import (
    ALLOWED_CLASS_VALUES,
    ClassifiableDocument,
    Classifier,
    ClassLabel,
)
from corpus_forge.classifiers.registry import ClassifierRegistry

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FixedClassifier:
    """A test classifier that always returns a pre-baked label (or None)."""

    def __init__(self, name: str, result: ClassLabel | None) -> None:
        self.name = name
        self._result = result

    def classify(self, doc: ClassifiableDocument) -> ClassLabel | None:
        return self._result


def _doc(**overrides) -> ClassifiableDocument:
    """Minimal `ClassifiableDocument` with overrides."""
    defaults = {
        "document_id": 1,
        "source_uri": "file:///x/y.md",
        "title": "Title",
        "text": "body",
        "format_labels": [("format", "markdown")],
        "metadata": {},
    }
    defaults.update(overrides)
    return ClassifiableDocument(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dataclass invariants
# ---------------------------------------------------------------------------


class TestClassLabel:
    def test_holds_value_confidence_rationale(self) -> None:
        cl = ClassLabel(value="note", confidence=0.5, rationale="markdown default")
        assert cl.value == "note"
        assert cl.confidence == 0.5
        assert cl.rationale == "markdown default"

    def test_is_frozen(self) -> None:
        cl = ClassLabel(value="note", confidence=0.5, rationale="r")
        with pytest.raises((AttributeError, Exception)):
            cl.value = "code"  # type: ignore[misc]

    def test_rejects_unknown_class_value(self) -> None:
        with pytest.raises(ValueError):
            ClassLabel(value="not_a_class", confidence=0.5, rationale="r")

    def test_rejects_confidence_below_zero(self) -> None:
        with pytest.raises(ValueError):
            ClassLabel(value="note", confidence=-0.1, rationale="r")

    def test_rejects_confidence_above_one(self) -> None:
        with pytest.raises(ValueError):
            ClassLabel(value="note", confidence=1.1, rationale="r")

    def test_boundary_values_accepted(self) -> None:
        ClassLabel(value="note", confidence=0.0, rationale="r")
        ClassLabel(value="note", confidence=1.0, rationale="r")

    def test_all_nine_class_values_emittable(self) -> None:
        for v in (
            "code",
            "chat",
            "book",
            "textbook",
            "paper",
            "article",
            "reference",
            "note",
            "other",
        ):
            ClassLabel(value=v, confidence=0.5, rationale="r")

    def test_allowed_class_values_export(self) -> None:
        assert set(ALLOWED_CLASS_VALUES) == {
            "code",
            "chat",
            "book",
            "textbook",
            "paper",
            "article",
            "reference",
            "note",
            "other",
        }


class TestClassifiableDocument:
    def test_construction(self) -> None:
        d = _doc()
        assert d.document_id == 1
        assert d.source_uri == "file:///x/y.md"
        assert d.format_labels == [("format", "markdown")]

    def test_is_frozen(self) -> None:
        d = _doc()
        with pytest.raises((AttributeError, Exception)):
            d.source_uri = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Protocol — runtime-checkable
# ---------------------------------------------------------------------------


class TestClassifierProtocol:
    def test_fixed_classifier_is_a_classifier(self) -> None:
        c = _FixedClassifier("dummy", None)
        assert isinstance(c, Classifier)

    def test_object_missing_classify_is_not_a_classifier(self) -> None:
        class _Bad:
            name = "bad"

        assert not isinstance(_Bad(), Classifier)


# ---------------------------------------------------------------------------
# Registry — dispatch semantics
# ---------------------------------------------------------------------------


class TestClassifierRegistry:
    """Registry.classify now returns ``tuple[str, ClassLabel] | None`` —
    ``(winner_name, label)``. Phase E / Wave 3 (C-10/11) source-attribution fix.
    """

    def test_empty_registry_returns_none(self) -> None:
        reg = ClassifierRegistry()
        assert reg.classify(_doc()) is None

    def test_single_classifier_returns_its_result(self) -> None:
        reg = ClassifierRegistry()
        target = ClassLabel(value="note", confidence=0.9, rationale="r")
        reg.register(_FixedClassifier("a", target))
        out = reg.classify(_doc())
        assert out is not None
        winner, label = out
        assert winner == "a"
        assert label is target

    def test_high_confidence_short_circuits(self) -> None:
        """First classifier passes the threshold; second is never consulted."""
        reg = ClassifierRegistry()
        first = _FixedClassifier("first", ClassLabel(value="code", confidence=0.99, rationale="r"))
        # If the second is consulted it would return a different label that
        # the assertion below would catch.
        second = _FixedClassifier(
            "second", ClassLabel(value="other", confidence=0.99, rationale="r")
        )
        reg.register(first)
        reg.register(second)
        out = reg.classify(_doc(), threshold=0.5)
        assert out is not None
        winner, label = out
        assert winner == "first"
        assert label.value == "code"

    def test_low_confidence_escalates_to_next(self) -> None:
        """First classifier's confidence is below threshold; chain walks on."""
        reg = ClassifierRegistry()
        first = _FixedClassifier(
            "first", ClassLabel(value="note", confidence=0.2, rationale="weak")
        )
        second = _FixedClassifier(
            "second", ClassLabel(value="article", confidence=0.8, rationale="strong")
        )
        reg.register(first)
        reg.register(second)
        out = reg.classify(_doc(), threshold=0.5)
        assert out is not None
        winner, label = out
        assert winner == "second"
        assert label.value == "article"

    def test_all_below_threshold_returns_last_non_none(self) -> None:
        """Nobody clears the bar — return the last non-None result so the
        caller still gets something to act on (better than `None`).

        Winner name is the classifier that produced the last-seen label.
        """
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("a", ClassLabel("note", 0.2, "r1")))
        reg.register(_FixedClassifier("b", ClassLabel("article", 0.3, "r2")))
        out = reg.classify(_doc(), threshold=0.9)
        assert out is not None
        winner, label = out
        assert winner == "b"
        assert label.value == "article"

    def test_all_return_none(self) -> None:
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("a", None))
        reg.register(_FixedClassifier("b", None))
        assert reg.classify(_doc()) is None

    def test_none_then_strong_yields_strong(self) -> None:
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("a", None))
        reg.register(_FixedClassifier("b", ClassLabel("note", 0.99, "r")))
        out = reg.classify(_doc())
        assert out is not None
        winner, label = out
        assert winner == "b"
        assert label.value == "note"

    def test_winner_name_for_rule_above_threshold(self) -> None:
        """Explicit rule-vs-llm winner-name assertion (Phase E P1).

        Rule classifier returns 0.99; chain short-circuits before
        the LLM is consulted — winner_name == "rule".
        """
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("rule", ClassLabel("code", 0.99, "fast")))
        reg.register(_FixedClassifier("llm", ClassLabel("other", 0.5, "never seen")))
        out = reg.classify(_doc(), threshold=0.4)
        assert out is not None
        winner, label = out
        assert winner == "rule"
        assert label.value == "code"

    def test_winner_name_for_llm_after_rule_low_confidence(self) -> None:
        """Rule returned a weak signal; LLM clears threshold; winner == llm."""
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("rule", ClassLabel("other", 0.3, "uncertain")))
        reg.register(_FixedClassifier("llm", ClassLabel("article", 0.8, "blog post")))
        out = reg.classify(_doc(), threshold=0.4)
        assert out is not None
        winner, label = out
        assert winner == "llm"
        assert label.value == "article"

    def test_register_last_write_wins_on_name(self) -> None:
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("dup", ClassLabel("note", 0.99, "r1")))
        reg.register(_FixedClassifier("dup", ClassLabel("article", 0.99, "r2")))
        # Only one entry with name "dup" should remain.
        assert reg.names().count("dup") == 1
        out = reg.classify(_doc())
        assert out is not None
        winner, label = out
        assert winner == "dup"
        assert label.value == "article"

    def test_names_reflect_registration_order(self) -> None:
        reg = ClassifierRegistry()
        reg.register(_FixedClassifier("rule", None))
        reg.register(_FixedClassifier("llm", None))
        assert reg.names() == ["rule", "llm"]


# ---------------------------------------------------------------------------
# Default-classifiers boot hook
# ---------------------------------------------------------------------------


class TestRegisterDefaultClassifiers:
    def test_none_config_yields_rule_only(self) -> None:
        """Calling with ``config=None`` (no config object at all) still
        yields the rule-only chain for ad-hoc / scripted use."""
        reg = register_default_classifiers(None)
        assert reg.names() == ["rule"]

    def test_explicit_rule_chain(self) -> None:
        # Build a duck-typed config (don't depend on the pydantic model in
        # this test — keep the unit test boundary tight).
        class _Cfg:
            chain: ClassVar[list[str]] = ["rule"]

        reg = register_default_classifiers(_Cfg())
        assert reg.names() == ["rule"]

    def test_rule_then_llm_chain(self) -> None:
        """The P1 default chain (``[rule, llm]``) loads both classifiers."""

        class _Cfg:
            chain: ClassVar[list[str]] = ["rule", "llm"]

        reg = register_default_classifiers(_Cfg())
        assert reg.names() == ["rule", "llm"]

    def test_unknown_classifier_name_raises(self) -> None:
        class _Cfg:
            chain: ClassVar[list[str]] = ["definitely-not-a-classifier"]

        with pytest.raises(ValueError, match="unknown classifier"):
            register_default_classifiers(_Cfg())

    def test_llm_classifier_loads_with_default_fields(self) -> None:
        """P1: ``"llm"`` resolves to a constructed :class:`LLMClassifier`
        even when the config object only exposes ``chain`` (legacy
        callers / tests duck-type the config). The loader falls back to
        the LLMClassifier's own defaults for missing fields.
        """

        class _Cfg:
            chain: ClassVar[list[str]] = ["llm"]

        reg = register_default_classifiers(_Cfg())
        assert reg.names() == ["llm"]
        llm = reg.get("llm")
        assert llm is not None
        assert llm.name == "llm"
