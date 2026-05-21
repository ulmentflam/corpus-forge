"""OpenCode source plugin.

OpenCode persists chats in a triple-store layout under
``<storage>/session``, ``<storage>/message``, ``<storage>/part``. The exact
shape has shifted across releases; the two we care about are:

- **Modern**: ``session/info/<sid>.json`` + ``session/message/<sid>/<mid>.json``
  + ``session/part/<sid>/<mid>/<pid>.json``. Reconstructable into a real
  multi-turn conversation.
- **Legacy / simplified** (used by existing tests): bare
  ``message/<mid>/message.json`` files containing a single message blob,
  parsed standalone.

:meth:`scan` first attempts the modern reconstruction; if no session info
files are found it falls through to the legacy per-file ``parse``.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ._flatten import flatten_message
from .base import RawConversation, RawMessage, WatchedSource


def _parse_ts(ts_value: object) -> float | None:
    if ts_value is None:
        return None
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    try:
        return datetime.fromisoformat(str(ts_value)).timestamp()
    except (ValueError, TypeError):
        return None


def _message_from_record(data: dict, parts: list[dict] | None = None) -> RawMessage:
    """Build a RawMessage from an OpenCode message record + optional parts."""
    role = str(data.get("role", "assistant"))
    raw_parts = parts if parts is not None else data.get("parts", [])

    if raw_parts:
        flat = flatten_message(raw_parts, source="opencode")
        content = flat.content
        tool_calls = flat.tool_calls
        tool_results = flat.tool_results
    else:
        content = str(data.get("content", ""))
        tool_calls = None
        tool_results = None

    return RawMessage(
        external_uuid=data.get("id"),
        parent_uuid=data.get("parentId") or data.get("parent_id"),
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_results=tool_results,
        ts=_parse_ts(data.get("timestamp") or data.get("createdAt")),
        metadata={},
    )


class OpenCodeSource(WatchedSource):
    """OpenCode chat source."""

    name = "opencode"
    dataset_kind = "chat"
    _session_link_client: str = "opencode"

    def __init__(self, storage_root: Path | str, **kwargs):
        super().__init__(Path(storage_root).expanduser(), **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield message.json files from OpenCode storage (legacy layout)."""
        if not self.root.exists():
            return
        yield from self.root.rglob("message.json")

    def parse(self, path: Path) -> RawConversation:
        """Parse a single OpenCode ``message.json`` into a one-message conversation.

        Kept for the legacy layout used by tests and OpenCode's older
        on-disk format. The modern session/message/part triple-store is
        handled in :meth:`scan`.
        """
        try:
            raw_bytes = path.read_bytes()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            data = json.loads(raw_bytes)
            external_uuid = data.get("id")
            message = _message_from_record(data)
            return RawConversation(
                source_uri=(
                    f"opencode://{path.parent.parent.name}"
                    f"/{path.parent.name}/{external_uuid or 'unknown'}"
                ),
                external_id=external_uuid,
                content_hash=content_hash,
                title=None,
                started_at=message.ts,
                ended_at=message.ts,
                messages=[message],
                metadata={},
                labels=[],
            )
        except (json.JSONDecodeError, KeyError):
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

    def scan(self) -> Iterator[RawConversation]:
        """Yield reconstructed conversations from the modern triple-store layout.

        When ``session/info/*.json`` exists, group messages and parts by
        session and emit one ``RawConversation`` per session. Otherwise
        fall back to the legacy per-file behaviour from the base class.
        """
        if not self.root.exists():
            return
        info_dir = self.root / "session" / "info"
        if info_dir.is_dir() and any(info_dir.glob("*.json")):
            yield from self._scan_triple_store(info_dir)
            return

        # Legacy layout — defer to the base class iterator.
        for path in self.discover():
            result = self.parse(path)
            if result is not None and result.messages:
                yield result

    def _scan_triple_store(self, info_dir: Path) -> Iterator[RawConversation]:
        message_dir = self.root / "session" / "message"
        part_dir = self.root / "session" / "part"

        for info_path in sorted(info_dir.glob("*.json")):
            try:
                info = json.loads(info_path.read_bytes())
            except json.JSONDecodeError:
                continue
            if not isinstance(info, dict):
                continue

            session_id = str(info.get("id") or info_path.stem)
            session_msg_dir = message_dir / session_id
            if not session_msg_dir.is_dir():
                continue

            messages: list[RawMessage] = []
            hash_input: list[bytes] = [info_path.read_bytes()]

            for msg_path in sorted(session_msg_dir.glob("*.json")):
                try:
                    raw = msg_path.read_bytes()
                    hash_input.append(raw)
                    msg_data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg_data, dict):
                    continue

                msg_id = str(msg_data.get("id") or msg_path.stem)
                parts_for_msg: list[dict] = []
                msg_part_dir = part_dir / session_id / msg_id
                if msg_part_dir.is_dir():
                    for part_path in sorted(msg_part_dir.glob("*.json")):
                        try:
                            part_raw = part_path.read_bytes()
                            hash_input.append(part_raw)
                            part_data = json.loads(part_raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(part_data, dict):
                            parts_for_msg.append(part_data)

                messages.append(_message_from_record(msg_data, parts=parts_for_msg))

            if not messages:
                continue

            content_hash = hashlib.sha256(b"".join(hash_input)).hexdigest()
            title = info.get("title")
            meta: dict[str, object] = {}
            for key in ("cwd", "model", "provider", "version"):
                if info.get(key) is not None:
                    meta[key] = info[key]

            yield RawConversation(
                source_uri=f"opencode://{session_id}",
                external_id=session_id,
                content_hash=content_hash,
                title=str(title) if title else None,
                started_at=messages[0].ts,
                ended_at=messages[-1].ts,
                messages=messages,
                metadata=meta,
                labels=[],
            )
