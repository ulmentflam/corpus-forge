"""Unit tests for the multilingual prose fixture family.

Pure-filesystem checks (no Postgres, no DB, no model downloads) over the
``prose/multilingual/`` subtree of the multi-format fixture corpus built
by ``scripts/build_fixture_corpus.py::build_multilingual_prose``.

Asserts the six expected ``<lang>.md`` files exist, decode as UTF-8, and
that the Russian / Japanese / Arabic files carry real Cyrillic / CJK /
Arabic codepoints — proving genuine multi-script coverage, not just
filename stems.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"
_MULTILINGUAL_DIR = _FIXTURE_ROOT / "prose" / "multilingual"

# The six languages the builder emits, keyed by filename stem.
_EXPECTED_STEMS = frozenset({"es", "fr", "de", "ru", "ja", "ar"})


def _has_codepoint_in(text: str, start: int, end: int) -> bool:
    """Return True if any character of ``text`` lies in ``[start, end]``."""
    return any(start <= ord(ch) <= end for ch in text)


class TestMultilingualFixtures:
    """Filesystem-only assertions over ``prose/multilingual/``."""

    def test_directory_exists(self) -> None:
        assert _MULTILINGUAL_DIR.is_dir(), (
            f"Multilingual prose dir missing: {_MULTILINGUAL_DIR}. Re-run "
            "`uv run python scripts/build_fixture_corpus.py`."
        )

    def test_exactly_six_expected_files(self) -> None:
        md_files = sorted(p.name for p in _MULTILINGUAL_DIR.glob("*.md"))
        expected = sorted(f"{stem}.md" for stem in _EXPECTED_STEMS)
        assert md_files == expected, (
            f"prose/multilingual/ should contain exactly {expected}; found {md_files}."
        )
        # No stray non-markdown files snuck in.
        all_files = sorted(p.name for p in _MULTILINGUAL_DIR.iterdir() if p.is_file())
        assert all_files == expected, (
            f"Unexpected non-.md files under prose/multilingual/: "
            f"{sorted(set(all_files) - set(expected))}."
        )

    def test_every_file_is_nonempty_valid_utf8(self) -> None:
        for path in sorted(_MULTILINGUAL_DIR.glob("*.md")):
            raw = path.read_bytes()
            assert raw, f"{path.name} is empty."
            # Raises UnicodeDecodeError on invalid UTF-8 — that's the assertion.
            text = raw.decode("utf-8")
            assert text.strip(), f"{path.name} has no non-whitespace content."

    def test_at_least_five_distinct_languages(self) -> None:
        stems = {p.stem for p in _MULTILINGUAL_DIR.glob("*.md")}
        covered = stems & _EXPECTED_STEMS
        assert len(covered) >= 5, (
            f"Expected at least 5 of {sorted(_EXPECTED_STEMS)} languages; "
            f"covered only {sorted(covered)}."
        )

    def test_multi_script_codepoints_present(self) -> None:
        """ru/ja/ar files must contain real Cyrillic / CJK / Arabic glyphs."""
        ru = (_MULTILINGUAL_DIR / "ru.md").read_text(encoding="utf-8")
        assert _has_codepoint_in(ru, 0x0400, 0x04FF), (
            "ru.md contains no Cyrillic codepoints (U+0400 to U+04FF)."
        )

        ja = (_MULTILINGUAL_DIR / "ja.md").read_text(encoding="utf-8")
        # Hiragana/Katakana (U+3040 to U+30FF) or CJK Unified Ideographs
        # (U+4E00 to U+9FFF).
        assert _has_codepoint_in(ja, 0x3040, 0x30FF) or _has_codepoint_in(ja, 0x4E00, 0x9FFF), (
            "ja.md contains no CJK/kana codepoints."
        )

        ar = (_MULTILINGUAL_DIR / "ar.md").read_text(encoding="utf-8")
        assert _has_codepoint_in(ar, 0x0600, 0x06FF), (
            "ar.md contains no Arabic codepoints (U+0600 to U+06FF)."
        )
