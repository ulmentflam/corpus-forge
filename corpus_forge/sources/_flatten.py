"""Shared flattening logic for chat sources."""

from .base import RawMessage


def flatten_message(blocks: list[dict], *, source: str) -> RawMessage:
    """
    Single helper that turns either a Claude Code message.content list
    or a list of OpenCode parts into a unified RawMessage.
    The two chat plugins share this — there is no parallel parsing pipeline.

    Args:
        blocks: List of content blocks (Claude Code) or parts (OpenCode)
        source: Source identifier ('claude_code' or 'opencode') for metadata

    Returns:
        RawMessage with flattened content
    """
    content_parts = []
    tool_calls = []
    tool_results = []

    for block in blocks:
        if isinstance(block, dict):
            block_type = block.get("type")

            # Text content
            if block_type == "text":
                content_parts.append(block.get("text", ""))

            # Tool use (Claude Code format)
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    }
                )

            # Tool result (Claude Code format)
            elif block_type == "tool_result":
                tool_results.append(
                    {
                        "tool_use_id": block.get("tool_use_id"),
                        "content": block.get("content"),
                        "is_error": block.get("is_error", False),
                    }
                )

            # OpenCode part formats (simplified)
            elif block_type in ("thinking", "comment"):
                content_parts.append(f"[{block_type}] {block.get('content', '')}")

    content = "".join(content_parts).strip()

    return RawMessage(
        external_uuid=None,  # Would be set by caller
        parent_uuid=None,  # Would be set by caller
        role="assistant",  # Default, overridden by caller
        content=content,
        tool_calls=tool_calls if tool_calls else None,
        tool_results=tool_results if tool_results else None,
        ts=None,  # Would be set by caller
        metadata={"source": source},
    )
