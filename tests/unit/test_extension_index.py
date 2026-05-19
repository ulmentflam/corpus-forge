"""Phase M Wave 2 — `_full_ext_index()` + filename-index unit tests.

`_full_ext_index()` is the union of:
  - every extractor's `supported_extensions` (via `ExtractorRegistry`)
  - every heuristic-table extension (from `_heuristics()`)

It seeds the walker's `include_exts` argument so we can short-circuit
on file extension BEFORE calling `entry.stat()`. The filename-only set
is the union of every extractor's `supported_filenames` (Makefile,
Dockerfile, ...) — same purpose, applied to extension-less files.
"""

from __future__ import annotations


def test_full_ext_index_contains_common_text_extensions() -> None:
    from corpus_forge.estimate import _full_ext_index

    idx = _full_ext_index()
    assert ".md" in idx
    assert ".py" in idx
    assert ".pdf" in idx
    assert ".txt" in idx
    assert ".json" in idx
    assert ".ipynb" in idx


def test_full_ext_index_omits_binary_only_extensions() -> None:
    from corpus_forge.estimate import _full_ext_index

    idx = _full_ext_index()
    # These are not in the heuristic table and no extractor supports them;
    # the perf walker MUST short-circuit on these before stat'ing.
    assert ".iso" not in idx
    assert ".dmg" not in idx
    assert ".zip" not in idx


def test_full_ext_index_is_frozenset_lowercase() -> None:
    from corpus_forge.estimate import _full_ext_index

    idx = _full_ext_index()
    assert isinstance(idx, frozenset)
    # Every entry starts with `.` and is lowercase.
    for ext in idx:
        assert ext.startswith(".")
        assert ext == ext.lower()


def test_full_ext_index_idempotent() -> None:
    from corpus_forge.estimate import _full_ext_index

    a = _full_ext_index()
    b = _full_ext_index()
    assert a == b


def test_registry_filenames_contains_makefile_and_dockerfile() -> None:
    from corpus_forge.extractors.registry import register_default_extractors

    reg = register_default_extractors(None)
    filenames = set(reg.filenames())
    # CodeExtractor's _SUPPORTED_FILENAMES carries these. If the extractor
    # is gated off, the walker still won't yield extension-less files —
    # so we only enforce when the extractor is in the registry.
    if filenames:
        assert "Makefile" in filenames
        assert "Dockerfile" in filenames
