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


def _load_classifier(name: str, config: object | None = None) -> Classifier:
    """Lazy-import ``corpus_forge.classifiers.<submodule>.<class_name>``.

    Phase E P1 (C-10/11): when ``name == "llm"``, LLM-relevant fields
    are forwarded from ``config`` to :class:`LLMClassifier`. Missing
    fields fall back to the class's own defaults so duck-typed configs
    (unit tests) still work.
    """
    if name not in _CLASSIFIER_REGISTRY:
        raise ValueError(
            f"unknown classifier name: {name!r}. Known: {sorted(_CLASSIFIER_REGISTRY)}"
        )
    submodule, class_name = _CLASSIFIER_REGISTRY[name]
    try:
        module = importlib.import_module(f"corpus_forge.classifiers.{submodule}")
    except ImportError as exc:
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

    # Forward LLM-relevant kwargs from config. The rule classifier
    # takes no args; the LLM classifier reads model / url / api_key /
    # timeout / temperature / excerpt_chars from the config block.
    if name == "llm" and config is not None:
        import os  # noqa: PLC0415

        kwargs: dict[str, object] = {}
        for cfg_attr, kwarg in (
            ("llm_model", "model"),
            ("llm_url", "llm_url"),
            ("llm_timeout_s", "timeout_s"),
            ("llm_temperature", "temperature"),
            ("llm_excerpt_chars", "excerpt_chars"),
        ):
            if hasattr(config, cfg_attr):
                value = getattr(config, cfg_attr)
                # AnyHttpUrl is not a plain str — cast it so the
                # backend's ``rstrip('/')`` works.
                if kwarg == "llm_url":
                    value = str(value)
                kwargs[kwarg] = value
        # Optional bearer token. Empty env-var name (default) means
        # "open local Ollama"; setting it to e.g. ``OPENAI_API_KEY``
        # swaps the same backend onto a hosted authenticated endpoint.
        env_name = getattr(config, "llm_api_key_env", "") or ""
        if env_name:
            api_key = os.environ.get(env_name)
            if api_key:
                kwargs["api_key"] = api_key
        return cls(**kwargs)

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
        reg.register(_load_classifier(name, config))
    return reg


__all__ = [
    "ALLOWED_CLASS_VALUES",
    "ClassLabel",
    "ClassifiableDocument",
    "Classifier",
    "ClassifierRegistry",
    "register_default_classifiers",
]
