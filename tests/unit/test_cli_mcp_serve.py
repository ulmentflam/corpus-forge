"""R5-02 / R5-04 — `corpus-forge mcp serve` CLI surface.

R5-02 scope: pin the existence of the ``mcp serve`` subcommand and its
``--transport`` flag (only ``stdio`` is valid in v1).

R5-04 will extend this module with end-to-end CLI tests that exercise
the serve loop against a seeded fake retriever.  For Wave 1 we only
pin the help-surface so the typer registration lands correctly.
"""

from __future__ import annotations

from typer.testing import CliRunner


def test_help_lists_mcp_subcommand() -> None:
    """`corpus-forge --help` advertises the `mcp` subcommand group."""
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "mcp" in result.output, (
        f"`mcp` subcommand must appear in `corpus-forge --help`; got:\n{result.output}"
    )


def test_help_lists_serve_under_mcp() -> None:
    """`corpus-forge mcp --help` advertises the `serve` subcommand."""
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0, result.output
    assert "serve" in result.output, (
        f"`serve` must appear in `corpus-forge mcp --help`; got:\n{result.output}"
    )


def test_help_lists_stdio_transport() -> None:
    """`corpus-forge mcp serve --help` documents the `--transport` flag.

    In v1, only ``stdio`` is a valid transport.  The CLI surfaces the flag
    so users can future-proof against later transports (HTTP, etc.).
    """
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--transport" in result.output, (
        f"`mcp serve --help` must list `--transport`; got:\n{result.output}"
    )
    assert "stdio" in result.output.lower(), (
        f"`mcp serve --help` must mention `stdio` transport; got:\n{result.output}"
    )


def test_invalid_transport_rejected() -> None:
    """Anything other than ``stdio`` must be rejected (v1 only ships stdio)."""
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "serve", "--transport", "http"])
    assert result.exit_code != 0, (
        f"`--transport http` should be rejected in v1; got:\n{result.output}"
    )


# ── R5-04: dispatch wiring ───────────────────────────────────────────────


def test_mcp_serve_stdio_dispatches_to_serve_stdio(monkeypatch) -> None:
    """`corpus-forge mcp serve` (default transport) calls
    ``corpus_forge.mcp.server.serve_stdio`` exactly once."""
    import corpus_forge.mcp.server as server_mod
    from corpus_forge.cli import app

    calls = {"count": 0, "kwargs": None}

    def _fake_serve_stdio(**kwargs):
        calls["count"] += 1
        calls["kwargs"] = kwargs

    monkeypatch.setattr(server_mod, "serve_stdio", _fake_serve_stdio)

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "serve"])
    assert result.exit_code == 0, result.output
    assert calls["count"] == 1, f"serve_stdio must be invoked once; got {calls['count']}"


def test_mcp_serve_passes_default_dataset(monkeypatch) -> None:
    """`--dataset NAME` flows into ``serve_stdio(default_dataset=NAME)``."""
    import corpus_forge.mcp.server as server_mod
    from corpus_forge.cli import app

    captured = {}

    def _fake_serve_stdio(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(server_mod, "serve_stdio", _fake_serve_stdio)

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "serve", "--dataset", "vault"])
    assert result.exit_code == 0, result.output
    assert captured.get("default_dataset") == "vault", (
        f"--dataset must flow into serve_stdio(default_dataset=...); got {captured!r}"
    )


# ── Missing-`mcp`-extra hint (regression for 0005 finding) ───────────────


def test_missing_mcp_extra_shows_install_hint(monkeypatch) -> None:
    """When the optional ``mcp`` package isn't installed, ``mcp serve`` must
    emit a one-line install hint and exit 1 — NOT a Rich-formatted traceback
    from the deep ``from mcp.server.stdio import stdio_server`` lazy import.
    """
    import sys

    from corpus_forge.cli import app

    # Simulate the `mcp` package being unavailable by shadowing it in
    # sys.modules with a ModuleNotFoundError-raising sentinel. The
    # pre-flight `import mcp` in cli.mcp_serve must catch it.
    real_mcp = sys.modules.pop("mcp", None)

    class _MissingFinder:
        def find_spec(self, name, path=None, target=None):
            if name == "mcp" or name.startswith("mcp."):
                raise ModuleNotFoundError(f"No module named {name!r}")
            # Defer to other finders for everything else.

    finder = _MissingFinder()
    sys.meta_path.insert(0, finder)
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["mcp", "serve"])
    finally:
        sys.meta_path.remove(finder)
        if real_mcp is not None:
            sys.modules["mcp"] = real_mcp

    assert result.exit_code == 1, (
        f"missing-`mcp` path must exit 1; got {result.exit_code}\n{result.output}"
    )
    out = result.output + (result.stderr if hasattr(result, "stderr") and result.stderr else "")
    assert "mcp" in out.lower() and "extra" in out.lower(), (
        f"error message must name the missing extra; got:\n{out}"
    )
    # The recovery command must be present — property #2 of the
    # `human-friendly` plan: error messages name the broken thing AND
    # the fix. Rich may wrap the line in narrow terminals, so check the
    # collapsed (whitespace-normalised) form.
    collapsed = " ".join(out.split())
    assert "corpus-forge[mcp]" in collapsed, (
        f"error message must include the `corpus-forge[mcp]` install command; got:\n{collapsed}"
    )
    assert "Traceback" not in out, f"missing-`mcp` path must not surface a traceback; got:\n{out}"
