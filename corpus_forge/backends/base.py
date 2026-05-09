"""Storage backend protocol for corpus-forge."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np

    from corpus_forge.embedders.base import Embedder
    from corpus_forge.sources.base import RawConversation, RawDocument


class StorageBackend(Protocol):
    """Pluggable storage backend. Implementations live behind this Protocol."""

    def migrate(self) -> None: ...

    def register_embedder(self, embedder: "Embedder") -> int: ...

    def upsert_document(
        self, dataset_id: int, doc: "RawDocument", chunks: list[tuple[str | None, str]]
    ) -> int: ...

    def upsert_conversation(
        self,
        dataset_id: int,
        conv: "RawConversation",
        chunked_messages: list[list[tuple[str | None, str]]],
    ) -> int: ...

    def write_embeddings(self, embedder_id: int, pairs: list[tuple[int, "np.ndarray"]]) -> None: ...

    def chunks_missing_embedding(
        self, embedder_id: int, limit: int = 1024
    ) -> Iterator[tuple[int, str]]: ...

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
