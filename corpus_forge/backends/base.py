"""Storage backend protocol for corpus-forge."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

    from corpus_forge.embedders.base import Embedder
    from corpus_forge.retrieval.types import Hit
    from corpus_forge.sources.base import RawConversation, RawDocument


class StorageBackend(Protocol):
    """Pluggable storage backend. Implementations live behind this Protocol."""

    def migrate(self) -> None: ...

    def register_embedder(self, embedder: "Embedder") -> int: ...

    def upsert_document(
        self,
        dataset_id: int,
        doc: "RawDocument",
        chunks: list[Any],
        embedder_ids: "list[int] | None" = None,
    ) -> int:
        """Insert or update a document and its chunks.

        Phase D housekeeping (HK-2): ``chunks`` accepts either
        :class:`~corpus_forge.chunkers.base.TextChunk` instances
        (preferred — carries ``metadata``, ``role``, ``token_count``) or
        the legacy ``(heading, text)`` 2-tuple shape used by older tests
        and ``tests/smoke``. Implementations coerce on the way in.

        Typed as ``list[Any]`` so the invariant-``list`` mismatch between
        the Protocol and the per-backend implementations stays out of
        the type checker's way; both backends document the accepted
        shapes in their own docstrings.
        """
        ...

    def find_document(self, dataset_id: int, source_uri: str) -> "dict | None": ...

    def upsert_conversation(
        self,
        dataset_id: int,
        conv: "RawConversation",
        chunked_messages: list[list[Any]],
    ) -> int:
        """Insert or update a conversation and its messages/chunks.

        ``chunked_messages`` accepts either :class:`TextChunk` instances
        or the legacy ``(heading, text)`` 2-tuple shape, same as
        :meth:`upsert_document`.
        """
        ...

    def write_embeddings(self, embedder_id: int, pairs: list[tuple[int, "np.ndarray"]]) -> None: ...

    def chunks_missing_embedding(
        self, embedder_id: int, limit: int = 1024
    ) -> Iterator[tuple[int, str]]: ...

    # --- Multi-modal embedding surface (Phase G P1) ----------------------------

    def register_multimodal_embedder(
        self,
        *,
        name: str,
        model_id: str,
        dimension: int,
    ) -> int:
        """Register a multi-modal embedder and create its ``image_embeddings_<name>`` table.

        Mirrors :meth:`register_embedder` but flags the row as
        ``image=TRUE`` (Phase G migration 0011 added the column) and
        provisions a parallel ``image_embeddings_<name>`` table rather
        than ``embeddings_<name>``.
        """
        ...

    def write_image_embeddings(
        self,
        embedder_id: int,
        pairs: "list[tuple[int, list[float]]]",
    ) -> None:
        """Write image embeddings for chunks (Phase G P1).

        Same shape as :meth:`write_embeddings` but operates on the
        ``image_embeddings_<name>`` table and accepts plain
        ``list[float]`` vectors (no numpy dependency required at this
        boundary — the image-embed pipeline produces lists directly).
        """
        ...

    def image_chunks_missing_embedding(
        self, embedder_id: int, *, limit: int = 1024
    ) -> "Iterator[tuple[int, dict]]":
        """Return image-labeled chunks missing an embedding for ``embedder_id``.

        Yields ``(chunk_id, metadata_dict)`` so the caller can read
        ``metadata["image_uri"]`` (or however image bytes are sourced)
        from the dict rather than re-querying the chunks table.

        Filter: only chunks whose document carries ``format=image`` are
        considered (image-extractor output).
        """
        ...

    def lock_source(
        self, key: str
    ) -> "AbstractContextManager[None]": ...  # Context manager for advisory lock

    def delete_document(self, dataset_id: int, source_uri: str) -> None: ...

    def delete_conversation(self, dataset_id: int, source_uri: str) -> None: ...

    def resolve_document(self, dataset_id: int, source_uri: str) -> "dict | None": ...

    def resolve_self_source(self, dataset_id: int, host: str) -> int: ...

    def insert_revision(
        self,
        *,
        document_id: int,
        source_uri: str,
        content_hash: str,
        text: str,
        parent_revision_id: "int | None",
        author_host: str,
        is_tombstone: bool,
        metadata: "dict | None" = None,
    ) -> dict: ...

    def latest_revision(self, document_id: int) -> "dict | None": ...

    def pending_remote_revisions(
        self,
        dataset_id: int,
        last_pulled_revision_id: "int | None",
        self_host: str,
        *,
        limit: int = 1024,
    ) -> "list[dict]": ...

    def mark_revision_pulled(self, source_id: int, revision_id: int) -> None: ...

    def set_tombstone(self, document_id: int) -> None: ...

    def clear_tombstone(self, document_id: int) -> None: ...

    def get_or_create_dataset(self, name: str, kind: str, description: str) -> int: ...

    def find_dataset_id_by_name(self, name: str) -> "int | None": ...

    def register_source(self, dataset_id: int, plugin: str, identity: str, host: str) -> int: ...

    # --- Retrieval surface (Phase R1) ----------------------------------------

    def search_dense(
        self,
        embedder_id: int,
        query_vector: "np.ndarray",
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> "list[Hit]": ...

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> "list[Hit]": ...

    def get_chunk(self, chunk_id: int) -> "dict | None": ...

    def get_chunk_by_content_hash(self, content_hash: str) -> "dict | None": ...

    def get_document_chunk_texts(self, document_id: int) -> "list[str]":
        """Return the texts of all chunks attached to ``document_id`` in order.

        Phase F (F-04): used by ``corpus-forge rechunk`` to compare the
        prospective new chunk-text list against the stored chunk-text
        list and skip the upsert when they match (idempotency check).

        Returns an empty list when the document has no chunks.
        """
        ...

    def get_document_chunk_metadatas(self, document_id: int) -> "list[dict]":
        """Return the metadata dicts of all chunks attached to ``document_id``.

        Phase F (F-04): used by the ``rechunk`` CLI idempotency check to
        detect when stored chunks lack the expected chunker signature
        (e.g. ``cdc_fingerprint`` for prose classes), in which case the
        rechunk pass runs even if the chunk text happens to match.
        """
        ...

    def replace_document_chunks(
        self,
        document_id: int,
        chunks: list[Any],
        embedder_ids: "list[int] | None" = None,
    ) -> int:
        """Replace the chunks of a document with ``chunks``, content-hash-aware.

        Phase F (F-04): used by the ``rechunk`` CLI. Mirrors the
        ``content_hash`` chunk-reuse path inside :meth:`upsert_document`
        WITHOUT touching the document row. Embedding rows for chunks
        whose ``content_hash`` survives the rechunk are preserved
        in-place (Phase C BUG-3).
        """
        ...

    def list_datasets(self) -> "list[dict]": ...

    def backfill_lexical_index(self) -> int: ...

    # --- Classification surface (Phase E) ----------------------------------

    # --- Code-enrichment surface (Phase H) ---------------------------------

    def iter_code_chunks_for_enrichment(
        self,
        model_tag: str,
        dataset_id: "int | None" = None,
    ) -> "Iterator[tuple[int, Any, str]]":
        """Yield ``(chunk_id, TextChunk, language)`` for code chunks to enrich.

        Iterates chunks whose parent document carries ``class=code``
        (the Phase E classifier output) AND whose
        ``metadata.enrichment.model`` does NOT equal ``model_tag``.
        That second filter is the idempotency guard — chunks already
        enriched with the current model tag are skipped so re-running
        the CLI is cheap.

        ``language`` resolution order (first non-empty wins):
        1. ``chunks.metadata.language`` (CodeChunker stamps this).
        2. ``documents.language`` document-label
           (``namespace='language'``).
        3. The string ``"unknown"`` as a final fallback.
        """
        ...

    def update_chunk_enrichment(
        self,
        chunk_id: int,
        enrichment: Any,
    ) -> None:
        """Merge ``enrichment.to_metadata()`` into ``chunks.metadata.enrichment``.

        Preserves existing chunk-metadata fields (``kind``, ``name``,
        ``byte_range``, ``cdc_fingerprint``) by writing only the
        ``enrichment`` sub-key — other keys are left untouched.
        """
        ...

    def iter_documents_for_classification(
        self,
        dataset_id: "int | None" = None,
        *,
        include_classified: bool = False,
    ) -> "Iterator[Any]":
        """Yield :class:`ClassifiableDocument` rows for the classifier chain.

        Read-only iterator joining ``documents`` to
        ``document_labels`` / ``labels`` so the caller sees the
        already-attached structural labels (``format``, ``language``,
        ``extractor``).

        Args:
            dataset_id: Restrict to a single dataset. ``None`` iterates
                every dataset.
            include_classified: When ``False`` (default), skip documents
                that already carry a ``namespace='class'`` label whose
                ``source LIKE 'classifier:%'``. User-attached class
                labels (``source='user'``) do NOT block iteration —
                the classifier writes its own source-distinct row.
        """
        ...
