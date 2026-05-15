"""Code extractor — pairs with :class:`CodeChunker` (D-02).

Phase D / Wave 1 — D-13.

This extractor does **not** parse anything itself — it identifies the
language by extension (or filename fallback), reads the raw source, and
hands off to :class:`CodeChunker` via the standard ``ExtractedDocument``
shape. The chunker drives tree-sitter directly.

Grammar fetch policy (project_phase_d_treesitter_lazy_fetch):

* ``tree-sitter-language-pack`` 1.8.x ships only the Python grammar by
  default. Other languages are downloaded on first encounter via
  ``pack.download([language])``.
* On fetch failure (network down, language not packaged, etc.) the
  extractor logs a WARNING and continues — :class:`CodeChunker`'s
  byte-line long-tail fallback covers languages without grammars.
* First-time download is logged at INFO. Subsequent encounters of the
  same language skip the cache and the network entirely.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import ExtractedDocument

logger = logging.getLogger(__name__)

# ── Extension → tree-sitter language id ───────────────────────────────
#
# The keys here drive ``supported_extensions``; the values are the
# tree-sitter-language-pack language ids. Where the pack does NOT have a
# grammar we still register the extension (so dispatch routes to this
# extractor) and CodeChunker falls back to the byte-line chunker.

_LANG_BY_EXT: dict[str, str] = {
    # Mainstream
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    # BEAM
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    # Logic / functional
    ".pl": "prolog",
    ".hs": "haskell",
    ".ml": "ocaml",
    # Lisps
    ".clj": "clojure",
    ".cljs": "clojure",
    ".lisp": "commonlisp",
    ".scm": "scheme",
    # Shells
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "fish",
    # Data / web
    ".sql": "sql",
    ".css": "css",
    ".scss": "scss",
    # Niche
    ".lua": "lua",
    ".zig": "zig",
    ".nim": "nim",
    ".cr": "crystal",
    ".r": "r",
    ".jl": "julia",
    ".swift": "swift",
    ".dart": "dart",
    ".nix": "nix",
    # C-family
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cxx": "cpp",
    ".m": "objc",
    ".mm": "objc",
}

# Filename-fallback table — keyed on the exact lowercase filename.
_LANG_BY_FILENAME: dict[str, str] = {
    "makefile": "make",
    "gnumakefile": "make",
    "dockerfile": "dockerfile",
    ".gitignore": "gitignore",
    ".editorconfig": "editorconfig",
}

# Filenames declared on :attr:`CodeExtractor.supported_filenames`. The
# registry's second-pass lookup (D-14) is case-sensitive, so we list
# both common casings of ``Makefile`` / ``Dockerfile`` rather than ask
# users to normalise filenames on disk. Derived from
# :data:`_LANG_BY_FILENAME` so the two tables can't drift.
_SUPPORTED_FILENAMES: tuple[str, ...] = (
    "Makefile",
    "makefile",
    "GNUmakefile",
    "Dockerfile",
    "dockerfile",
    ".gitignore",
    ".editorconfig",
)

# Per-process cache of languages we've already attempted to fetch.
# Maps ``language -> bool`` where ``True`` means "fetch succeeded or was
# skipped because already present". ``False`` is reserved for explicit
# fetch failures so we don't keep hammering a missing grammar.
_GRAMMAR_FETCH_CACHE: dict[str, bool] = {}


def _detect_language(path: Path) -> str | None:
    """Return the tree-sitter language id for ``path`` or None.

    Resolution order:
    1. Filename-fallback table (``Makefile``, ``Dockerfile``, dotfiles).
    2. File-extension table.
    """
    name_lower = path.name.lower()
    if name_lower in _LANG_BY_FILENAME:
        return _LANG_BY_FILENAME[name_lower]
    ext = path.suffix.lower()
    return _LANG_BY_EXT.get(ext)


def _download_grammar(language: str) -> int:
    """Thin wrapper around ``tree_sitter_language_pack.download``.

    Split out as a module-level function so tests can monkey-patch the
    fetch without touching the pack itself.
    """
    import tree_sitter_language_pack as pack  # noqa: PLC0415

    return pack.download([language])


def _ensure_grammar(language: str) -> None:
    """Best-effort: make sure ``language``'s grammar is downloaded locally.

    Honours :data:`_GRAMMAR_FETCH_CACHE`: a second call for the same
    language is a no-op. On failure (network down, grammar missing) we
    log a WARNING and move on — CodeChunker has a byte-line fallback.

    Note: ``pack.available_languages()`` lists every language the pack
    *knows how to download*, not what is currently cached locally. So we
    must actually call ``pack.download(...)`` (idempotent — fast no-op on
    repeat) rather than treating "in available_languages" as "ready".
    Otherwise on platforms where the wheel doesn't pre-bundle the grammar
    (Linux), ``pack.process`` returns items with ``kind=None``/``name=None``.
    """
    if language in _GRAMMAR_FETCH_CACHE:
        return

    import tree_sitter_language_pack as pack  # noqa: PLC0415

    try:
        supported = language in pack.available_languages()
    except Exception:  # pragma: no cover — defensive
        supported = False

    if not supported:
        # Not a language the pack can produce a grammar for; skip the
        # download attempt entirely so we don't waste a network round-trip.
        _GRAMMAR_FETCH_CACHE[language] = False
        return

    try:
        logger.info("Ensuring tree-sitter grammar is cached: %s", language)
        _download_grammar(language)
        _GRAMMAR_FETCH_CACHE[language] = True
    except Exception as exc:
        logger.warning(
            "Lazy-fetch of tree-sitter grammar %r failed (%s); "
            "CodeChunker byte-line fallback will be used.",
            language,
            exc,
        )
        _GRAMMAR_FETCH_CACHE[language] = False


class CodeExtractor:
    """Identifies source files and hands raw text to ``CodeChunker``."""

    # All registered extensions from ``_LANG_BY_EXT``.
    supported_extensions: tuple[str, ...] = tuple(sorted(_LANG_BY_EXT.keys()))

    # Wave 2 (D-14) — filenames matched by the registry's second-pass
    # lookup. Both casings of common build-tool names are declared so
    # cross-platform repos resolve regardless of which the user has on
    # disk.
    supported_filenames: tuple[str, ...] = _SUPPORTED_FILENAMES

    def __init__(self, code_chunker_config: dict | None = None):
        """``code_chunker_config`` is plumbed onto every
        ``ExtractedDocument`` so the downstream ``ChunkerDispatcher`` can
        construct ``CodeChunker(**cfg)`` with the user's tunables. Held
        as a ``dict`` here (not unpacked) so the dispatcher decides when
        to instantiate.
        """
        self.code_chunker_config: dict = code_chunker_config or {}

    def extract(self, path: Path) -> ExtractedDocument:
        text = path.read_text(encoding="utf-8")
        language = _detect_language(path)

        # Best-effort lazy fetch — never fatal. Every supported language
        # goes through ``_ensure_grammar`` once per process; the helper is
        # idempotent and downstream callers (CodeChunker) tolerate failure
        # via the byte-line fallback. Python is NOT special-cased here —
        # tree-sitter-language-pack only pre-bundles the Python grammar
        # on macOS wheels, NOT on Linux wheels.
        if language is not None:
            _ensure_grammar(language)

        labels = [("format", "code")]
        if language is not None:
            labels.append(("language", language))

        return ExtractedDocument(
            text=text,
            chunker_hint="code",
            language=language,
            metadata={
                "extractor": "tree-sitter",
                "language": language,
                "byte_count": len(text),
            },
            labels=labels,
        )
