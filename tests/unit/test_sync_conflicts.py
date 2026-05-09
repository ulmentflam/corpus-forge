"""Unit tests for conflict_filename — canonical conflict naming."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

# The function does not exist yet — these tests must fail red.
from corpus_forge.sync.conflicts import conflict_filename

# ── Fixtures ───────────────────────────────────────────────────────────────

# Fixed timestamp for deterministic assertions.
FIXED_TS = datetime(2026, 5, 7, 22, 30, 45, tzinfo=UTC)

# Expected timestamp component (ISO 8601 basic, no colons).
EXPECTED_TS = "20260507T223045Z"


# ── Happy-path tests ───────────────────────────────────────────────────────


class TestConflictFilenameHappyPath:
    """The basic happy path — without provider."""

    def test_basic_format_no_provider(self):
        """conflict_filename(Path('notes/Foo.md'), host='macA', ts=...)
        → notes/Foo.conflict-macA-20260507T223045Z.md"""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"notes/Foo.conflict-macA-{EXPECTED_TS}.md")

    def test_basic_format_with_provider(self):
        """With provider: <stem>.conflict-<provider>-<host>-<ts><suffix>."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider="icloud")
        assert result == Path(f"notes/Foo.conflict-icloud-macA-{EXPECTED_TS}.md")

    def test_provider_none_explicit(self):
        """Explicit provider=None behaves like no provider."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider=None)
        assert result == Path(f"notes/Foo.conflict-macA-{EXPECTED_TS}.md")

    def test_absolute_original_path(self):
        """Absolute original path — stem and suffix preserved."""
        original = Path("/Users/alice/Vault/notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        expected = Path(f"/Users/alice/Vault/notes/Foo.conflict-macA-{EXPECTED_TS}.md")
        assert result == expected


# ── Suffix / extension tests ───────────────────────────────────────────────


class TestConflictFilenameSuffixes:
    """Different file extensions and edge-case suffixes."""

    def test_txt_extension(self):
        """A .txt file keeps its .txt suffix."""
        original = Path("data/records.txt")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"data/records.conflict-macA-{EXPECTED_TS}.txt")

    def test_no_extension(self):
        """A file with no extension: stem becomes the entire name."""
        original = Path("data/Makefile")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"data/Makefile.conflict-macA-{EXPECTED_TS}")

    def test_hidden_file_no_extension(self):
        """A dotfile (no extension) — stem is the full name."""
        original = Path(".gitignore")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f".gitignore.conflict-macA-{EXPECTED_TS}")

    def test_double_extension(self):
        """A file with a compound extension (.tar.gz)."""
        original = Path("archive/data.tar.gz")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"archive/data.tar.conflict-macA-{EXPECTED_TS}.gz")

    def test_md_with_multiple_dots(self):
        """A file like 'report.v2.md' — stem is 'report.v2', suffix is '.md'."""
        original = Path("docs/report.v2.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"docs/report.v2.conflict-macA-{EXPECTED_TS}.md")

    def test_dotfile_with_extension(self):
        """A file like '.env.local' — stem is '.env', suffix is '.local'."""
        original = Path("config/.env.local")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"config/.env.conflict-macA-{EXPECTED_TS}.local")


# ── Timestamp tests ────────────────────────────────────────────────────────


class TestConflictFilenameTimestamp:
    """Timestamp format: ISO 8601 basic, UTC, no colons, sortable."""

    def test_no_colons_in_timestamp(self):
        """The timestamp portion must not contain any colons."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        # Extract the timestamp from the filename
        stem_part = result.stem  # e.g. "Foo.conflict-macA-20260507T223045Z"
        ts_part = stem_part.split("-")[-1]
        assert ":" not in ts_part

    def test_trailing_z(self):
        """Timestamp must end with 'Z' to indicate UTC."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        stem_part = result.stem
        ts_part = stem_part.split("-")[-1]
        assert ts_part.endswith("Z")

    def test_sortable_ascending(self):
        """Two conflict filenames with different timestamps must sort correctly."""
        ts_earlier = datetime(2026, 5, 7, 10, 0, 0, tzinfo=UTC)
        ts_later = datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)
        original = Path("notes/Foo.md")
        fn_earlier = conflict_filename(original, host="macA", ts=ts_earlier)
        fn_later = conflict_filename(original, host="macA", ts=ts_later)
        assert fn_earlier < fn_later

    def test_different_hosts_same_ts_sortable(self):
        """Different hosts with the same timestamp must still be sortable."""
        original = Path("notes/Foo.md")
        fn_a = conflict_filename(original, host="alpha", ts=FIXED_TS)
        fn_b = conflict_filename(original, host="beta", ts=FIXED_TS)
        assert fn_a < fn_b or fn_b < fn_a  # deterministic sort by host name

    def test_midnight_boundary(self):
        """Timestamp at midnight (00:00:00) must format correctly."""
        ts_midnight = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=ts_midnight)
        assert "20260101T000000Z" in str(result)

    def test_leap_second_day(self):
        """Timestamp on Feb 29 (leap year) must format correctly."""
        ts_leap = datetime(2028, 2, 29, 12, 0, 0, tzinfo=UTC)
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=ts_leap)
        assert "20280229T120000Z" in str(result)


# ── Host name edge cases ───────────────────────────────────────────────────


class TestConflictFilenameHostEdgeCases:
    """Edge cases for the host parameter."""

    def test_long_host_name(self):
        """Very long host name must still produce a valid filename."""
        long_host = "a" * 255
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host=long_host, ts=FIXED_TS)
        # Must be a valid Path — no illegal characters
        stem = result.stem
        # Check the host appears in the stem
        assert long_host in stem

    def test_host_with_underscores(self):
        """Host name containing underscores."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="mac_server_01", ts=FIXED_TS)
        assert result == Path(f"notes/Foo.conflict-mac_server_01-{EXPECTED_TS}.md")

    def test_host_with_dashes(self):
        """Host name containing dashes."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="dev-host-2", ts=FIXED_TS)
        assert result == Path(f"notes/Foo.conflict-dev-host-2-{EXPECTED_TS}.md")

    def test_host_with_numbers(self):
        """Host name that is purely numeric."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="192168001", ts=FIXED_TS)
        assert result == Path(f"notes/Foo.conflict-192168001-{EXPECTED_TS}.md")

    def test_empty_host(self):
        """Empty host string should still produce a valid (if odd) filename."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="", ts=FIXED_TS)
        # Should not raise — just produces a filename with empty host segment (double dash)
        assert "conflict--" in str(result) and EXPECTED_TS in str(result)


# ── Provider edge cases ────────────────────────────────────────────────────


class TestConflictFilenameProviderEdgeCases:
    """Provider parameter variations."""

    def test_provider_icloud(self):
        """provider='icloud' inserts icloud between stem and host."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider="icloud")
        # Format: <stem>.conflict-<provider>-<host>-<ts><suffix>
        assert "conflict-icloud-macA-" in str(result)

    def test_provider_dropbox(self):
        """provider='dropbox' inserts dropbox between stem and host."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider="dropbox")
        assert "conflict-dropbox-macA-" in str(result)

    def test_provider_gdrive(self):
        """provider='gdrive' inserts gdrive between stem and host."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider="gdrive")
        assert "conflict-gdrive-macA-" in str(result)

    def test_provider_with_long_name(self):
        """Very long provider string must still produce a valid filename."""
        long_provider = "some-very-long-cloud-provider-name"
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider=long_provider)
        assert f"conflict-{long_provider}-macA-" in str(result)

    def test_provider_no_trailing_slash_in_path(self):
        """Provider must not introduce path separators."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider="icloud")
        # The stem should be "Foo.conflict-icloud-macA-20260507T223045Z"
        assert result.stem == f"Foo.conflict-icloud-macA-{EXPECTED_TS}"


# ── Path structure tests ───────────────────────────────────────────────────


class TestConflictFilenamePathStructure:
    """Verify directory structure is preserved correctly."""

    def test_preserves_parent_directory(self):
        """Parent directory of original must be preserved."""
        original = Path("deep/nested/dir/notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.parent == Path("deep/nested/dir/notes")

    def test_preserves_parent_with_provider(self):
        """Parent directory preserved even with provider."""
        original = Path("deep/nested/dir/notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS, provider="icloud")
        assert result.parent == Path("deep/nested/dir/notes")

    def test_relative_path_becomes_relative(self):
        """Relative input produces relative output."""
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert not result.is_absolute()

    def test_absolute_path_becomes_absolute(self):
        """Absolute input produces absolute output."""
        original = Path("/Users/alice/Vault/notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.is_absolute()


# ── Multi-dot file edge cases ──────────────────────────────────────────────


class TestConflictFilenameMultiDotFiles:
    """Files with multiple dots in the stem."""

    def test_multi_dot_stem(self):
        """'report.v2.md' → stem='report.v2'.conflict-<host>-<ts>.md."""
        original = Path("docs/report.v2.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        # stem should be "report.v2.conflict-macA-20260507T223045Z"
        assert result.stem == f"report.v2.conflict-macA-{EXPECTED_TS}"
        # suffix should be ".md"
        assert result.suffix == ".md"

    def test_just_dots(self):
        """A file named 'a.b.c' — stem='a.b', suffix='.c'."""
        original = Path("data/a.b.c")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.stem == f"a.b.conflict-macA-{EXPECTED_TS}"
        assert result.suffix == ".c"

    def test_dotfile_with_multiple_dots(self):
        """A dotfile with extension: '.config.v2' → stem='.config', suffix='.v2'."""
        original = Path(".config.v2")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.stem == f".config.conflict-macA-{EXPECTED_TS}"
        assert result.suffix == ".v2"


# ── Type / format tests ────────────────────────────────────────────────────


class TestConflictFilenameTypeHandling:
    """Wrong types and malformed inputs should raise."""

    def test_non_path_original_raises(self):
        """Passing a string as original should raise TypeError."""
        with pytest.raises(TypeError):
            conflict_filename("notes/Foo.md", host="macA", ts=FIXED_TS)  # type: ignore[arg-type]

    def test_non_string_host_raises(self):
        """Passing a non-string host should raise TypeError."""
        original = Path("notes/Foo.md")
        with pytest.raises(TypeError):
            conflict_filename(original, host=123, ts=FIXED_TS)  # type: ignore[arg-type]

    def test_non_datetime_ts_raises(self):
        """Passing a non-datetime ts should raise TypeError."""
        original = Path("notes/Foo.md")
        with pytest.raises(TypeError):
            conflict_filename(original, host="macA", ts="2026-05-07")  # type: ignore[arg-type]

    def test_non_string_provider_raises(self):
        """Passing a non-string provider should raise TypeError."""
        original = Path("notes/Foo.md")
        with pytest.raises(TypeError):
            conflict_filename(original, host="macA", ts=FIXED_TS, provider=123)  # type: ignore[arg-type]

    def test_returns_path(self):
        """Return type must be Path."""
        original = Path("notes/Foo.md")
        # This will fail because the function is not implemented yet
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert isinstance(result, Path)


# ── Idempotency / consistency tests ────────────────────────────────────────


class TestConflictFilenameConsistency:
    """Repeated calls with same args produce identical results."""

    def test_same_args_same_output(self):
        """Calling twice with identical args yields identical Path."""
        original = Path("notes/Foo.md")
        r1 = conflict_filename(original, host="macA", ts=FIXED_TS)
        r2 = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert r1 == r2

    def test_same_args_different_ts_different_output(self):
        """Different timestamps must produce different filenames."""
        original = Path("notes/Foo.md")
        ts2 = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
        r1 = conflict_filename(original, host="macA", ts=FIXED_TS)
        r2 = conflict_filename(original, host="macA", ts=ts2)
        assert r1 != r2

    def test_different_host_same_ts_different_output(self):
        """Different hosts must produce different filenames."""
        original = Path("notes/Foo.md")
        r1 = conflict_filename(original, host="macA", ts=FIXED_TS)
        r2 = conflict_filename(original, host="macB", ts=FIXED_TS)
        assert r1 != r2


# ── Regression hooks ───────────────────────────────────────────────────────


class TestConflictFilenameRegression:
    """Regression tests for known edge cases."""

    def test_stem_with_conflict_word(self):
        """A file already named 'conflict.md' must not double-conflict."""
        original = Path("notes/conflict.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        # stem should be "conflict.conflict-macA-20260507T223045Z"
        assert result.stem == f"conflict.conflict-macA-{EXPECTED_TS}"
        assert result.suffix == ".md"

    def test_path_with_spaces(self):
        """A file path containing spaces must produce a valid filename."""
        original = Path("My Documents/notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.parent == Path("My Documents/notes")
        assert result.suffix == ".md"

    def test_unicode_stem(self):
        """A file with Unicode in the stem must work."""
        original = Path("notes/日本語.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.suffix == ".md"
        assert "conflict-macA" in str(result)

    def test_unicode_path_components(self):
        """A path with Unicode directory names must work."""
        original = Path("ノート/文書/Foo.md")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result.suffix == ".md"


# ─────────────────────────────────────────────────────────────────────────────
# is_cloud_duplicate — detect cloud-sync conflict copies by filename pattern
# ─────────────────────────────────────────────────────────────────────────────

from corpus_forge.sync.conflicts import is_cloud_duplicate


class TestIsCloudDuplicateHappyPath:
    """Each provider's canonical pattern matches correctly."""

    def test_icloud_space_two(self):
        """Foo 2.md → (True, 'icloud', Foo.md)"""
        assert is_cloud_duplicate(Path("Foo 2.md")) == (True, "icloud", Path("Foo.md"))

    def test_icloud_space_three(self):
        """Foo 3.md → (True, 'icloud', Foo.md)"""
        assert is_cloud_duplicate(Path("Foo 3.md")) == (True, "icloud", Path("Foo.md"))

    def test_icloud_parens_two(self):
        """Foo (2).md → (True, 'icloud', Foo.md)"""
        assert is_cloud_duplicate(Path("Foo (2).md")) == (True, "icloud", Path("Foo.md"))

    def test_icloud_deeply_nested(self):
        """dir/sub/Foo 2.md → (True, 'icloud', dir/sub/Foo.md)"""
        assert is_cloud_duplicate(Path("dir/sub/Foo 2.md")) == (
            True,
            "icloud",
            Path("dir/sub/Foo.md"),
        )

    def test_dropbox_conflicted_copy(self):
        """Foo (MacBook-Pro's conflicted copy 2026-05-07).md → dropbox"""
        path = Path("Foo (MacBook-Pro's conflicted copy 2026-05-07).md")
        assert is_cloud_duplicate(path) == (True, "dropbox", Path("Foo.md"))

    def test_dropbox_different_host(self):
        """Foo (alice-pc's conflicted copy 2025-12-01).md → dropbox"""
        path = Path("Foo (alice-pc's conflicted copy 2025-12-01).md")
        assert is_cloud_duplicate(path) == (True, "dropbox", Path("Foo.md"))

    def test_gdrive_parens_one(self):
        """Foo (1).md → (True, 'gdrive', Foo.md) — (1) is gdrive, not icloud"""
        assert is_cloud_duplicate(Path("Foo (1).md")) == (True, "gdrive", Path("Foo.md"))

    def test_gdrive_conflict_dash(self):
        """Foo-conflict-2026-05-07-001.md → (True, 'gdrive', Foo.md)"""
        path = Path("Foo-conflict-2026-05-07-001.md")
        assert is_cloud_duplicate(path) == (True, "gdrive", Path("Foo.md"))

    def test_gdrive_conflict_variation(self):
        """Foo-conflict-2025-01-15-999.md → (True, 'gdrive', Foo.md)"""
        path = Path("Foo-conflict-2025-01-15-999.md")
        assert is_cloud_duplicate(path) == (True, "gdrive", Path("Foo.md"))

    def test_finder_copy(self):
        """Foo copy.md → (True, 'finder', Foo.md)"""
        assert is_cloud_duplicate(Path("Foo copy.md")) == (True, "finder", Path("Foo.md"))

    def test_finder_copy_two(self):
        """Foo copy 2.md → (True, 'finder', Foo.md)"""
        assert is_cloud_duplicate(Path("Foo copy 2.md")) == (True, "finder", Path("Foo.md"))

    def test_finder_copy_three(self):
        """Foo copy 3.md → (True, 'finder', Foo.md)"""
        assert is_cloud_duplicate(Path("Foo copy 3.md")) == (True, "finder", Path("Foo.md"))


class TestIsCloudDuplicateNoMatch:
    """Paths that should NOT match any duplicate pattern."""

    def test_plain_file(self):
        """Foo.md → (False, None, None)"""
        assert is_cloud_duplicate(Path("Foo.md")) == (False, None, None)

    def test_single_copy_word(self):
        """copy.md → (False, None, None) — 'copy' is the entire stem, not a suffix"""
        assert is_cloud_duplicate(Path("copy.md")) == (False, None, None)

    def test_nested_no_match(self):
        """dir/sub/Foo.md → (False, None, None)"""
        assert is_cloud_duplicate(Path("dir/sub/Foo.md")) == (False, None, None)

    def test_copy_as_substring_not_suffix(self):
        """photocopy.md → (False, None, None) — 'copy' not at stem end"""
        assert is_cloud_duplicate(Path("photocopy.md")) == (False, None, None)

    def test_absolute_no_match(self):
        """/usr/Foo.md → (False, None, None)"""
        assert is_cloud_duplicate(Path("/usr/Foo.md")) == (False, None, None)


class TestIsCloudDuplicateEdgeCases:
    """Boundaries and edge cases."""

    def test_no_extension(self):
        """Foo 2 (no extension) → (True, 'icloud', Foo)"""
        assert is_cloud_duplicate(Path("Foo 2")) == (True, "icloud", Path("Foo"))

    def test_different_extension(self):
        """Foo 2.txt → (True, 'icloud', Foo.txt)"""
        assert is_cloud_duplicate(Path("Foo 2.txt")) == (True, "icloud", Path("Foo.txt"))

    def test_unicode_stem(self):
        """日本語 2.md → (True, 'icloud', 日本語.md)"""
        assert is_cloud_duplicate(Path("日本語 2.md")) == (True, "icloud", Path("日本語.md"))

    def test_absolute_path_icloud(self):
        """/abs/Foo 2.md → (True, 'icloud', /abs/Foo.md)"""
        assert is_cloud_duplicate(Path("/abs/Foo 2.md")) == (
            True,
            "icloud",
            Path("/abs/Foo.md"),
        )

    def test_dropbox_nested(self):
        """dir/Foo (host's conflicted copy date).md → dropbox, dir/Foo.md"""
        path = Path("dir/sub/Foo (mbp's conflicted copy 2026-05-07).md")
        assert is_cloud_duplicate(path) == (
            True,
            "dropbox",
            Path("dir/sub/Foo.md"),
        )

    def test_gdrive_conflict_nested(self):
        """dir/Foo-conflict-2026-05-07-001.md → gdrive, dir/Foo.md"""
        path = Path("dir/sub/Foo-conflict-2026-05-07-001.md")
        assert is_cloud_duplicate(path) == (
            True,
            "gdrive",
            Path("dir/sub/Foo.md"),
        )

    def test_dropbox_with_unicode(self):
        """日本語 (host's conflicted copy date).md → dropbox, 日本語.md"""
        path = Path("日本語 (mbp's conflicted copy 2026-05-07).md")
        assert is_cloud_duplicate(path) == (True, "dropbox", Path("日本語.md"))


class TestIsCloudDuplicatePrecedence:
    """When a path matches multiple patterns, correct provider wins."""

    def test_gdrive_beats_icloud_on_parens_one(self):
        """Foo (1).md matches both gdrive and icloud — gdrive wins"""
        result = is_cloud_duplicate(Path("Foo (1).md"))
        assert result[1] == "gdrive"

    def test_dropbox_beats_finder(self):
        """ "copy" in hostname doesn't falsely match finder"""
        path = Path("Foo (mbp's conflicted copy 2026-05-07).md")
        result = is_cloud_duplicate(path)
        assert result[1] == "dropbox"


class TestIsCloudDuplicateReturnType:
    """Return value structure."""

    def test_returns_tuple_len_three(self):
        """Return is a 3-tuple."""
        for path in [Path("Foo.md"), Path("Foo 2.md")]:
            r = is_cloud_duplicate(path)
            assert isinstance(r, tuple) and len(r) == 3

    def test_match_flag_is_bool(self):
        """First element is bool."""
        assert isinstance(is_cloud_duplicate(Path("Foo 2.md"))[0], bool)
        assert isinstance(is_cloud_duplicate(Path("Foo.md"))[0], bool)

    def test_match_provider_is_str(self):
        """Second element is str when matched."""
        assert isinstance(is_cloud_duplicate(Path("Foo 2.md"))[1], str)

    def test_match_canonical_is_path(self):
        """Third element is Path when matched."""
        assert isinstance(is_cloud_duplicate(Path("Foo 2.md"))[2], Path)

    def test_no_match_provider_none(self):
        """Second element is None when not matched."""
        assert is_cloud_duplicate(Path("Foo.md"))[1] is None

    def test_no_match_canonical_none(self):
        """Third element is None when not matched."""
        assert is_cloud_duplicate(Path("Foo.md"))[2] is None
