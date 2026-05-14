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
    "dockerfile": "dockerfile",
    ".gitignore": "gitignore",
    ".editorconfig": "editorconfig",
}

_FILENAME_FALLBACK_EXTENSIONS: tuple[str, ...] = ("",)  # used in supported_extensions filtering

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
    """Best-effort: make sure ``language``'s grammar is available locally.

    Honours :data:`_GRAMMAR_FETCH_CACHE`: a second call for the same
    language is a no-op. On failure (network down, grammar missing) we
    log a WARNING and move on — CodeChunker has a byte-line fallback.
    """
    if language in _GRAMMAR_FETCH_CACHE:
        return

    import tree_sitter_language_pack as pack  # noqa: PLC0415

    try:
        if language in pack.available_languages():
            _GRAMMAR_FETCH_CACHE[language] = True
            return
    except Exception:  # pragma: no cover — defensive
        pass

    try:
        logger.info("Lazy-fetching tree-sitter grammar: %s", language)
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

    # All registered extensions from ``_LANG_BY_EXT``. Filename fallbacks
    # (``Makefile``, etc.) are matched via the registry's path-based
    # ``get_for`` but those don't appear here since they're not
    # extensions. The Wave 2 ``FilesystemSource`` walker will add the
    # filename fallbacks via a separate dispatch hook (D-14).
    supported_extensions: tuple[str, ...] = tuple(sorted(_LANG_BY_EXT.keys()))

    def extract(self, path: Path) -> ExtractedDocument:
        text = path.read_text(encoding="utf-8")
        language = _detect_language(path)

        # Best-effort lazy fetch — never fatal.
        if language is not None and language != "python":
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
