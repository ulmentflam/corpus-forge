"""Claude Code source plugin.

Walks ``~/.claude/projects/<project-slug>/<session-uuid>.jsonl`` files. Each
line is one of a small set of typed events; only ``user`` / ``assistant`` /
``attachment`` carry message content, while ``ai-title`` / ``last-prompt`` /
``pr-link`` / ``permission-mode`` / ``file-history-snapshot`` are
conversation-level metadata. Tool calls and tool results inside
``message.content`` blocks are extracted via :func:`flatten_message` so
downstream code sees structured ``tool_calls`` / ``tool_results`` on the
``RawMessage`` rather than a string blob.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ._flatten import flatten_message
from .base import RawConversation, RawMessage, WatchedSource

# Lines whose ``type`` is one of these are real chat turns and become
# RawMessages. Everything else is conversation-level metadata.
_MESSAGE_TYPES: frozenset[str] = frozenset({"user", "assistant", "attachment"})


def _parse_ts(ts_value: object) -> float | None:
    """Convert a Claude Code timestamp to a Unix float, or None if absent/unparseable.

    Claude Code session files store timestamps as ISO-8601 strings
    (e.g. "2026-05-14T07:34:58.573552+00:00"). ``RawMessage.ts`` and
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


def _extract_attachment_content(attachment: object) -> str:
    """Render an ``attachment`` event's payload as plain text for the corpus."""
    if isinstance(attachment, dict):
        if "text" in attachment:
            return str(attachment["text"])
        kind = attachment.get("type", "attachment")
        path = attachment.get("path") or attachment.get("name") or ""
        return f"[{kind}] {path}".rstrip()
    return str(attachment) if attachment is not None else ""


class ClaudeCodeSource(WatchedSource):
    """Claude Code chat source.

    Reads ``<projects_root>/<project-slug>/*.jsonl`` files where each line
    is a typed JSON event (``user``/``assistant``/``attachment``/
    ``ai-title``/``last-prompt``/``pr-link``/``permission-mode``/
    ``file-history-snapshot``). Non-message types are folded into
    ``RawConversation.metadata`` so feedback signals (PR links, AI-assigned
    titles, permission-mode transitions) survive the round trip.
    """

    name = "claude_code"
    dataset_kind = "chat"
    _session_link_client: str = "claude-code"

    def __init__(
        self,
        projects_root: Path | str,
        include_subagents: bool = True,
        history_path: Path | str | None = None,
        **kwargs,
    ):
        super().__init__(Path(projects_root).expanduser(), **kwargs)
        self.include_subagents = include_subagents
        # Optional ``~/.claude/history.jsonl`` — the raw user-prompt log
        # Claude Code persists across sessions. Captured separately from
        # the per-session JSONLs because it represents the user's typed
        # input (incl. pasted content) without assistant turns.
        self.history_path = Path(history_path).expanduser() if history_path is not None else None

    def discover(self) -> Iterator[Path]:
        """Yield session.jsonl files from Claude Code projects."""
        if not self.root.is_dir():
            return
        for project_dir in self.root.iterdir():
            if not project_dir.is_dir():
                continue
            if not self.include_subagents and project_dir.name.startswith("_"):
                continue
            yield from project_dir.glob("*.jsonl")

    def parse(self, path: Path) -> RawConversation:
        """Parse a Claude Code session file into RawConversation."""
        messages: list[RawMessage] = []
        meta: dict[str, object] = {}
        title: str | None = None
        permission_modes: list[dict] = []
        pr_links: list[dict] = []
        snapshots: list[dict] = []
        session_id: str | None = None
        git_branch: str | None = None
        cwd: str | None = None
        version: str | None = None
        last_prompt: str | None = None

        content_hash_parts: list[str] = []

        with path.open(encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content_hash_parts.append(line)

                # Capture the first sessionId/cwd/gitBranch/version we see;
                # these don't change within a session.
                if session_id is None and data.get("sessionId"):
                    session_id = str(data["sessionId"])
                if cwd is None and data.get("cwd"):
                    cwd = str(data["cwd"])
                if git_branch is None and data.get("gitBranch"):
                    git_branch = str(data["gitBranch"])
                if version is None and data.get("version"):
                    version = str(data["version"])

                event_type = data.get("type")

                # Backward-compat: older fixtures and pre-typed sessions emit
                # only ``{"message": {"role": ..., "content": ...}}`` with no
                # ``type`` field. Treat those as ``user``/``assistant`` based
                # on ``message.role`` so we don't silently drop them.
                if event_type is None and isinstance(data.get("message"), dict):
                    inferred = data["message"].get("role")
                    if inferred in ("user", "assistant"):
                        event_type = inferred

                if event_type == "ai-title":
                    title = data.get("aiTitle") or title
                    continue
                if event_type == "last-prompt":
                    last_prompt = data.get("lastPrompt") or last_prompt
                    continue
                if event_type == "pr-link":
                    pr_links.append(
                        {
                            "number": data.get("prNumber"),
                            "repository": data.get("prRepository"),
                            "url": data.get("prUrl"),
                            "ts": _parse_ts(data.get("timestamp")),
                        }
                    )
                    continue
                if event_type == "permission-mode":
                    permission_modes.append(
                        {
                            "mode": data.get("permissionMode"),
                            "ts": _parse_ts(data.get("timestamp")),
                        }
                    )
                    continue
                if event_type == "file-history-snapshot":
                    snapshots.append(
                        {
                            "message_id": data.get("messageId"),
                            "is_update": data.get("isSnapshotUpdate", False),
                        }
                    )
                    continue

                if event_type not in _MESSAGE_TYPES:
                    continue

                if event_type == "attachment":
                    role = "user"
                    content = _extract_attachment_content(data.get("attachment"))
                    tool_calls: list | None = None
                    tool_results: list | None = None
                else:
                    role = event_type  # "user" or "assistant"
                    msg = data.get("message") or {}
                    role = str(msg.get("role", role))
                    raw_content = msg.get("content")
                    if isinstance(raw_content, str):
                        content = raw_content
                        tool_calls = None
                        tool_results = None
                    elif isinstance(raw_content, list):
                        flat = flatten_message(raw_content, source="claude_code")
                        content = flat.content
                        tool_calls = flat.tool_calls
                        tool_results = flat.tool_results
                    else:
                        content = "" if raw_content is None else str(raw_content)
                        tool_calls = None
                        tool_results = None

                msg_metadata: dict[str, object] = {}
                if data.get("isSidechain"):
                    msg_metadata["sidechain"] = True
                if data.get("isMeta"):
                    msg_metadata["meta"] = True
                if data.get("subtype"):
                    msg_metadata["subtype"] = data["subtype"]
                if data.get("permissionMode"):
                    msg_metadata["permission_mode"] = data["permissionMode"]
                if data.get("requestId"):
                    msg_metadata["request_id"] = data["requestId"]
                if data.get("promptId"):
                    msg_metadata["prompt_id"] = data["promptId"]

                messages.append(
                    RawMessage(
                        external_uuid=data.get("uuid"),
                        parent_uuid=data.get("parentUuid"),
                        role=role,
                        content=content,
                        tool_calls=tool_calls,
                        tool_results=tool_results,
                        ts=_parse_ts(data.get("timestamp")),
                        metadata=msg_metadata,
                    )
                )

        if session_id is not None:
            meta["session_id"] = session_id
        if cwd is not None:
            meta["cwd"] = cwd
        if git_branch is not None:
            meta["git_branch"] = git_branch
        if version is not None:
            meta["client_version"] = version
        if last_prompt is not None:
            meta["last_prompt"] = last_prompt
        if permission_modes:
            meta["permission_modes"] = permission_modes
        if pr_links:
            meta["pr_links"] = pr_links
        if snapshots:
            meta["file_history_snapshots"] = snapshots

        content_hash = ""
        if content_hash_parts:
            content_hash = hashlib.sha256("\n".join(content_hash_parts).encode("utf-8")).hexdigest()

        return RawConversation(
            source_uri=f"claude-code://{path.parent.name}/{path.stem}",
            external_id=path.stem,
            content_hash=content_hash,
            title=title,
            started_at=messages[0].ts if messages else None,
            ended_at=messages[-1].ts if messages else None,
            messages=messages,
            metadata=meta,
            labels=[],
        )

    def scan(self) -> Iterator[RawConversation]:
        """Iterate per-session JSONLs, then optionally yield prompt-history sessions."""
        for path in self.discover():
            result = self.parse(path)
            if result is not None:
                yield result

        if self.history_path is None or not self.history_path.is_file():
            return
        yield from self._scan_history(self.history_path)

    def _scan_history(self, history_path: Path) -> Iterator[RawConversation]:
        """Group ``~/.claude/history.jsonl`` rows by sessionId.

        Each row is a typed user prompt with millisecond timestamp,
        ``sessionId``, ``project`` cwd, and ``pastedContents`` (a dict
        of paste-id → text for any clipboard inserts). Captured because
        the per-session JSONL only stores the *normalised* user turn
        the agent saw — pasted content and intermediate re-edits are
        only visible in ``history.jsonl``.
        """
        by_session: dict[str, list[dict]] = {}
        try:
            raw = history_path.read_bytes()
        except OSError:
            return
        hash_input = raw
        for line in raw.decode("utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            sid = entry.get("sessionId")
            if not sid:
                continue
            by_session.setdefault(str(sid), []).append(entry)

        for sid, entries in by_session.items():
            entries.sort(key=lambda e: e.get("timestamp") or 0)
            messages: list[RawMessage] = []
            project_cwd: str | None = None
            for entry in entries:
                # history.jsonl stores epoch milliseconds; normalise to seconds.
                ts_raw = entry.get("timestamp")
                ts: float | None = None
                if isinstance(ts_raw, (int, float)):
                    ts = float(ts_raw) / 1000.0 if ts_raw > 10**11 else float(ts_raw)
                project_cwd = project_cwd or entry.get("project")
                pasted = entry.get("pastedContents") or {}
                parts: list[str] = [str(entry.get("display", ""))]
                if isinstance(pasted, dict):
                    for paste_id, paste in pasted.items():
                        if isinstance(paste, dict):
                            text = paste.get("content") or paste.get("text")
                            if text:
                                parts.append(f"\n\n[pasted:{paste_id}]\n{text}")
                        elif paste:
                            parts.append(f"\n\n[pasted:{paste_id}]\n{paste}")
                messages.append(
                    RawMessage(
                        external_uuid=None,
                        parent_uuid=None,
                        role="user",
                        content="".join(parts),
                        tool_calls=None,
                        tool_results=None,
                        ts=ts,
                        metadata={},
                    )
                )
            if not messages:
                continue
            digest = hashlib.sha256(hash_input + sid.encode()).hexdigest()
            yield RawConversation(
                # Distinct URI scheme so this doesn't collide with the
                # project-session conversation that shares the sessionId.
                source_uri=f"claude-code-history://{sid}",
                # Distinct external_id prevents the feedback-session
                # auto-linker from binding both to the same feedback row.
                external_id=f"{sid}-prompts",
                content_hash=digest,
                title=None,
                started_at=messages[0].ts,
                ended_at=messages[-1].ts,
                messages=messages,
                metadata={
                    "session_id": sid,
                    "source_kind": "history",
                    **({"cwd": project_cwd} if project_cwd else {}),
                },
                labels=[],
            )
