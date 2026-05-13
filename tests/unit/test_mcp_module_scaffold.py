"""R5-02 — ``corpus_forge.mcp`` module scaffold + Transport enum.

Pins:

- ``corpus_forge.mcp`` is importable as a package.
- ``corpus_forge.mcp.transport`` exposes a future-proof ``Transport`` enum
  with at least ``STDIO`` defined.
- The enum's value matches the wire string the CLI flag accepts (``"stdio"``).
- Importing the package is side-effect-free (no MCP server is constructed
  at import-time; no heavy deps are eagerly loaded).
"""

from __future__ import annotations

import sys


def test_mcp_package_importable() -> None:
    import corpus_forge.mcp  # noqa: F401

    assert "corpus_forge.mcp" in sys.modules


def test_transport_module_importable() -> None:
    from corpus_forge.mcp import transport  # noqa: F401

    assert "corpus_forge.mcp.transport" in sys.modules


def test_transport_enum_has_stdio() -> None:
    from corpus_forge.mcp.transport import Transport

    assert hasattr(Transport, "STDIO"), "Transport enum must expose STDIO"
    assert Transport.STDIO.value == "stdio", (
        f"Transport.STDIO.value must be 'stdio' (matches CLI flag); got {Transport.STDIO.value!r}"
    )


def test_transport_enum_is_string_enum() -> None:
    """Transport must derive from str so `Transport.STDIO == 'stdio'` works."""
    from corpus_forge.mcp.transport import Transport

    assert Transport.STDIO == "stdio"


def test_package_import_does_not_load_server() -> None:
    """`import corpus_forge.mcp` must NOT eagerly import the server module
    (which pulls in the third-party `mcp` package).  Lazy-load discipline
    mirrors the rerank sub-package pattern from R4."""
    # Drop any cached modules first
    for k in [k for k in list(sys.modules) if k.startswith("corpus_forge.mcp")]:
        sys.modules.pop(k, None)

    import corpus_forge.mcp  # noqa: F401

    assert "corpus_forge.mcp.server" not in sys.modules, (
        "Importing corpus_forge.mcp must not eagerly import the server module."
    )
