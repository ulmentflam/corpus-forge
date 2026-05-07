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
