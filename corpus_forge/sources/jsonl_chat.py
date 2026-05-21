"""Generic JSONL chat source plugin."""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import RawConversation, RawMessage, WatchedSource


def _parse_ts(ts_value: object) -> float | None:
    """Convert a timestamp to Unix float, or None."""
    if ts_value is None:
        return None
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    try:
        return datetime.fromisoformat(str(ts_value)).timestamp()
    except (ValueError, TypeError):
        return None


class JSONLChatSource(WatchedSource):
    """Generic JSONL chat source.

    Reads *.jsonl files from a directory (or a single file).
    Each line is expected to be {role, content, [tool_calls], [tool_results], [ts]}.
    """

    name = "jsonl_chat"
    dataset_kind = "chat"
    _session_link_client: str | None = None

    def __init__(self, path: Path | str, **kwargs):
        super().__init__(Path(path).expanduser(), **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield *.jsonl files from root (or root itself if it is a file)."""
        if self.root.is_file():
            yield self.root
        else:
            yield from self.root.glob("*.jsonl")

    def parse(self, path: Path) -> RawConversation | None:
        """Parse a JSONL chat file into RawConversation."""
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        messages: list[RawMessage] = []
        for raw_line in raw_bytes.decode("utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role", "user"))
            content = str(entry.get("content", ""))
            ts = _parse_ts(entry.get("ts") or entry.get("timestamp"))
            if ts is None:
                ts = path.stat().st_mtime
            tool_calls = entry.get("tool_calls") or None
            tool_results = entry.get("tool_results") or None
            messages.append(
                RawMessage(
                    external_uuid=entry.get("id"),
                    parent_uuid=None,
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    ts=ts,
                    metadata={},
                )
            )

        if not messages:
            return None

        return RawConversation(
            source_uri=f"jsonl-chat://{path}",
            external_id=path.stem,
            content_hash=content_hash,
            title=None,
            started_at=messages[0].ts,
            ended_at=messages[-1].ts,
            messages=messages,
            metadata={},
            labels=[],
        )
