"""In-process llama.cpp embedder backend.

Why this exists
---------------

The ``provider = "openai"`` embedder talking to a local Ollama at
``:11434/v1`` returns HTTP 500 with the body
``failed to encode response: json: unsupported value: NaN`` for ~30 %
of Python-code chunks against ``qwen3-embedding:8b`` (maintainer's
2026-05-26 incident). Ollama's Go JSON encoder explicitly refuses
``NaN`` floats, so the entire HTTP response gets dropped on the
floor — even though the underlying model has *some* well-formed
rows in the response. Bisection-with-skip rescues ~70 % of the
batch but loses the rest.

Running ``llama.cpp`` in-process via ``llama-cpp-python`` sidesteps
Ollama's encoder entirely: we get the raw ``float32`` vectors from
the C library and never serialise them through JSON. NaNs that
were a transport artefact disappear; NaNs that are real model
output are caught by the same validators the OpenAI path uses
(empty fast-path, dim mismatch, row-count mismatch) and surface
as a clear ``ValueError`` instead of an opaque 500.

Module-import safety
--------------------

The top-level ``import llama_cpp`` is wrapped in ``try / except
ImportError`` so importing this module never crashes on a minimal
install (``pip install corpus-forge`` without the ``[llama-cpp]``
extra). The ``LLAMA_CPP_AVAILABLE`` flag is used by lazy-load and
warmup to early-return without doing anything; ``encode()`` raises
a clear ``ImportError`` pointing at the ``[llama-cpp]`` extra so
the user gets one focused error message rather than a fan-out of
attribute crashes deeper in the call stack.

GGUF resolution
---------------

The :func:`resolve_gguf_path` helper supports two ways to point the
embedder at the model file:

1. ``gguf_path = "/path/to/model.gguf"`` — explicit. Wins when set.
2. ``model_id = "<name>:<tag>"`` — Ollama auto-discover. We parse
   the Ollama manifest at
   ``<ollama_root>/manifests/registry.ollama.ai/library/<name>/<tag>``,
   find the layer whose ``mediaType`` is
   ``"application/vnd.ollama.image.model"``, and return the blob path
   at ``<ollama_root>/blobs/sha256-<digest>``.

This means a user who has already run ``ollama pull
qwen3-embedding:8b`` gets the embedder working out of the box — no
duplicate GGUF download. Setting ``gguf_path`` explicitly is the
escape hatch for users who pulled their GGUF from HuggingFace
directly or want to pin a specific quantisation.

Cross-cutting
-------------

The fingerprint module (``corpus_forge.embedders.fingerprint``)
silently skips embedders that haven't been registered with the
backend yet — adding ``"llama-cpp"`` to the registry's dispatch
dictionary does NOT trip drift detection on the user's main
embedder. Same contract as the ``model2vec`` fast-tier addition.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

try:
    # pyrefly: ignore[missing-import]  # optional dep, install via [llama-cpp] extra
    import llama_cpp  # noqa: F401  (referenced inside _load_llama_handle)

    LLAMA_CPP_AVAILABLE = True
except ImportError:
    LLAMA_CPP_AVAILABLE = False

from .base import BaseEmbedder

# Greppable logger name — mirrors ``corpus_forge.embedders.loader``
# from the sentence-transformers and model2vec embedders. INFO on
# load start / ready; DEBUG-only chatter elsewhere.
loader_logger = logging.getLogger("corpus_forge.embedders.loader")


# Ollama lays its on-disk model store out as:
#
#   ~/.ollama/models/
#   ├── blobs/
#   │   └── sha256-<digest>                  (the raw GGUF / safetensors blob)
#   └── manifests/
#       └── registry.ollama.ai/
#           └── library/
#               └── <name>/
#                   └── <tag>                (JSON manifest)
#
# The manifest's ``layers[]`` array enumerates the artifacts the
# image is composed of (model weights, template, params, license,
# …). The model weights layer is keyed by mediaType verbatim:
_OLLAMA_MODEL_LAYER_MEDIA_TYPE = "application/vnd.ollama.image.model"
_DEFAULT_OLLAMA_MODELS_ROOT = Path.home() / ".ollama" / "models"


def resolve_gguf_path(
    *,
    gguf_path: str | Path | None,
    model_id: str | None,
    ollama_root: Path | None = None,
) -> Path:
    """Return the on-disk GGUF path for ``LlamaCppEmbedder``.

    Resolution order:

    1. If ``gguf_path`` is set → ``Path(gguf_path).expanduser()``.
       Raises :class:`FileNotFoundError` (with the path quoted) when
       the file does not exist.

    2. Else, if ``model_id`` is parseable as ``"<name>:<tag>"`` and an
       Ollama manifest exists at
       ``<ollama_root>/manifests/registry.ollama.ai/library/<name>/<tag>``,
       parse the manifest and return the path of the layer whose
       ``mediaType == "application/vnd.ollama.image.model"``. Manifest
       digests are formatted ``"sha256:<hex>"``; the on-disk blob
       filename uses a dash (``sha256-<hex>``).

    3. Else, raise :class:`FileNotFoundError` whose message names BOTH
       knobs so the user knows where to look.

    Args:
        gguf_path: explicit GGUF file path. Wins when set.
        model_id: Ollama-style ``"<name>:<tag>"`` identifier. Used only
            when ``gguf_path`` is ``None``.
        ollama_root: test seam — production callers omit this so the
            default ``~/.ollama/models`` is used. Tests pass a
            ``tmp_path``-rooted manifest tree to exercise the
            auto-discover branch hermetically.

    Returns:
        Absolute :class:`pathlib.Path` to the GGUF file on disk.

    Raises:
        FileNotFoundError: when neither knob resolves to an existing file.
    """

    if gguf_path is not None:
        candidate = Path(gguf_path).expanduser()
        if not candidate.exists():
            raise FileNotFoundError(
                f"LlamaCppEmbedder gguf_path={candidate!s} does not exist. "
                "Either fix the path in [[embedders]] or set model_id to a "
                "tag you've already pulled via `ollama pull`."
            )
        return candidate

    # Auto-discover via Ollama manifest. Both ``model_id`` and a
    # parseable ``<name>:<tag>`` shape required.
    if model_id and ":" in model_id:
        name, _, tag = model_id.partition(":")
        if name and tag:
            root = ollama_root if ollama_root is not None else _DEFAULT_OLLAMA_MODELS_ROOT
            manifest_path = root / "manifests" / "registry.ollama.ai" / "library" / name / tag
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                except (json.JSONDecodeError, OSError) as exc:
                    raise FileNotFoundError(
                        f"LlamaCppEmbedder could not parse Ollama manifest "
                        f"{manifest_path!s} for model_id={model_id!r}: "
                        f"{exc!s}. Pass gguf_path=<absolute-path-to-GGUF> "
                        "to bypass auto-discover."
                    ) from exc
                for layer in manifest.get("layers", []):
                    if layer.get("mediaType") != _OLLAMA_MODEL_LAYER_MEDIA_TYPE:
                        continue
                    digest = str(layer.get("digest", ""))
                    if not digest.startswith("sha256:"):
                        continue
                    blob = root / "blobs" / f"sha256-{digest.split(':', 1)[1]}"
                    if blob.exists():
                        return blob
                    raise FileNotFoundError(
                        f"LlamaCppEmbedder Ollama manifest for "
                        f"model_id={model_id!r} pointed at blob {blob!s} "
                        "but the file is missing. Re-run "
                        f"`ollama pull {model_id}` to fetch it, or set "
                        "gguf_path to bypass auto-discover."
                    )
                # Manifest exists but has no model-layer with a sha256
                # digest — fall through to the both-missing error.

    raise FileNotFoundError(
        "LlamaCppEmbedder could not locate a GGUF file. Tried "
        f"gguf_path=None and model_id={model_id!r} (Ollama auto-discover). "
        "Fix: either set [[embedders]].gguf_path=<path/to/model.gguf>, or "
        f"set model_id to an Ollama tag you've pulled (`ollama pull {model_id or '<name>:<tag>'}`)."
    )


def _load_llama_handle(
    *,
    gguf_path: str | None,
    model_id: str | None,
    n_ctx: int,
    n_gpu_layers: int,
) -> Any:
    """Resolve the GGUF and construct a ``llama_cpp.Llama`` for embeddings.

    Hoisted into a module-level function (rather than inlined into
    :meth:`LlamaCppEmbedder._load_model`) so unit tests can patch
    *the loader* via ``patch.object(mod, "_load_llama_handle",
    return_value=fake)`` without monkey-patching the embedder
    instance OR having to write a real GGUF onto disk. Mirrors the
    ``_load_static_model`` seam in ``corpus_forge.embedders.model2vec``.

    The GGUF resolution (Ollama auto-discover vs explicit path)
    happens here — keeping it inside the patched seam means tests
    that swap in a fake ``Llama`` handle don't need to stand up a
    fake GGUF file on disk.

    Raises:
        ImportError: when ``llama_cpp`` is not installed. The message
            names the ``[llama-cpp]`` extra.
        FileNotFoundError: when :func:`resolve_gguf_path` cannot locate
            a GGUF (propagated unchanged).
    """
    if not LLAMA_CPP_AVAILABLE:
        raise ImportError(
            "The 'llama-cpp-python' package is required for the llama-cpp "
            "embedder. Install via: pip install 'corpus-forge[llama-cpp]' "
            "(or `uv tool install 'corpus-forge[llama-cpp]'`). For Metal "
            "GPU offload on Apple Silicon, prefix with "
            'CMAKE_ARGS="-DGGML_METAL=on".'
        )
    # Lazy import — resolved only when LLAMA_CPP_AVAILABLE is True.
    # pyrefly: ignore[missing-import]
    from llama_cpp import Llama  # noqa: PLC0415

    path = resolve_gguf_path(gguf_path=gguf_path, model_id=model_id)
    return Llama(
        model_path=str(path),
        embedding=True,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        verbose=False,
    )


class LlamaCppEmbedder(BaseEmbedder):
    """In-process embedder powered by ``llama-cpp-python``.

    Args:
        name: registry-side name (``[[embedders]] name``).
        model_id: Ollama-style ``"<name>:<tag>"`` identifier. Used by
            :func:`resolve_gguf_path` for auto-discover when
            ``gguf_path`` is unset; otherwise informational only.
        dimension: vector dimension. Must equal the GGUF's native
            embedding width — llama-cpp-python does not support
            Matryoshka truncation, so a mismatch raises in :meth:`encode`.
        normalized: whether to L2-normalise rows on the way out.
            Defaults ``True`` (matches the corpus-forge cosine search
            convention). Set ``False`` only when you intend to compose
            with another normalisation stage downstream.
        distance: similarity metric label. Default ``"cosine"``.
        gguf_path: explicit GGUF file path. Wins over Ollama auto-discover.
        n_ctx: llama.cpp context window. Default ``512``; raise it
            when chunks routinely exceed that (qwen3-embedding's
            native context is 32 K).
        n_gpu_layers: number of layers to offload to GPU. ``-1``
            means "all" (Metal on Apple Silicon, CUDA on Linux).
            ``0`` forces CPU-only.
        batch_size: ``encode`` mini-batch size. Default ``32``;
            tune up for higher throughput on a beefier GPU.
        **_unused_kwargs: tolerated so the registry's generic
            ``embedder_class(name=..., **kwargs)`` dispatch can pass
            through fields that don't apply here (``device``,
            ``api_key_env``, ``base_url`` …).

    The model is loaded lazily on the first :meth:`encode` /
    :meth:`warmup` call so constructing the embedder is cheap (does
    not touch disk). Constructors round-trip on machines missing the
    ``[llama-cpp]`` extra — only :meth:`encode` raises in that case.

    Notes on asymmetry
    ------------------
    ``qwen3-embedding`` is documented asymmetric — the official
    instruction prompt for query-side encoding differs from the
    document side. This first cut ships :meth:`encode_query` as a
    pure delegate to :meth:`encode` (symmetric), matching the
    minimum-viable model2vec contract. A follow-up wires the
    Qwen3 query instruction prompt the same way
    :class:`~corpus_forge.embedders.sentence_transformers.SentenceTransformersEmbedder`
    does.
    """

    def __init__(
        self,
        name: str,
        model_id: str,
        dimension: int,
        normalized: bool = True,
        distance: str = "cosine",
        gguf_path: str | None = None,
        n_ctx: int = 512,
        n_gpu_layers: int = -1,
        batch_size: int = 32,
        **_unused_kwargs: Any,
    ):
        super().__init__(
            name=name,
            provider="llama-cpp",
            model_id=model_id,
            dimension=dimension,
            normalized=normalized,
            distance=distance,
        )
        # ``_unused_kwargs`` keeps the registry's generic dispatch
        # tolerant of callers that pass-through fields that don't apply
        # to llama-cpp (``device``, ``api_key_env``, ``base_url`` etc.).
        self.gguf_path = gguf_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.batch_size = batch_size
        self._llama: Any | None = None

    # ── lazy load ─────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Lazy load the ``Llama`` handle. Silently no-ops when the extra is missing."""
        if self._llama is not None:
            return
        if not LLAMA_CPP_AVAILABLE:
            return
        loader_logger.info(
            "Loading embedder %s (llama-cpp, %d-dim, model_id=%s, "
            "gguf_path=%s, n_ctx=%d, n_gpu_layers=%d)",
            self.name,
            self.dimension,
            self.model_id,
            self.gguf_path or "<auto-discover>",
            self.n_ctx,
            self.n_gpu_layers,
        )
        started = time.perf_counter()
        # ``_load_llama_handle`` resolves the GGUF internally so unit
        # tests can patch this seam without standing up a fake GGUF
        # on disk.
        self._llama = _load_llama_handle(
            gguf_path=self.gguf_path,
            model_id=self.model_id,
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
        )
        loader_logger.info(
            "Embedder %s ready in %.1fs",
            self.name,
            time.perf_counter() - started,
        )

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Load the model + run a single dummy encode.

        No-op when the ``[llama-cpp]`` extra isn't installed — matches
        :class:`SentenceTransformersEmbedder.warmup` /
        :class:`Model2VecEmbedder.warmup` so a doctor / introspection
        path doesn't crash on a minimal install.
        """
        self._load_model()
        if self._llama is not None:
            # Dummy encode primes any internal caches.
            self.encode(["warmup"])

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode texts into ``(N, dim)`` float32 embeddings.

        The fast-path for empty input returns ``(0, dim)`` without
        touching the underlying model — mirrors the model2vec /
        sentence-transformers behaviour so doctor / introspection
        calls with empty inputs don't load the GGUF.

        Raises:
            ImportError: when the ``[llama-cpp]`` extra isn't installed.
                The message names the extra.
            ValueError: when the underlying GGUF emits a row count or
                dimension that disagrees with the configured shape.
        """
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        # If a model handle was already attached (tests inject a fake;
        # production warms via :meth:`warmup`), use it directly without
        # consulting LLAMA_CPP_AVAILABLE — the caller has taken
        # responsibility for the dependency.
        if self._llama is None:
            if not LLAMA_CPP_AVAILABLE:
                raise ImportError(
                    "The 'llama-cpp-python' package is required for the "
                    "llama-cpp embedder. Install via: pip install "
                    "'corpus-forge[llama-cpp]' (or `uv tool install "
                    "'corpus-forge[llama-cpp]'`). For Metal GPU offload "
                    'on Apple Silicon, prefix with CMAKE_ARGS="-DGGML_METAL=on".'
                )
            self._load_model()
            if self._llama is None:
                # Defensive — should not happen post-_load_model when
                # LLAMA_CPP_AVAILABLE is True.
                raise RuntimeError("Failed to load llama_cpp.Llama handle")

        # Honour the instance batch size when the caller doesn't override.
        # ``32`` is the protocol-level default, so a literal-32 caller
        # gets the config-driven value instead.
        _PROTOCOL_DEFAULT_BATCH_SIZE = 32
        actual_batch_size = (
            batch_size if batch_size != _PROTOCOL_DEFAULT_BATCH_SIZE else self.batch_size
        )

        texts_list = list(texts)
        all_rows: list[list[float]] = []
        for start in range(0, len(texts_list), actual_batch_size):
            batch = texts_list[start : start + actual_batch_size]
            response = self._llama.create_embedding(batch)
            data = response.get("data", []) if isinstance(response, dict) else []
            if len(data) != len(batch):
                raise ValueError(
                    f"LlamaCppEmbedder row-count mismatch: requested "
                    f"{len(batch)} embeddings, got {len(data)} rows. This is a "
                    "llama-cpp-python contract violation; report upstream."
                )
            for item in data:
                vec = item["embedding"] if isinstance(item, dict) else item.embedding  # type: ignore[union-attr]
                all_rows.append(list(vec))

        embeddings = np.asarray(all_rows, dtype=np.float32)

        # Dim guard — llama.cpp returns the GGUF's native width verbatim
        # (no Matryoshka truncation), so a mismatch is a misconfigured
        # ``[[embedders]] dimension`` field.
        _EXPECTED_NDIM = 2
        if embeddings.ndim != _EXPECTED_NDIM or embeddings.shape[1] != self.dimension:
            got = embeddings.shape[1] if embeddings.ndim == _EXPECTED_NDIM else "?"
            raise ValueError(
                f"LlamaCppEmbedder model {self.model_id!r} produced embeddings "
                f"of dimension {got}, expected {self.dimension}. Fix the "
                "[[embedders]].dimension field to match the GGUF's native width."
            )

        if self.normalized:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-12)
            embeddings = embeddings / norms

        return embeddings

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Query-path encode — symmetric, delegates to :meth:`encode`.

        First-cut: no instruction-prompt prefix. ``qwen3-embedding``
        IS documented asymmetric, but the prompt override lands in a
        follow-up (parallel to
        :meth:`SentenceTransformersEmbedder.encode_query`'s Qwen3
        override). Until then we ride on symmetric encoding, which
        is the baseline contract for every other embedder corpus-forge
        ships.
        """
        return self.encode(texts, batch_size=batch_size)


__all__ = ["LLAMA_CPP_AVAILABLE", "LlamaCppEmbedder", "resolve_gguf_path"]
