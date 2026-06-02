"""T1 — abs-path resolver tests.

Pins :func:`corpus_forge.sources.path_resolve.resolve_abs_path`:

- Resolves ``filesystem://<root_name>/<rel>`` against
  ``config.datasets[*].sources[*]`` where ``plugin == "filesystem"`` by
  matching ``Path(source.root).name`` to ``<root_name>``.
- Also resolves the legacy ``vault://<root_name>/<rel>`` shape against
  ``markdown_vault`` sources via ``vault_root``.
- Returns ``None`` for non-filesystem schemes
  (``claude-code://``, ``http(s)://``, ``chatgpt-export://``, ...).
- Returns ``None`` for unknown root names, malformed URIs, missing
  matches — never raises.
- Returns the root path itself when the URI has no rel component.
- Disambiguates between multiple filesystem sources by ``.name``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.config import Config


def _make_config(*sources: dict) -> Config:
    """Build a minimal in-process Config with the given source blocks."""
    return Config(
        **{
            "backend": {"kind": "sqlite", "dsn": "/tmp/test.db"},
            "daemon": {},
            "datasets": [
                {
                    "name": "ds",
                    "kind": "text",
                    "sources": list(sources),
                }
            ],
            "embedders": [
                {
                    "name": "e1",
                    "provider": "sentence_transformers",
                    "model_id": "test/m",
                    "dimension": 8,
                }
            ],
        }
    )


# ── Filesystem URI resolution ──────────────────────────────────────────


def test_resolve_filesystem_uri_classic(tmp_path: Path) -> None:
    """``filesystem://<root.name>/<rel>`` resolves against the filesystem source."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    root = tmp_path / "Notes"
    root.mkdir()
    cfg = _make_config({"plugin": "filesystem", "root": str(root), "chunker": "markdown"})
    out = resolve_abs_path("filesystem://Notes/sub/file.md", cfg)
    assert out == (root / "sub/file.md").resolve()


def test_resolve_filesystem_uri_returns_root_when_rel_empty(tmp_path: Path) -> None:
    """A URI with no rel part returns the resolved root."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    root = tmp_path / "Vault"
    root.mkdir()
    cfg = _make_config({"plugin": "filesystem", "root": str(root), "chunker": "markdown"})
    out = resolve_abs_path("filesystem://Vault/", cfg)
    assert out == root.resolve()


def test_resolve_filesystem_uri_no_trailing_slash_returns_root(tmp_path: Path) -> None:
    """A URI with NO trailing slash (``filesystem://Vault``) also returns the root."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    root = tmp_path / "Vault"
    root.mkdir()
    cfg = _make_config({"plugin": "filesystem", "root": str(root), "chunker": "markdown"})
    out = resolve_abs_path("filesystem://Vault", cfg)
    assert out == root.resolve()


def test_resolve_filesystem_uri_multi_source_disambiguates(tmp_path: Path) -> None:
    """Two filesystem sources — only the one with matching ``.name`` is chosen."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    root_a = tmp_path / "Notes"
    root_b = tmp_path / "Code"
    root_a.mkdir()
    root_b.mkdir()
    cfg = _make_config(
        {"plugin": "filesystem", "root": str(root_a), "chunker": "markdown"},
        {"plugin": "filesystem", "root": str(root_b), "chunker": "markdown"},
    )
    out = resolve_abs_path("filesystem://Code/lib/foo.py", cfg)
    assert out == (root_b / "lib/foo.py").resolve()


def test_resolve_filesystem_uri_unknown_root_returns_none(tmp_path: Path) -> None:
    """Unknown root name → None (do not raise)."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    root = tmp_path / "Notes"
    root.mkdir()
    cfg = _make_config({"plugin": "filesystem", "root": str(root), "chunker": "markdown"})
    assert resolve_abs_path("filesystem://Other/x.md", cfg) is None


# ── Vault URI resolution (markdown_vault legacy) ───────────────────────


def test_resolve_vault_uri(tmp_path: Path) -> None:
    """``vault://<root_name>/<rel>`` resolves against markdown_vault sources."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    vault = tmp_path / "Obsidian"
    vault.mkdir()
    cfg = _make_config(
        {"plugin": "markdown_vault", "vault_root": str(vault), "chunker": "markdown"}
    )
    out = resolve_abs_path("vault://Obsidian/notes/today.md", cfg)
    assert out == (vault / "notes/today.md").resolve()


# ── Non-filesystem schemes ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "uri",
    [
        "claude-code://project-x/session-1",
        "claude-code-history://abc",
        "chatgpt-export:///tmp/x.json",
        "codex-cli:///tmp/x.jsonl",
        "gemini-cli:///tmp/x.json",
        "jsonl-chat:///tmp/x.jsonl",
        "opencode://abc",
        "zotero://1234/itemkey/abstract",
        "https://example.com/x.md",
        "http://example.com/x",
    ],
)
def test_non_filesystem_scheme_returns_none(uri: str, tmp_path: Path) -> None:
    """Every non-filesystem URI scheme returns None."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    cfg = _make_config({"plugin": "filesystem", "root": str(tmp_path), "chunker": "markdown"})
    assert resolve_abs_path(uri, cfg) is None


def test_malformed_uri_returns_none(tmp_path: Path) -> None:
    """Malformed URIs return None — never raise."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    cfg = _make_config({"plugin": "filesystem", "root": str(tmp_path), "chunker": "markdown"})
    for bad in ("", "not-a-uri", "filesystem:", "filesystem://", None):
        assert resolve_abs_path(bad, cfg) is None  # type: ignore[arg-type]


def test_no_filesystem_sources_returns_none(tmp_path: Path) -> None:
    """If config has no filesystem/vault sources, returns None."""
    from corpus_forge.sources.path_resolve import resolve_abs_path

    cfg = _make_config(
        {
            "plugin": "claude_code",
            "projects_root": str(tmp_path),
            "chunker": "conversation",
        }
    )
    assert resolve_abs_path("filesystem://anything/x.md", cfg) is None
