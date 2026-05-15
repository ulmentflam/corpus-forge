"""Ordered :class:`Classifier` registry — Phase E / Wave 0 — C-01.

The registry is an ordered list (``list[Classifier]``); duplicate names
are de-duplicated last-write-wins so users can swap in a custom
classifier by re-registering it after the defaults. Dispatch walks the
list, returns the first result that clears ``threshold``, and falls back
to the last non-``None`` result if no classifier ever clears the bar
(better to give callers a low-confidence answer than no answer at all
when the bar happens to be misconfigured).

This mirrors :mod:`corpus_forge.extractors.registry` in spirit — a flat,
ordered table with a single dispatch entry-point and a `register_default_*`
boot hook that lazy-loads optional sub-modules so heavy deps stay out of
the import graph.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import Classifier

if TYPE_CHECKING:
    from .base import ClassifiableDocument, ClassLabel

logger = logging.getLogger(__name__)


class ClassifierRegistry:
    """Ordered :class:`Classifier` registry with threshold-based dispatch.

    Dispatch semantics (see :meth:`classify`):

    1. Walk classifiers in registration order.
    2. Each yields ``None`` (skip) or a :class:`ClassLabel`.
    3. The first non-``None`` result whose ``confidence >= threshold``
       wins and the walk short-circuits.
    4. If every label is below threshold, the *last* non-``None`` label
       is returned (so the caller still gets a label to act on).
    5. If every classifier returns ``None``, dispatch returns ``None``.
    """

    def __init__(self) -> None:
        self._classifiers: list[Classifier] = []

    def register(self, classifier: Classifier) -> None:
        """Append ``classifier`` to the chain.

        Last-write-wins on ``name`` collisions: any prior classifier
        with the same ``name`` is removed before append so registering
        a custom version after :func:`register_default_classifiers`
        cleanly overrides the default.
        """
        name = classifier.name
        # Remove any prior entry with the same name (last-write-wins).
        self._classifiers = [c for c in self._classifiers if c.name != name]
        self._classifiers.append(classifier)

    def classify(self, doc: ClassifiableDocument, threshold: float = 0.4) -> ClassLabel | None:
        """Walk the chain and return the best :class:`ClassLabel` (or ``None``).

        Args:
            doc: The document to classify.
            threshold: Confidence floor. The first classifier output
                whose ``confidence >= threshold`` wins outright. Default
                ``0.4`` mirrors the plan's escalation threshold; the CLI
                pulls it from ``ClassifierConfig.escalation_threshold``
                so a user can tune it without changing code.

        Returns:
            The winning :class:`ClassLabel`, or ``None`` if every
            classifier returned ``None``.
        """
        last_seen: ClassLabel | None = None
        for clf in self._classifiers:
            result = clf.classify(doc)
            if result is None:
                continue
            last_seen = result
            if result.confidence >= threshold:
                return result
        # No classifier cleared the bar; return the last non-None
        # result so the caller can still write *something* to
        # ``document_labels``. The source field will still distinguish
        # ``classifier:rule`` from ``classifier:llm`` and the audit log
        # captures the original confidence.
        return last_seen

    def names(self) -> list[str]:
        """Return classifier names in registration order."""
        return [c.name for c in self._classifiers]

    def __len__(self) -> int:
        return len(self._classifiers)

    def get(self, name: str) -> Classifier | None:
        """Return the classifier with this ``name``, or ``None``."""
        for c in self._classifiers:
            if c.name == name:
                return c
        return None
