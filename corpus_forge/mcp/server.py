"""Phase R5 — corpus-forge MCP server (in-process, stdio transport).

Wave 1 scaffold: this module currently exposes a single ``serve_stdio``
entry point that constructs an ``mcp.server.Server`` and runs the stdio
loop.  Tool registration + retriever wiring lands in R5-03 (Wave 2).

The server is constructed lazily — importing this module pulls in the
third-party ``mcp`` package (via ``[mcp]`` optional extra).  Callers
that don't need the server (e.g. ``corpus-forge migrate``) must not
import this module.
"""

from __future__ import annotations

from typing import Any


def serve_stdio(*, default_dataset: str | None = None) -> None:  # pragma: no cover - Wave 1 stub
    """Run the MCP server over stdio.

    Wave 1 scaffold: registers no tools; returns immediately once the
    client disconnects.  Wave 2 lands ``search`` / ``get_chunk`` /
    ``list_datasets`` and wires through ``HybridRetriever``.

    The full implementation moved here from the CLI to keep
    ``corpus_forge.cli`` free of the third-party ``mcp`` import.

    Args:
        default_dataset: Optional default dataset name; Wave 2 threads
            this into per-call ``SearchOptions`` when the caller does
            not specify a dataset explicitly.
    """
    import asyncio  # noqa: PLC0415

    asyncio.run(_serve_stdio_async(default_dataset=default_dataset))


async def _serve_stdio_async(*, default_dataset: str | None) -> None:  # pragma: no cover
    from mcp.server import Server  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    server: Server[Any] = Server("corpus-forge")
    # `default_dataset` will be captured by Wave 2's tool handlers via
    # closure; for now we accept it to lock the surface in place.
    _ = default_dataset

    # Wave 2 will register tools here:
    #   @server.list_tools() / @server.call_tool() etc.

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
