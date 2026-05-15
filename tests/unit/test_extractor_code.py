"""Unit tests for D-13: CodeExtractor.

Strategy: detect language from extension or filename fallback →
return the raw source verbatim with ``chunker_hint="code"`` and the
detected ``language`` so :class:`CodeChunker` (D-02) can drive the
tree-sitter parse downstream. Tree-sitter grammars are
**lazy-fetched** via ``pack.download([lang])`` on first encounter;
fetch failures don't block extraction (CodeChunker has a byte-line
fallback).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.code import CodeExtractor

# ── Tests ────────────────────────────────────────────────────────────


def test_extractor_protocol_conformance():
    ex: Extractor = CodeExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions_covers_common_languages():
    """Spot-check a handful of expected extensions from the plan list."""
    ex = CodeExtractor()
    exts = set(ex.supported_extensions)
    must_have = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".scala",
        ".rb",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".pl",
        ".hs",
        ".ml",
        ".clj",
        ".cljs",
        ".lisp",
        ".scm",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".sql",
        ".css",
        ".scss",
        ".lua",
        ".zig",
        ".nim",
        ".cr",
        ".r",
        ".jl",
        ".swift",
        ".dart",
        ".nix",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cxx",
        ".m",
        ".mm",
    }
    missing = must_have - exts
    assert not missing, f"missing extensions: {missing}"


def test_extract_python_file(tmp_path: Path):
    p = tmp_path / "module.py"
    src = "def hello():\n    return 'world'\n"
    p.write_text(src, encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "code"
    assert doc.text == src
    assert doc.language == "python"


def test_extract_typescript_file(tmp_path: Path):
    p = tmp_path / "app.ts"
    src = "export const x: number = 1;\n"
    p.write_text(src, encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.chunker_hint == "code"
    assert doc.language == "typescript"


def test_extract_go_file(tmp_path: Path):
    p = tmp_path / "main.go"
    src = 'package main\n\nfunc main() { println("hi") }\n'
    p.write_text(src, encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.language == "go"


def test_extract_metadata(tmp_path: Path):
    p = tmp_path / "x.py"
    src = "x = 1\n"
    p.write_text(src, encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.metadata.get("extractor") == "tree-sitter"
    assert doc.metadata.get("language") == "python"
    assert doc.metadata.get("byte_count") == len(src)


def test_extract_labels(tmp_path: Path):
    p = tmp_path / "x.py"
    p.write_text("x = 1\n", encoding="utf-8")
    doc = CodeExtractor().extract(p)
    labels = {(ns, val) for ns, val in doc.labels}
    assert ("format", "code") in labels
    assert ("language", "python") in labels


@pytest.mark.parametrize(
    ("name", "language"),
    [
        ("Makefile", "make"),
        ("Dockerfile", "dockerfile"),
        (".gitignore", "gitignore"),
        (".editorconfig", "editorconfig"),
    ],
)
def test_extract_filename_fallback(tmp_path: Path, name: str, language: str):
    p = tmp_path / name
    p.write_text("# placeholder content\n", encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.chunker_hint == "code"
    assert doc.language == language


def test_extract_falls_back_for_unknown_extension(tmp_path: Path):
    """An unmapped file under our registered set still returns an
    ExtractedDocument; CodeChunker's byte-line fallback will handle it.
    But unmapped extensions live in the registry — this test only
    covers the in-set ``.editorconfig`` case where dotfiles are present.
    """
    p = tmp_path / ".gitignore"
    p.write_text("# foo\n*.tmp\n", encoding="utf-8")
    doc = CodeExtractor().extract(p)
    # Even if tree-sitter has no grammar, the extractor still returns
    # source text — CodeChunker handles the rest.
    assert doc.text.strip()


def _reset_grammar_cache(language: str) -> None:
    """Wipe the per-process attempt cache for ``language``.

    ``pack.available_languages()`` reports what the pack KNOWS how to
    download, not what is currently cached locally — so every supported
    language goes through ``pack.download([...])`` once per process.
    """
    import corpus_forge.extractors.code as code_module

    code_module._GRAMMAR_FETCH_CACHE.pop(language, None)


def test_extract_lazy_fetch_warns_on_failure(tmp_path: Path, caplog, monkeypatch):
    """If the grammar download raises, the extractor must still produce
    a document — and log a WARNING. Uses a supported language (python)
    so the download path is exercised; simulate the network failure on
    ``_download_grammar``."""
    import corpus_forge.extractors.code as code_module

    _reset_grammar_cache("python")

    def boom(language: str) -> int:
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(code_module, "_download_grammar", boom)

    src = "x = 1\n"
    p = tmp_path / "x.py"
    p.write_text(src, encoding="utf-8")
    with caplog.at_level("WARNING", logger="corpus_forge.extractors.code"):
        doc = CodeExtractor().extract(p)
    assert doc.chunker_hint == "code"
    assert doc.text == src  # source still passes through verbatim
    assert any("python" in rec.message.lower() for rec in caplog.records)


def test_extract_lazy_fetch_only_once_per_language(tmp_path: Path, monkeypatch):
    """Repeated extracts of the same language should download only once —
    idempotency guarded by ``_GRAMMAR_FETCH_CACHE``."""
    import corpus_forge.extractors.code as code_module

    _reset_grammar_cache("python")

    call_count = {"n": 0}

    def fake_download(language: str) -> int:
        call_count["n"] += 1
        return 1

    monkeypatch.setattr(code_module, "_download_grammar", fake_download)

    ex = CodeExtractor()
    for name in ("a.py", "b.py", "c.py"):
        p = tmp_path / name
        p.write_text("x = 1\n", encoding="utf-8")
        ex.extract(p)
    assert call_count["n"] == 1


def test_extract_unsupported_language_skips_download(tmp_path: Path, monkeypatch):
    """When the pack doesn't list a language, no download is attempted —
    we fall through to the byte-line chunker silently."""
    import tree_sitter_language_pack as pack

    import corpus_forge.extractors.code as code_module

    # Force ``zig`` out of the supported set so we exercise the "not
    # downloadable" path without depending on what the pack actually has.
    real_available = pack.available_languages

    def filtered_available() -> set[str]:
        return {lang for lang in real_available() if lang != "zig"}

    monkeypatch.setattr(pack, "available_languages", filtered_available)
    code_module._GRAMMAR_FETCH_CACHE.pop("zig", None)

    called = {"n": 0}

    def fake_download(language: str) -> int:
        called["n"] += 1
        return 1

    monkeypatch.setattr(code_module, "_download_grammar", fake_download)

    p = tmp_path / "x.zig"
    p.write_text("// zig\n", encoding="utf-8")
    CodeExtractor().extract(p)
    # Language is not in available_languages → download is skipped.
    assert called["n"] == 0


def test_extract_language_is_set_on_extracted_document(tmp_path: Path):
    """``ExtractedDocument.language`` must be set for the dispatcher
    to pass to CodeChunker."""
    p = tmp_path / "x.rs"
    p.write_text("fn main() {}\n", encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.language == "rust"


def test_registry_wires_code_extractor(tmp_path: Path):
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    for ext in (".py", ".rs", ".go", ".kt", ".ts"):
        p = tmp_path / f"x{ext}"
        p.write_text("// placeholder\n", encoding="utf-8")
        extractor = reg.get_for(p)
        assert isinstance(extractor, CodeExtractor), ext


def test_extract_handles_empty_file(tmp_path: Path):
    p = tmp_path / "empty.py"
    p.write_text("", encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.text == ""
    assert doc.chunker_hint == "code"
    assert doc.language == "python"
    assert doc.metadata.get("byte_count") == 0


def test_lazy_import_does_not_load_pack_on_module_import():
    """Module import must not pull tree_sitter_language_pack into RAM."""
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.code as m; "
        "assert 'tree_sitter_language_pack' not in sys.modules, sorted(sys.modules); "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize(
    ("ext", "language"),
    [
        (".py", "python"),
        (".js", "javascript"),
        (".ts", "typescript"),
        (".tsx", "tsx"),
        (".jsx", "javascript"),
        (".go", "go"),
        (".rs", "rust"),
        (".java", "java"),
        (".kt", "kotlin"),
        (".scala", "scala"),
        (".rb", "ruby"),
        (".ex", "elixir"),
        (".sh", "bash"),
        (".sql", "sql"),
        (".css", "css"),
        (".lua", "lua"),
        (".zig", "zig"),
        (".swift", "swift"),
        (".dart", "dart"),
        (".nix", "nix"),
        (".c", "c"),
        (".cpp", "cpp"),
        (".h", "c"),
        (".hpp", "cpp"),
        (".r", "r"),
        (".jl", "julia"),
    ],
)
def test_extension_language_mapping(tmp_path: Path, ext: str, language: str):
    p = tmp_path / f"x{ext}"
    p.write_text("// placeholder\n", encoding="utf-8")
    doc = CodeExtractor().extract(p)
    assert doc.language == language, f"{ext} → expected {language}, got {doc.language}"


# ── D-14: CodeExtractor.supported_filenames (Wave 2 bridge) ──────────────


def test_code_extractor_declares_supported_filenames_non_empty():
    """Wave 2 wires CodeExtractor through the filename-fallback path —
    ``supported_filenames`` must declare every filename that
    ``_detect_language`` resolves outside the extension table."""
    ex = CodeExtractor()
    assert hasattr(ex, "supported_filenames")
    assert isinstance(ex.supported_filenames, tuple)
    assert len(ex.supported_filenames) > 0


def test_code_extractor_supported_filenames_match_detect_language():
    """``supported_filenames`` is the single source of truth — every
    declared filename must resolve in ``_detect_language``, and every
    filename ``_detect_language`` handles must be declared (DRY)."""
    from corpus_forge.extractors import code as code_module

    ex = CodeExtractor()
    declared = set(ex.supported_filenames)
    # Every declaration must resolve to a non-None language.
    for name in declared:
        lang = code_module._detect_language(Path("/tmp") / name)
        assert lang is not None, f"declared filename {name!r} not handled by _detect_language"
    # Every filename handled by _detect_language (lowercase keys) must be
    # represented in ``supported_filenames`` at least once (case-folded).
    declared_lower = {name.lower() for name in declared}
    for filename_key in code_module._LANG_BY_FILENAME:
        assert filename_key in declared_lower, (
            f"filename {filename_key!r} resolved by _detect_language but missing from "
            f"supported_filenames"
        )


def test_code_extractor_supported_filenames_includes_make_and_docker():
    """Spot-check the prompt's required entries land in the tuple."""
    ex = CodeExtractor()
    decl = set(ex.supported_filenames)
    required = {
        "Makefile",
        "makefile",
        "GNUmakefile",
        "Dockerfile",
        "dockerfile",
        ".gitignore",
        ".editorconfig",
    }
    missing = required - decl
    assert not missing, f"missing required supported_filenames: {missing}"
