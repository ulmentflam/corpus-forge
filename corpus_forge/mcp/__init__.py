"""Phase R5 — Model Context Protocol (MCP) server scaffold for corpus-forge.

This sub-package exposes the in-process MCP server that surfaces
corpus-forge's retrieval stack to MCP-compatible clients (Claude
Desktop, mcp-cli, etc.).  The public CLI entry point is::

    corpus-forge mcp serve

Module layout
-------------

- ``corpus_forge.mcp.transport`` — Transport enum + future-proof seams
  (only ``stdio`` is implemented in v1).
- ``corpus_forge.mcp.server`` — the ``mcp.server.Server`` instance, tool
  registrations, and the stdio entry point.  Lazy-imported so importing
  this package alone does NOT pull in the third-party ``mcp`` library
  (mirrors the rerank sub-package's import discipline from R4).

The third-party ``mcp`` package is installed via the ``[mcp]`` optional
extra::

    uv sync --extra mcp        # or: pip install corpus-forge[mcp]
"""

from __future__ import annotations
