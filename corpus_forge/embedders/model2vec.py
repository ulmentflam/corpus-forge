"""Phase N Wave 3 — static-embedding fast-tier embedder.

Wraps :class:`model2vec.StaticModel` (lazy-loaded on first encode) so
the optional ``[fast-tier]`` extra installs the dependency only when
the user opts in.  Default model: ``minishlab/potion-code-16M``
(256-dim, MIT, ~16 MB, code-focused; CPU-fast — sub-millisecond per
query in benchmarks).

The fast tier is wired into :class:`~corpus_forge.retrieval.retriever.HybridRetriever`
as an optional candidate generator (``fast_tier_mode`` ∈
``{skip, shortcut, only}``).  See ``.planning/tdd/phase_n_retrieval_quality.md``
Wave 3 section for the architectural sketch.

Module-import safety
--------------------

The top-level ``import model2vec`` is wrapped in a ``try / except
ImportError`` so importing this module never crashes on a minimal
install.  The ``MODEL2VEC_AVAILABLE`` flag is used by lazy-load and
warmup to early-return without doing anything; ``encode()`` raises a
clear ``ImportError`` pointing at the ``[fast-tier]`` extra so the
user gets one focused error message rather than a fan-out of
attribute crashes deeper in the call stack.

Cross-cutting
-------------

The fingerprint module (``corpus_forge.embedders.fingerprint``)
silently skips embedders that haven't been registered with the
backend yet — adding ``"model2vec"`` to the dispatch dictionary does
NOT trip drift detection on the user's main embedder.  Confirmed in
:func:`compare_active`'s ``find_embedder_row_by_name(...) is None``
guard.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

try:
    # pyrefly: ignore[missing-import]  # optional dep, install via [fast-tier] extra
    import model2vec  # noqa: F401  (used inside _load_static_model)

    MODEL2VEC_AVAILABLE = True
except ImportError:
    MODEL2VEC_AVAILABLE = False

from .base import BaseEmbedder

# Greppable logger name — mirrors
# ``corpus_forge.embedders.loader`` from the sentence-transformers
# embedder.  INFO on load start / ready; DEBUG-only chatter elsewhere.
loader_logger = logging.getLogger("corpus_forge.embedders.loader")


def _load_static_model(model_id: str) -> Any:
    """Import ``model2vec`` lazily and return ``StaticModel.from_pretrained``.

    Hoisted into a module-level function (rather than inlined into
    :meth:`Model2VecEmbedder._load_model`) so unit tests can patch
    *the loader* without monkey-patching the embedder instance.

    Raises:
        ImportError: if the ``[fast-tier]`` extra wasn't installed.
            The error message names the extra so the user gets one
            focused remediation hint.
    """
    if not MODEL2VEC_AVAILABLE:
        raise ImportError(
            "The 'model2vec' package is required for the fast-tier embedder. "
            "Install it via: pip install 'corpus-forge[fast-tier]' "
            "(or `uv tool install 'corpus-forge[fast-tier]'`)."
        )
    # Lazy import — referenced here so the symbol is resolved only
    # when we're sure the package is present.
    # pyrefly: ignore[missing-import]
    from model2vec import StaticModel  # noqa: PLC0415

    return StaticModel.from_pretrained(model_id)


class Model2VecEmbedder(BaseEmbedder):
    """Static-embedding fast-tier embedder.

    Args:
        name: registry-side name (``[[embedders]] name``).
        model_id: HuggingFace model id, e.g. ``"minishlab/potion-code-16M"``.
        dimension: vector dimension.  Must match the model's actual
            output (256 for potion-code-16M; the embedder guards
            against silent corruption by raising on mismatch in
            :meth:`encode`).
        normalized: forwarded to the metadata field.  StaticModel
            already L2-normalises its vectors, so this defaults
            ``True`` and changing it is uncommon.
        distance: similarity metric label.  Default ``"cosine"``.

    The model is loaded lazily on the first ``encode`` / ``warmup``
    call so constructing the embedder is cheap (does not touch the
    network).  Constructors round-trip on machines missing the
    ``[fast-tier]`` extra — only ``encode()`` raises in that case.
    """

    def __init__(
        self,
        name: str,
        model_id: str,
        dimension: int,
        normalized: bool = True,
        distance: str = "cosine",
        **_unused_kwargs: Any,
    ):
        super().__init__(
            name=name,
            provider="model2vec",
            model_id=model_id,
            dimension=dimension,
            normalized=normalized,
            distance=distance,
        )
        # ``_unused_kwargs`` keeps the registry's generic
        # ``embedder_class(name=..., **kwargs)`` dispatch tolerant of
        # callers that pass through fields that don't apply to static
        # tier (``batch_size`` / ``device`` / ``api_key_env`` etc.).
        # We intentionally ignore them — static embedders are
        # CPU-fixed and require no auth.
        self._model: Any | None = None

    # ── lazy load ─────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Lazy load the StaticModel.  Silently no-ops when the extra is missing."""
        if self._model is not None:
            return
        if not MODEL2VEC_AVAILABLE:
            return
        loader_logger.info(
            "Loading embedder %s (model2vec, %d-dim, model_id=%s)",
            self.name,
            self.dimension,
            self.model_id,
        )
        started = time.perf_counter()
        self._model = _load_static_model(self.model_id)
        loader_logger.info(
            "Embedder %s ready in %.1fs",
            self.name,
            time.perf_counter() - started,
        )

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Load the model + run a single dummy encode.

        No-op when the ``[fast-tier]`` extra isn't installed — matches
        :class:`SentenceTransformersEmbedder.warmup` so a doctor /
        introspection path doesn't crash on a minimal install.
        """
        self._load_model()
        if self._model is not None:
            # Dummy encode primes any internal caches.
            self._model.encode(["warmup"])

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode texts via the StaticModel.

        Raises:
            ImportError: when the ``[fast-tier]`` extra isn't installed.
                The message names the extra so the user gets one
                focused hint instead of an opaque ``AttributeError``
                from ``self._model.encode``.
            ValueError: when the underlying StaticModel's output
                dimension disagrees with ``self.dimension``.  Guards
                against a misconfigured ``[[embedders]]`` block
                writing bogus-shaped vectors into the table.
        """
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        # If a model handle was already attached (tests inject a fake;
        # production warms via :meth:`warmup`), use it directly without
        # consulting MODEL2VEC_AVAILABLE — the caller has taken
        # responsibility for the dependency.
        if self._model is None:
            if not MODEL2VEC_AVAILABLE:
                raise ImportError(
                    "The 'model2vec' package is required for the fast-tier "
                    "embedder. Install it via: pip install "
                    "'corpus-forge[fast-tier]' (or `uv tool install "
                    "'corpus-forge[fast-tier]'`)."
                )
            self._load_model()
            if self._model is None:
                # Defensive — should not happen post-_load_model when
                # MODEL2VEC_AVAILABLE is True.
                raise RuntimeError("Failed to load Model2Vec StaticModel")

        # The unused ``batch_size`` arg is part of the Embedder
        # protocol; StaticModel doesn't honour it (the model is
        # CPU-static and processes the whole list at once).  Kept
        # for protocol parity.
        _ = batch_size

        embeddings = self._model.encode(list(texts))
        embeddings = np.asarray(embeddings, dtype=np.float32)

        # StaticModel returns a 2-D ndarray ``(N_texts, dim)``.  Any
        # other shape is a misconfigured upstream model.
        _EXPECTED_NDIM = 2
        if embeddings.ndim != _EXPECTED_NDIM or embeddings.shape[1] != self.dimension:
            got = embeddings.shape[1] if embeddings.ndim == _EXPECTED_NDIM else "?"
            raise ValueError(
                f"Model2Vec model {self.model_id} produced embeddings of "
                f"dimension {got}, expected {self.dimension}."
            )

        return embeddings

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Query-path encode — symmetric, delegates straight to :meth:`encode`.

        model2vec / potion-code-16M is documented as a symmetric model
        (same projection for query and document text), so no
        instruction prompt or asymmetric path is needed.  This
        override is explicit so the call shape is grep-able and the
        symmetric guarantee is pinned in unit tests.
        """
        return self.encode(texts, batch_size=batch_size)


__all__ = ["MODEL2VEC_AVAILABLE", "Model2VecEmbedder"]
