"""Known-embedder registry — per-model-family facts the ``[[embedders]]``
config can't infer on its own.

Today it answers one question: does a model family expect each input to be
terminated with an EOS/SEP token? The **nomic-embed** family (text + code)
is trained that way — its pooled sentence embedding assumes the terminator
is present. A serving GGUF that leaves ``tokenizer.ggml.add_eos_token``
unset silently drops it, so the embedding is pooled over a sequence missing
the token the model was trained to expect (a quiet retrieval-quality
regression, not an error). The fix is to append the terminator client-side;
this module only decides *whether* a given model wants that — the actual
append lives in the embedding transport (a later RFC item).

Matching is by a **normalized family token** so the same underlying model
resolves identically across every name it's served under. For example::

    manutic/nomic-embed-code:latest   (Ollama)
    nomic-embed-code:7b               (llama-cpp / GGUF tag)
    text-embedding-nomic-embed-code   (LM Studio / OpenAI shim)

all normalize to ``nomic-embed-code`` and resolve to the same registry
entry. That cross-transport agreement is exactly what
``rfc-fleet-6-model-identity-aliases`` shared-lane aliasing depends on: two
names pooled onto one claim lane are only interchangeable if both tokenize
the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "KnownEmbedder",
    "lookup_known_embedder",
    "normalize_model_id",
    "resolve_append_eos",
]


@dataclass(frozen=True)
class KnownEmbedder:
    """Registry facts for a model family that config alone can't supply.

    ``family`` is the normalized token matched against a normalized model
    id; ``append_eos`` is whether inputs for this family must be terminated
    with the model's EOS/SEP token client-side.
    """

    family: str
    append_eos: bool


# Families whose pooled embedding assumes a trailing EOS/SEP token. Ordered
# most-specific-first so ``nomic-embed-code`` wins over the generic
# ``nomic-embed`` for a code model id (the matcher returns the first hit).
_KNOWN_EMBEDDERS: tuple[KnownEmbedder, ...] = (
    KnownEmbedder("nomic-embed-code", append_eos=True),
    KnownEmbedder("nomic-embed-text", append_eos=True),
    KnownEmbedder("nomic-embed", append_eos=True),
)

# The LM Studio / OpenAI-shim naming convention prefixes the served model
# with ``text-embedding-``; strip it so the family token matches the
# Ollama / GGUF form.
_OPENAI_EMBED_PREFIX = "text-embedding-"


def normalize_model_id(model_id: str) -> str:
    """Reduce a served model id to its bare family token.

    Drops the registry/org prefix (``manutic/``), the ``:tag`` suffix
    (``:latest`` / ``:7b`` / ``:v1.5``), and the LM Studio ``text-embedding-``
    prefix, lowercasing throughout — so the names a single model is served
    under all collapse to the same token.
    """
    norm = model_id.strip().lower()
    norm = norm.rsplit("/", 1)[-1]  # drop registry/org prefix
    norm = norm.split(":", 1)[0]  # drop :tag
    if norm.startswith(_OPENAI_EMBED_PREFIX):
        norm = norm[len(_OPENAI_EMBED_PREFIX) :]
    return norm


def lookup_known_embedder(model_id: str) -> KnownEmbedder | None:
    """Return the registry entry whose family token is contained in the
    normalized ``model_id``, or ``None`` for an unknown model."""
    norm = normalize_model_id(model_id)
    for entry in _KNOWN_EMBEDDERS:
        if entry.family in norm:
            return entry
    return None


def resolve_append_eos(model_id: str, explicit: bool | None) -> bool:
    """Whether to append the EOS/SEP terminator for ``model_id``.

    Precedence: an explicit config flag wins; otherwise the known-model
    registry default; otherwise ``False`` — unknown models are left as-is,
    since we only append where we know the family was trained to expect it.
    """
    if explicit is not None:
        return explicit
    known = lookup_known_embedder(model_id)
    return known.append_eos if known is not None else False
