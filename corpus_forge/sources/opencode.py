"""OpenCode source plugin."""

import json
from collections.abc import Iterator
from pathlib import Path

from ._flatten import flatten_message
from .base import RawConversation, RawMessage, WatchedSource


class OpenCodeSource(WatchedSource):
    """OpenCode chat source; subclasses WatchedSource, uses _flatten.flatten_message."""

    name = "opencode"
    dataset_kind = "chat"

    def __init__(self, storage_root: Path, **kwargs):
        super().__init__(storage_root, **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield message.json files from OpenCode storage."""
        # OpenCode storage structure: {session,message,part}/...
        # We want to find message.json files
        yield from self.root.rglob("message.json")

    def parse(self, path: Path) -> RawConversation:
        """Parse an OpenCode message file into RawConversation."""
        # In OpenCode, we need to reconstruct conversations from message and part files
        # This is a simplified version - real implementation would be more complex

        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)

            # Extract basic info
            external_uuid = data.get("id")
            parent_uuid = data.get("parentId")
            role = data.get("role", "assistant")

            # Flatten content from parts if present
            content = data.get("content", "")
            parts = data.get("parts", [])

            if parts:
                # Use flatten_message for parts
                flattened = flatten_message(parts, source="opencode")
                content = flattened.content
            else:
                content = str(content)

            # Build RawMessage
            message = RawMessage(
                external_uuid=external_uuid,
                parent_uuid=parent_uuid,
                role=role,
                content=content,
                tool_calls=None,  # Would extract if present
                tool_results=None,  # Would extract if present
                ts=data.get("timestamp"),
                metadata={},
            )

            # Create a minimal conversation with this single message
            # Real implementation would group messages by session
            return RawConversation(
                source_uri=(
                    f"opencode://{path.parent.parent.name}"
                    f"/{path.parent.name}/{external_uuid or 'unknown'}"
                ),
                external_id=external_uuid,
                content_hash="",  # Would compute from raw data
                title=None,
                started_at=message.ts,
                ended_at=message.ts,
                messages=[message],
                metadata={},
                labels=[],
            )

        except (json.JSONDecodeError, KeyError):
            # Return empty conversation on error
            return RawConversation(
                source_uri=f"opencode://error/{path.name}",
                external_id=None,
                content_hash="",
                title=None,
                started_at=None,
                ended_at=None,
                messages=[],
                metadata={},
                labels=[],
            )
