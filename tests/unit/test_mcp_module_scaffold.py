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

import subprocess
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

    Runs the check in a subprocess so the assertion sees a truly fresh
    interpreter (no prior ``from corpus_forge.mcp.X import Y`` having
    populated ``sys.modules``).  In-process snapshot/restore of
    ``sys.modules`` is insufficient — the import statement also writes
    submodules as attributes on the parent package
    (``corpus_forge.mcp.server = <module>``), and that attribute
    survives ``sys.modules.pop``.  When a sibling test then does
    ``import corpus_forge.mcp.server as server_mod``, Python's
    ``IMPORT_FROM`` bytecode resolves ``server`` via attribute lookup on
    ``corpus_forge.mcp`` — picking up the stale freshly-imported module
    instead of the one in ``sys.modules``.  Under ``-n auto`` this
    surfaces as spurious ``ValueError: I/O operation on closed file``
    (``test_cli_mcp_serve``) and ``ProcessDiscoveryUnavailable``
    cascades (``test_mcp_restart_and_doctor``) on the same xdist
    worker.  The subprocess form sidesteps both: a fresh interpreter
    has no parent-attr pointers to leak.
    """
    code = (
        "import sys\n"
        "import corpus_forge.mcp  # noqa: F401\n"
        "assert 'corpus_forge.mcp.server' not in sys.modules, (\n"
        "    'Importing corpus_forge.mcp must not eagerly import the server module; '\n"
        "    f'found keys: {[k for k in sys.modules if k.startswith(\"corpus_forge.mcp\")]}'\n"
        ")\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    assert result.returncode == 0, (
        f"Subprocess assertion failed (exit {result.returncode}).\n"
        f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    )
