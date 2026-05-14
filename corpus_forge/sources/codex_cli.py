"""Codex CLI source plugin."""

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


class CodexCLISource(WatchedSource):
    """OpenAI Codex CLI session source.

    Reads JSONL files from ~/.codex/sessions/*.jsonl (also *.json).
    Each line is {role, content, ...}.
    """

    name = "codex_cli"
    dataset_kind = "chat"
    _session_link_client: str = "codex-cli"

    def __init__(self, sessions_root: Path = Path("~/.codex/sessions"), **kwargs):
        super().__init__(sessions_root.expanduser(), **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield *.json and *.jsonl files from sessions_root."""
        yield from self.root.glob("*.jsonl")
        yield from self.root.glob("*.json")

    def parse(self, path: Path) -> RawConversation | None:
        """Parse a Codex CLI session file (JSONL) into RawConversation."""
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
            messages.append(
                RawMessage(
                    external_uuid=entry.get("id"),
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
            source_uri=f"codex-cli://{path}",
            external_id=path.stem,
            content_hash=content_hash,
            title=None,
            started_at=messages[0].ts,
            ended_at=messages[-1].ts,
            messages=messages,
            metadata={},
            labels=[],
        )
