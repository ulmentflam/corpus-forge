"""Document-classification subsystem — Phase E.

The package exposes:

- :class:`~corpus_forge.classifiers.base.ClassifiableDocument` /
  :class:`~corpus_forge.classifiers.base.ClassLabel` /
  :class:`~corpus_forge.classifiers.base.Classifier` (the seam).
- :class:`~corpus_forge.classifiers.registry.ClassifierRegistry`
  (ordered dispatch).
- :func:`register_default_classifiers` — boot hook that lazy-loads each
  classifier named in :attr:`ClassifierConfig.chain` so optional
  backends (P1's LLM classifier) don't get pulled in at config-load
  time when the chain doesn't include them.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from .base import (
    ALLOWED_CLASS_VALUES,
    ClassifiableDocument,
    Classifier,
    ClassLabel,
)
from .registry import ClassifierRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Map classifier ``name`` → ``(submodule, class_name)``. New classifiers
# (P1's ``LLMClassifier``) land by adding an entry here and the matching
# module. Keeping this dispatcher tiny lets the boot hook lazy-load only
# the chain the user configured.
_CLASSIFIER_REGISTRY: dict[str, tuple[str, str]] = {
    "rule": ("rule_based", "RuleBasedClassifier"),
    "llm": ("llm", "LLMClassifier"),
}


def _load_classifier(name: str) -> Classifier:
    """Lazy-import ``corpus_forge.classifiers.<submodule>.<class_name>``."""
    if name not in _CLASSIFIER_REGISTRY:
        raise ValueError(
            f"unknown classifier name: {name!r}. Known: {sorted(_CLASSIFIER_REGISTRY)}"
        )
    submodule, class_name = _CLASSIFIER_REGISTRY[name]
    try:
        module = importlib.import_module(f"corpus_forge.classifiers.{submodule}")
    except ImportError as exc:
        # P0 ships only the rule classifier; ``llm`` lazy-import will
        # fire here until P1 lands ``corpus_forge.classifiers.llm``.
        raise ValueError(
            f"unknown classifier name: {name!r} (module "
            f"corpus_forge.classifiers.{submodule} is not available: {exc})"
        ) from exc
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(
            f"unknown classifier name: {name!r} (class {class_name} not "
            f"found in corpus_forge.classifiers.{submodule})"
        )
    return cls()


def register_default_classifiers(config: object | None) -> ClassifierRegistry:
    """Construct a :class:`ClassifierRegistry` from ``config``.

    ``config`` is duck-typed against the ``chain`` attribute exposed by
    :class:`corpus_forge.config.ClassifierConfig`. When ``config`` is
    ``None``, the default chain ``["rule"]`` is used so callers can
    construct a working registry without first loading config (handy
    for unit tests and one-off scripts).

    Raises ``ValueError`` if any name in the chain is not a known
    classifier or its module fails to import — callers should treat
    this as a config error, not an environmental one.
    """
    if config is None:
        chain: list[str] = ["rule"]
    else:
        chain = list(getattr(config, "chain", ["rule"]))

    reg = ClassifierRegistry()
    for name in chain:
        reg.register(_load_classifier(name))
    return reg


__all__ = [
    "ALLOWED_CLASS_VALUES",
    "ClassLabel",
    "ClassifiableDocument",
    "Classifier",
    "ClassifierRegistry",
    "register_default_classifiers",
]
