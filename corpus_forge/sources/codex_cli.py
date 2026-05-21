"""OpenAI Codex CLI source plugin.

Reads JSONL session rollouts from ``~/.codex/sessions``. Recent Codex CLI
builds shard sessions under ``YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``,
so :func:`discover` walks recursively. Each line is one of:

- ``{role, content, ts}`` — legacy / minimal format still used by tests
  and some third-party tooling.
- ``{"type": "session_meta", "payload": {...}}`` — conversation header
  (id, cwd, cli_version, originator). Folded into ``RawConversation.metadata``.
- ``{"type": "event_msg", "payload": {"type": "user_message" |
  "agent_message", "message": "..."}}`` — chat turns.
- ``{"type": "response_item", "payload": {"type": "function_call" |
  "function_call_output", ...}}`` — tool calls and tool results,
  attached to the most recent assistant turn.

Anything else is skipped silently.
"""

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

    Walks ``~/.codex/sessions`` recursively so both the legacy flat layout
    and the modern ``YYYY/MM/DD/rollout-*.jsonl`` layout are picked up.
    """

    name = "codex_cli"
    dataset_kind = "chat"
    _session_link_client: str = "codex-cli"

    def __init__(
        self,
        sessions_root: Path | str = Path("~/.codex/sessions"),
        **kwargs,
    ):
        super().__init__(Path(sessions_root).expanduser(), **kwargs)

    def discover(self) -> Iterator[Path]:
        """Yield *.jsonl and *.json files anywhere under sessions_root."""
        if not self.root.exists():
            return
        yield from self.root.rglob("*.jsonl")
        yield from self.root.rglob("*.json")

    def parse(self, path: Path) -> RawConversation | None:
        """Parse a Codex CLI rollout into a RawConversation."""
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        messages: list[RawMessage] = []
        meta: dict[str, object] = {}
        external_id: str | None = None
        title: str | None = None

        for raw_line in raw_bytes.decode("utf-8", errors="replace").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            event_type = entry.get("type")
            payload = entry.get("payload")
            line_ts = _parse_ts(entry.get("ts") or entry.get("timestamp"))

            if event_type == "session_meta" and isinstance(payload, dict):
                if external_id is None and payload.get("id"):
                    external_id = str(payload["id"])
                for key in ("cwd", "originator", "cli_version", "instructions", "model"):
                    if payload.get(key) is not None:
                        meta[key] = payload[key]
                continue

            if event_type == "event_msg" and isinstance(payload, dict):
                ptype = payload.get("type")
                if ptype == "user_message":
                    messages.append(
                        RawMessage(
                            external_uuid=payload.get("id"),
                            parent_uuid=None,
                            role="user",
                            content=str(payload.get("message", "")),
                            tool_calls=None,
                            tool_results=None,
                            ts=line_ts,
                            metadata={},
                        )
                    )
                elif ptype == "agent_message":
                    messages.append(
                        RawMessage(
                            external_uuid=payload.get("id"),
                            parent_uuid=None,
                            role="assistant",
                            content=str(payload.get("message", "")),
                            tool_calls=None,
                            tool_results=None,
                            ts=line_ts,
                            metadata={},
                        )
                    )
                elif ptype in ("reasoning_summary", "agent_reasoning"):
                    summary = payload.get("summary") or payload.get("text") or ""
                    if summary:
                        messages.append(
                            RawMessage(
                                external_uuid=payload.get("id"),
                                parent_uuid=None,
                                role="assistant",
                                content=str(summary),
                                tool_calls=None,
                                tool_results=None,
                                ts=line_ts,
                                metadata={"subtype": "reasoning"},
                            )
                        )
                continue

            if event_type == "response_item" and isinstance(payload, dict):
                ptype = payload.get("type")
                if ptype == "function_call":
                    call = {
                        "id": payload.get("call_id") or payload.get("id"),
                        "name": payload.get("name"),
                        "input": payload.get("arguments"),
                    }
                    if messages and messages[-1].role == "assistant":
                        prior = messages[-1]
                        merged = [*list(prior.tool_calls or []), call]
                        messages[-1] = RawMessage(
                            external_uuid=prior.external_uuid,
                            parent_uuid=prior.parent_uuid,
                            role=prior.role,
                            content=prior.content,
                            tool_calls=merged,
                            tool_results=prior.tool_results,
                            ts=prior.ts,
                            metadata=prior.metadata,
                        )
                    else:
                        messages.append(
                            RawMessage(
                                external_uuid=call["id"],
                                parent_uuid=None,
                                role="assistant",
                                content="",
                                tool_calls=[call],
                                tool_results=None,
                                ts=line_ts,
                                metadata={},
                            )
                        )
                elif ptype == "function_call_output":
                    result = {
                        "tool_use_id": payload.get("call_id"),
                        "content": payload.get("output"),
                        "is_error": bool(payload.get("is_error", False)),
                    }
                    messages.append(
                        RawMessage(
                            external_uuid=None,
                            parent_uuid=None,
                            role="tool",
                            content=str(payload.get("output") or ""),
                            tool_calls=None,
                            tool_results=[result],
                            ts=line_ts,
                            metadata={},
                        )
                    )
                continue

            # Legacy / minimal format: {role, content, ts}.
            if "role" in entry:
                role = str(entry.get("role", "user"))
                content = str(entry.get("content", ""))
                ts = line_ts if line_ts is not None else path.stat().st_mtime
                messages.append(
                    RawMessage(
                        external_uuid=entry.get("id"),
                        parent_uuid=None,
                        role=role,
                        content=content,
                        tool_calls=entry.get("tool_calls"),
                        tool_results=entry.get("tool_results"),
                        ts=ts,
                        metadata={},
                    )
                )

        if not messages:
            return None

        if external_id is None:
            external_id = path.stem

        return RawConversation(
            source_uri=f"codex-cli://{path}",
            external_id=external_id,
            content_hash=content_hash,
            title=title,
            started_at=messages[0].ts,
            ended_at=messages[-1].ts,
            messages=messages,
            metadata=meta,
            labels=[],
        )
