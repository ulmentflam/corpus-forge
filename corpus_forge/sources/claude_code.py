"""Claude Code source plugin."""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ._flatten import flatten_message
from .base import RawConversation, RawMessage, WatchedSource


def _parse_ts(ts_value: object) -> float | None:
    """Convert a Claude Code timestamp to a Unix float, or None if absent/unparseable.

    Claude Code session files store timestamps as ISO-8601 strings
    (e.g. "2026-05-14T07:34:58.573552+00:00").  ``RawMessage.ts`` and
    ``RawConversation.started_at``/``ended_at`` are typed ``float | None``
    (Unix epoch seconds), so we must convert here.
    """
    if ts_value is None:
        return None
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    try:
        return datetime.fromisoformat(str(ts_value)).timestamp()
    except (ValueError, TypeError):
        return None


class ClaudeCodeSource(WatchedSource):
    """Claude Code chat source; subclasses WatchedSource, uses _flatten.flatten_message."""

    name = "claude_code"
    dataset_kind = "chat"
    _session_link_client: str = "claude-code"

    def __init__(self, projects_root: Path, include_subagents: bool = True, **kwargs):
        super().__init__(projects_root, **kwargs)
        self.include_subagents = include_subagents

    def discover(self) -> Iterator[Path]:
        """Yield session.jsonl files from Claude Code projects."""
        # Look for .jsonl files in project directories
        for project_dir in self.root.iterdir():
            if project_dir.is_dir():
                # Skip subagents if not included
                if not self.include_subagents and project_dir.name.startswith("_"):
                    continue

                # Look for session files
                yield from project_dir.glob("*.jsonl")

    def parse(self, path: Path) -> RawConversation:
        """Parse a Claude Code session file into RawConversation."""
        messages = []
        content_hash_parts = []

        with path.open(encoding="utf-8") as f:
            for _line_num, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)

                    # Extract message content
                    content = ""
                    role = "assistant"  # default

                    if "message" in data:
                        msg = data["message"]
                        role = msg.get("role", "assistant")

                        # Handle different content formats
                        if isinstance(msg.get("content"), str):
                            content = msg["content"]
                        elif isinstance(msg.get("content"), list):
                            # Use flatten_message for list content
                            flattened = flatten_message(msg["content"], source="claude_code")
                            content = flattened.content
                        else:
                            content = str(msg.get("content", ""))

                    # Build RawMessage
                    message = RawMessage(
                        external_uuid=data.get("uuid"),
                        parent_uuid=data.get("parentUuid"),
                        role=role,
                        content=content,
                        tool_calls=None,  # Would extract from message if needed
                        tool_results=None,  # Would extract from message if needed
                        ts=_parse_ts(data.get("timestamp")),
                        metadata={},
                    )
                    messages.append(message)
                    content_hash_parts.append(line)

                except json.JSONDecodeError:
                    # Skip malformed lines but log in real implementation
                    continue

        # Compute content hash from all lines
        content_hash = ""
        if content_hash_parts:
            content_hash = hashlib.sha256("\n".join(content_hash_parts).encode("utf-8")).hexdigest()

        return RawConversation(
            source_uri=f"claude-code://{path.parent.name}/{path.stem}",
            external_id=path.stem,  # session ID
            content_hash=content_hash,
            title=None,  # Would extract from first message or metadata
            started_at=messages[0].ts if messages else None,
            ended_at=messages[-1].ts if messages else None,
            messages=messages,
            metadata={},
            labels=[],
        )
