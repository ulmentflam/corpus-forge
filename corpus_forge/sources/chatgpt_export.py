"""ChatGPT data-export source plugin."""

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


def _linearize_mapping(mapping: dict, current_node: str | None) -> list[dict]:
    """Walk the mapping tree from current_node to root, then reverse for chronological order.

    The ChatGPT export mapping is a dict of node_id -> node where each node has
    a 'parent' reference.  We follow current_node up through parents to build
    a path from root to leaf, then collect only user/assistant messages.
    """
    if not mapping or not current_node:
        return []

    # Trace ancestry: walk from current_node up to root
    path: list[str] = []
    visited: set[str] = set()
    node_id: str | None = current_node
    while node_id and node_id not in visited:
        visited.add(node_id)
        path.append(node_id)
        node = mapping.get(node_id)
        if node is None:
            break
        node_id = node.get("parent")

    # path is leaf→root; reverse to get root→leaf (chronological)
    path.reverse()

    messages: list[dict] = []
    for nid in path:
        node = mapping.get(nid)
        if node is None:
            continue
        msg = node.get("message")
        if not msg:
            continue
        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue
        # Content is a dict with "parts" list
        content_obj = msg.get("content", {})
        parts = content_obj.get("parts", []) if isinstance(content_obj, dict) else []
        text = " ".join(str(p) for p in parts if p)
        messages.append(
            {
                "role": role,
                "content": text,
                "ts": msg.get("create_time"),
            }
        )
    return messages


class ChatGPTExportSource(WatchedSource):
    """ChatGPT data-export source.

    Reads <export_root>/conversations.json — a JSON list of conversation objects
    each with a 'mapping' tree.  Messages are linearized by following the
    current_node/parent chain.
    """

    name = "chatgpt_export"
    dataset_kind = "chat"
    _session_link_client: str = "chatgpt-export"

    def __init__(self, export_root: Path, **kwargs):
        super().__init__(export_root, **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield the single conversations.json file if it exists."""
        candidate = self.root / "conversations.json"
        if candidate.is_file():
            yield candidate

    def parse(self, path: Path) -> RawConversation | None:
        """Parse a conversations.json export into a single RawConversation per call.

        Because the file may contain many conversations, this method is called once
        per conversations.json file and returns the first conversation found.
        For multi-conversation exports, callers should use scan() which yields
        one RawConversation per conversation object in the list.
        """
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        try:
            data = json.loads(raw_bytes)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, list) or not data:
            return None

        # Parse each conversation and return the first non-empty one
        for conv_obj in data:
            result = self._parse_conversation(conv_obj, path, content_hash)
            if result is not None:
                return result
        return None

    def _parse_conversation(
        self, conv_obj: dict, path: Path, content_hash: str
    ) -> RawConversation | None:
        """Parse a single conversation object from the export."""
        if not isinstance(conv_obj, dict):
            return None

        mapping = conv_obj.get("mapping", {})
        current_node = conv_obj.get("current_node")
        conv_id = conv_obj.get("id") or path.stem
        title = conv_obj.get("title")

        raw_messages = _linearize_mapping(mapping, current_node)
        if not raw_messages:
            return None

        messages: list[RawMessage] = []
        for entry in raw_messages:
            ts = _parse_ts(entry.get("ts"))
            if ts is None:
                ts = path.stat().st_mtime
            messages.append(
                RawMessage(
                    external_uuid=None,
                    parent_uuid=None,
                    role=entry["role"],
                    content=entry["content"],
                    tool_calls=None,
                    tool_results=None,
                    ts=ts,
                    metadata={},
                )
            )

        if not messages:
            return None

        return RawConversation(
            source_uri=f"chatgpt-export://{path}",
            external_id=str(conv_id),
            content_hash=content_hash,
            title=title,
            started_at=messages[0].ts,
            ended_at=messages[-1].ts,
            messages=messages,
            metadata={},
            labels=[],
        )

    def scan(self) -> Iterator[RawConversation]:  # type: ignore[override]
        """Override scan to yield one RawConversation per conversation object."""
        for path in self.discover():
            raw_bytes = path.read_bytes()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            try:
                data = json.loads(raw_bytes)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, list):
                continue
            for conv_obj in data:
                result = self._parse_conversation(conv_obj, path, content_hash)
                if result is not None:
                    yield result
