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
    mirrors the rerank sub-package pattern from R4.

    The cached ``corpus_forge.mcp.*`` entries are snapshotted and
    **restored** afterwards — sibling test files capture symbols from
    these exact module objects at collection time, so dropping them
    permanently makes ``patch("corpus_forge.mcp.lifecycle.X")`` target
    a freshly-created module while the captured functions keep reading
    the old one, silently nullifying every patch (see
    ``TestImportSurface`` in ``test_mcp_server.py`` for the full
    failure story; under ``-n auto`` this broke the lifecycle tests in
    ``test_mcp_restart_and_doctor.py`` whenever both landed on the
    same xdist worker).
    """
    prefix = "corpus_forge.mcp"
    snapshot = {k: v for k, v in sys.modules.items() if k.startswith(prefix)}
    for k in list(snapshot):
        sys.modules.pop(k, None)
    try:
        import corpus_forge.mcp  # noqa: F401

        assert "corpus_forge.mcp.server" not in sys.modules, (
            "Importing corpus_forge.mcp must not eagerly import the server module."
        )
    finally:
        # Put the pre-test cache back so function/class identity stays
        # stable for every other test file on this worker.
        for k in list(sys.modules):
            if k.startswith(prefix) and k not in snapshot:
                sys.modules.pop(k, None)
        sys.modules.update(snapshot)
