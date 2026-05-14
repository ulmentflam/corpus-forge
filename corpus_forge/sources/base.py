"""Source protocol and base classes for corpus-forge."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RawDocument:
    """Raw document from a text source."""

    source_uri: str
    content_hash: str
    text: str
    title: str | None
    modified_at: float
    metadata: dict
    labels: list[tuple[str, str]]  # [(namespace, value), ...]


@dataclass(frozen=True)
class RawMessage:
    """Raw message from a chat source."""

    external_uuid: str | None
    parent_uuid: str | None
    role: str
    content: str
    tool_calls: list | None
    tool_results: list | None
    ts: float | None
    metadata: dict


@dataclass(frozen=True)
class RawConversation:
    """Raw conversation from a chat source."""

    source_uri: str
    external_id: str | None
    content_hash: str
    title: str | None
    started_at: float | None
    ended_at: float | None
    messages: list[RawMessage]
    metadata: dict
    labels: list[tuple[str, str]]  # [(namespace, value), ...]


class Source(Protocol):
    """Pluggable ingestion source. Implementations live behind this Protocol."""

    name: str
    dataset_kind: str  # 'text' | 'chat'

    def scan(self) -> Iterator[RawDocument | RawConversation]: ...
    def watch(self, on_event) -> None: ...
    def identity(self) -> str: ...


class WatchedSource(ABC):
    """
    DRY base — every concrete Source subclasses this and overrides
    only `discover()` and `parse(path)`. The base owns watchdog wiring,
    debounce, identity, and content-hash short-circuit so the three
    ported plugins stay tiny.
    """

    name: str
    dataset_kind: str

    def __init__(self, root: Path, debounce: float = 2.0):
        self.root = root
        self.debounce = debounce
        # In a real implementation, we'd set up watchdog observer here

    def scan(self) -> Iterator[RawDocument | RawConversation]:
        for path in self.discover():
            result = self.parse(path)
            if result is not None:
                yield result

    def watch(self, on_event) -> None:  # noqa: B027
        """Set up watchdog observer with debounce. Override in subclasses."""
        pass

    def file_content_hash(self, path: Path) -> str:
        """Compute content hash for a file."""
        # Import here to avoid circular dependencies
        from corpus_forge.identity import file_content_hash  # noqa: PLC0415

        return file_content_hash(path)

    def identity(self) -> str:
        """Canonical source URI root."""
        return str(self.root.resolve())

    @abstractmethod
    def discover(self) -> Iterator[Path]:
        """Yield paths to process."""

    @abstractmethod
    def parse(self, path: Path) -> RawDocument | RawConversation | None:
        """Parse a single file into RawDocument or RawConversation, or None to skip."""
