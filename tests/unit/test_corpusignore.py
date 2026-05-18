"""Phase K1 — `.corpusignore` gitignore-subset matcher."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from corpus_forge.ignore import (
    CorpusIgnore,
    IgnoreStack,
    load_global_ignore,
    load_local_ignore,
)

# ── from_lines: blank / comment edge cases ───────────────────────────────


class TestFromLinesEmptyCases:
    def test_empty_file_matches_nothing(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines([], root=tmp_path)
        assert ig.patterns == ()
        assert ig.matches(tmp_path / "foo.txt", is_dir=False) is False

    def test_comments_only_file_matches_nothing(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["# foo", "# bar"], root=tmp_path)
        assert ig.patterns == ()

    def test_blank_lines_only_file_matches_nothing(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["", "  ", "\t"], root=tmp_path)
        assert ig.patterns == ()

    def test_trailing_hash_is_not_a_comment(self, tmp_path: Path) -> None:
        """`*.log#tmp` should compile to a glob, not be stripped as a comment."""
        ig = CorpusIgnore.from_lines(["*.log#tmp"], root=tmp_path)
        assert len(ig.patterns) == 1
        assert ig.matches(tmp_path / "foo.log#tmp", is_dir=False) is True
        # And `foo.log` alone does NOT match — proves the pattern is the
        # literal `*.log#tmp`, not just `*.log`.
        assert ig.matches(tmp_path / "foo.log", is_dir=False) is False


# ── pattern matching: glob fundamentals ──────────────────────────────────


class TestGlobBasics:
    def test_single_glob_matches_extension(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["*.heic"], root=tmp_path)
        assert ig.matches(tmp_path / "vacation.heic", is_dir=False) is True
        # At any depth (unanchored).
        nested = tmp_path / "Photos" / "2026" / "vacation.heic"
        assert ig.matches(nested, is_dir=False) is True

    def test_anchored_pattern_matches_only_at_root(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["/Backups/"], root=tmp_path)
        assert ig.matches(tmp_path / "Backups", is_dir=True) is True
        assert ig.matches(tmp_path / "nested" / "Backups", is_dir=True) is False

    def test_unanchored_pattern_matches_any_depth(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["Backups/"], root=tmp_path)
        assert ig.matches(tmp_path / "Backups", is_dir=True) is True
        assert ig.matches(tmp_path / "nested" / "Backups", is_dir=True) is True

    def test_directory_only_pattern_does_not_match_file(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["Backups/"], root=tmp_path)
        # A file (not a dir) named `Backups` should NOT match.
        assert ig.matches(tmp_path / "Backups", is_dir=False) is False

    def test_directory_only_pattern_matches_dir(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["Backups/"], root=tmp_path)
        assert ig.matches(tmp_path / "Backups", is_dir=True) is True

    def test_double_star_matches_recursive_components(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["**/foo.txt"], root=tmp_path)
        assert ig.matches(tmp_path / "foo.txt", is_dir=False) is True
        assert ig.matches(tmp_path / "a" / "foo.txt", is_dir=False) is True
        assert ig.matches(tmp_path / "a" / "b" / "c" / "foo.txt", is_dir=False) is True

    def test_single_star_does_not_match_slash(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["foo*bar"], root=tmp_path)
        assert ig.matches(tmp_path / "fooXbar", is_dir=False) is True
        # The `*` should NOT consume `/`.
        assert ig.matches(tmp_path / "foo" / "X" / "bar", is_dir=False) is False

    def test_question_mark_matches_single_char(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["file?.txt"], root=tmp_path)
        assert ig.matches(tmp_path / "fileA.txt", is_dir=False) is True
        # Two chars after `file` → no match.
        assert ig.matches(tmp_path / "fileAB.txt", is_dir=False) is False


# ── negation semantics ───────────────────────────────────────────────────


class TestNegation:
    def test_negation_un_ignores_later_match(self, tmp_path: Path) -> None:
        """`*.log` ignores everything; `!important.log` un-ignores one."""
        ig = CorpusIgnore.from_lines(["*.log", "!important.log"], root=tmp_path)
        assert ig.matches(tmp_path / "foo.log", is_dir=False) is True
        assert ig.matches(tmp_path / "important.log", is_dir=False) is False

    def test_negation_order_matters_later_wins(self, tmp_path: Path) -> None:
        """`!important.log` first, then bare `*.log` re-ignores it."""
        ig = CorpusIgnore.from_lines(["!important.log", "*.log"], root=tmp_path)
        # The bare `*.log` is encountered AFTER the negation → it re-ignores.
        assert ig.matches(tmp_path / "important.log", is_dir=False) is True


# ── escape sequences ─────────────────────────────────────────────────────


class TestEscapes:
    def test_escape_hash_matches_literal_hash(self, tmp_path: Path) -> None:
        """`\\#hashed` should match a file literally called `#hashed`."""
        ig = CorpusIgnore.from_lines([r"\#hashed"], root=tmp_path)
        assert ig.matches(tmp_path / "#hashed", is_dir=False) is True

    def test_escape_bang_matches_literal_bang(self, tmp_path: Path) -> None:
        """`\\!important` should match a file literally called `!important`."""
        ig = CorpusIgnore.from_lines([r"\!important"], root=tmp_path)
        assert ig.matches(tmp_path / "!important", is_dir=False) is True


# ── path-handling edges ──────────────────────────────────────────────────


class TestPathHandling:
    def test_posix_separator_normalisation(self, tmp_path: Path) -> None:
        """``Path`` always normalises separators — patterns use POSIX."""
        ig = CorpusIgnore.from_lines(["a/b/c.txt"], root=tmp_path)
        assert ig.matches(tmp_path / "a" / "b" / "c.txt", is_dir=False) is True

    def test_matches_rejects_path_outside_root(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["*.heic"], root=tmp_path)
        with pytest.raises(ValueError, match="not under ignore-file root"):
            ig.matches(tmp_path.parent / "elsewhere.heic", is_dir=False)


# ── file I/O ─────────────────────────────────────────────────────────────


class TestFromFile:
    def test_from_file_missing_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            CorpusIgnore.from_file(tmp_path / "no-such-file")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX permission semantics: Windows ignores chmod(0) for the file owner",
    )
    def test_from_file_permission_error_propagates(self, tmp_path: Path) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root; chmod 000 doesn't restrict")
        ignore_path = tmp_path / "ignore"
        ignore_path.write_text("*.tmp\n", encoding="utf-8")
        ignore_path.chmod(0)
        try:
            with pytest.raises((OSError, PermissionError)):
                CorpusIgnore.from_file(ignore_path)
        finally:
            ignore_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_from_lines_round_trip_preserves_pattern_str(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.from_lines(["*.log", "!keep.log"], root=tmp_path)
        assert len(ig.patterns) == 2
        assert ig.patterns[0].pattern_str == "*.log"
        assert ig.patterns[1].pattern_str == "!keep.log"


# ── IgnoreStack composition ──────────────────────────────────────────────


class TestIgnoreStack:
    def test_global_then_local_local_wins(self, tmp_path: Path) -> None:
        """Global ignores `*.log`; local un-ignores `important.log`."""
        global_set = CorpusIgnore.from_lines(["*.log"], root=tmp_path)
        local = CorpusIgnore.from_lines(["!important.log"], root=tmp_path)
        stack = IgnoreStack((global_set, local))
        assert stack.matches(tmp_path / "foo.log", is_dir=False) is True
        assert stack.matches(tmp_path / "important.log", is_dir=False) is False

    def test_empty_stack_matches_nothing(self, tmp_path: Path) -> None:
        stack = IgnoreStack(())
        assert stack.matches(tmp_path / "anything.txt", is_dir=False) is False

    def test_stack_rejects_path_outside_any_set_root(self, tmp_path: Path) -> None:
        s = CorpusIgnore.from_lines(["*.log"], root=tmp_path)
        stack = IgnoreStack((s,))
        with pytest.raises(ValueError):
            stack.matches(tmp_path.parent / "outside.log", is_dir=False)

    def test_scan_root_overrides_set_root_for_global_match(self, tmp_path: Path) -> None:
        """Regression: global ignore lives outside the scan tree (e.g.
        `~/.config/corpus-forge/ignore`) but its patterns must still
        match files inside the scan tree. ``scan_root`` is the reference
        frame for relative-path computation when provided.
        """
        # Simulate: global file at one location, scan tree at another.
        global_dir = tmp_path / "config" / "corpus-forge"
        global_dir.mkdir(parents=True)
        scan_root = tmp_path / "vault"
        scan_root.mkdir()

        global_set = CorpusIgnore.from_lines(["*.heic"], root=global_dir)
        local_set = CorpusIgnore.empty(scan_root)
        stack = IgnoreStack((global_set, local_set))

        # Without scan_root: the global's root is `tmp_path/config/corpus-forge`
        # and the scan-tree path isn't under that → ValueError (this is the
        # bug we're regressing-tested against).
        scan_path = scan_root / "vacation.heic"
        with pytest.raises(ValueError, match="not under ignore reference root"):
            stack.matches(scan_path, is_dir=False)

        # With scan_root: the global pattern matches the scan-tree path.
        assert stack.matches(scan_path, is_dir=False, scan_root=scan_root) is True

        # And a path that doesn't match the global pattern returns False.
        scan_path_other = scan_root / "notes.md"
        assert stack.matches(scan_path_other, is_dir=False, scan_root=scan_root) is False


# ── global ignore lookup ─────────────────────────────────────────────────


class TestLoadGlobalIgnore:
    def test_honors_env_var_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ignore_path = tmp_path / "custom-global"
        ignore_path.write_text("*.tmp\n", encoding="utf-8")
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(ignore_path))
        ig = load_global_ignore()
        assert len(ig.patterns) == 1
        assert ig.patterns[0].pattern_str == "*.tmp"

    def test_env_empty_string_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", "")
        ig = load_global_ignore()
        assert ig.patterns == ()

    def test_default_path_loads_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HOME → tmp_path; place a file at ~/.config/corpus-forge/ignore."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("CF_GLOBAL_IGNORE_FILE", raising=False)
        # Path.home() reads HOME on POSIX. We need the module-level
        # constant to recompute; patch the resolver instead.
        config_dir = tmp_path / ".config" / "corpus-forge"
        config_dir.mkdir(parents=True)
        (config_dir / "ignore").write_text("*.cache\n", encoding="utf-8")
        monkeypatch.setattr(
            "corpus_forge.ignore._DEFAULT_GLOBAL_IGNORE",
            config_dir / "ignore",
        )
        ig = load_global_ignore()
        assert len(ig.patterns) == 1
        assert ig.patterns[0].pattern_str == "*.cache"

    def test_missing_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CF_GLOBAL_IGNORE_FILE", raising=False)
        monkeypatch.setattr(
            "corpus_forge.ignore._DEFAULT_GLOBAL_IGNORE",
            tmp_path / "does-not-exist",
        )
        ig = load_global_ignore()
        assert ig.patterns == ()

    def test_env_var_missing_path_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env var points at a non-existent file → empty (do not raise)."""
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(tmp_path / "nope"))
        ig = load_global_ignore()
        assert ig.patterns == ()


# ── local ignore lookup ──────────────────────────────────────────────────


class TestLoadLocalIgnore:
    def test_override_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_local_ignore(tmp_path, override=tmp_path / "absent")

    def test_auto_detect_present(self, tmp_path: Path) -> None:
        (tmp_path / ".corpusignore").write_text("*.bak\n", encoding="utf-8")
        ig = load_local_ignore(tmp_path)
        assert len(ig.patterns) == 1
        assert ig.patterns[0].pattern_str == "*.bak"

    def test_auto_detect_absent_returns_empty(self, tmp_path: Path) -> None:
        ig = load_local_ignore(tmp_path)
        assert ig.patterns == ()

    def test_override_overrides_auto_detect(self, tmp_path: Path) -> None:
        (tmp_path / ".corpusignore").write_text("*.auto\n", encoding="utf-8")
        custom = tmp_path / "custom.ignore"
        custom.write_text("*.custom\n", encoding="utf-8")
        ig = load_local_ignore(tmp_path, override=custom)
        # Should reflect the override, not the auto-detect.
        assert any(p.pattern_str == "*.custom" for p in ig.patterns)
        assert not any(p.pattern_str == "*.auto" for p in ig.patterns)


# ── frozen-dataclass invariants ──────────────────────────────────────────


class TestFrozenInvariants:
    def test_corpusignore_frozen(self, tmp_path: Path) -> None:
        ig = CorpusIgnore.empty(tmp_path)
        with pytest.raises((AttributeError, Exception)):
            ig.root = tmp_path.parent  # type: ignore[misc]

    def test_ignore_stack_frozen(self, tmp_path: Path) -> None:
        stack = IgnoreStack(())
        with pytest.raises((AttributeError, Exception)):
            stack.sets = ()  # type: ignore[misc]
