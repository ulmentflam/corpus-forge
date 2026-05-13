"""Transport enum for the ``corpus-forge mcp serve`` CLI.

v1 ships ``stdio`` only.  The enum exists so the CLI flag has a clean
type to validate against, and so future transports (HTTP/SSE, websocket,
etc.) can land without churning the public surface.

The enum derives from ``str`` so that ``Transport.STDIO == "stdio"`` is
True — that matches the wire string the typer flag accepts.
"""

from __future__ import annotations

from enum import StrEnum


class Transport(StrEnum):
    """Wire-string-backed transport enum for the MCP server.

    Members
    -------
    STDIO
        JSON-RPC over stdin/stdout.  The only transport implemented in
        corpus-forge v1; sufficient for Claude Desktop, ``mcp-cli``, and
        any other client that drives MCP servers as subprocesses.

    Future
    ------
    HTTP/SSE and other transports may land in later phases; adding them
    here MUST also update the CLI's ``--transport`` validator and ship
    a corresponding adapter in :mod:`corpus_forge.mcp.server`.
    """

    STDIO = "stdio"
