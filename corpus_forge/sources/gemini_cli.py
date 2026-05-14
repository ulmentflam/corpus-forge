"""Gemini CLI source plugin."""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .base import RawConversation, RawMessage, WatchedSource


def _parse_ts(ts_value: object) -> float | None:
    """Convert an ISO-8601 timestamp string to Unix float, or None."""
    if ts_value is None:
        return None
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    try:
        return datetime.fromisoformat(str(ts_value)).timestamp()
    except (ValueError, TypeError):
        return None


class GeminiCLISource(WatchedSource):
    """Gemini CLI chat source.

    Reads JSON files from ~/.gemini/tmp/<projectHash>/chats/*.json.
    Each file is a JSON list of {role, content, timestamp?} objects where
    role='model' is mapped to 'assistant'.
    """

    name = "gemini_cli"
    dataset_kind = "chat"
    _session_link_client: str = "gemini-cli"

    def __init__(self, projects_root: Path = Path("~/.gemini/tmp"), **kwargs):
        super().__init__(projects_root.expanduser(), **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield *.json files from <projects_root>/<projectHash>/chats/."""
        for project_dir in self.root.iterdir():
            if not project_dir.is_dir():
                continue
            chats_dir = project_dir / "chats"
            if chats_dir.is_dir():
                yield from chats_dir.glob("*.json")

    def parse(self, path: Path) -> RawConversation | None:
        """Parse a Gemini CLI chat JSON file into RawConversation."""
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        try:
            data = json.loads(raw_bytes)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, list):
            return None

        messages: list[RawMessage] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role", "user")
            if role == "model":
                role = "assistant"
            content = str(entry.get("content", ""))
            ts = _parse_ts(entry.get("timestamp"))
            if ts is None:
                ts = path.stat().st_mtime
            messages.append(
                RawMessage(
                    external_uuid=None,
                    parent_uuid=None,
                    role=role,
                    content=content,
                    tool_calls=None,
                    tool_results=None,
                    ts=ts,
                    metadata={},
                )
            )

        if not messages:
            return None

        return RawConversation(
            source_uri=f"gemini-cli://{path}",
            external_id=path.stem,
            content_hash=content_hash,
            title=None,
            started_at=messages[0].ts,
            ended_at=messages[-1].ts,
            messages=messages,
            metadata={},
            labels=[],
        )
