"""Unit tests for conflict_filename — canonical conflict naming."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

# The function does not exist yet — these tests must fail red.
from corpus_forge.sync.conflicts import conflict_filename


# ── Fixtures ───────────────────────────────────────────────────────────────

# Fixed timestamp for deterministic assertions.
FIXED_TS = datetime(2026, 5, 7, 22, 30, 45, tzinfo=timezone.utc)

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
        """A file with a compound extension (.tar.gz) → suffix is .gz."""
        original = Path("archive/data.tar.gz")
        result = conflict_filename(original, host="macA", ts=FIXED_TS)
        assert result == Path(f"archive/data.conflict-macA-{EXPECTED_TS}.tar.gz")

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
        ts_earlier = datetime(2026, 5, 7, 10, 0, 0, tzinfo=timezone.utc)
        ts_later = datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc)
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
        ts_midnight = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        original = Path("notes/Foo.md")
        result = conflict_filename(original, host="macA", ts=ts_midnight)
        assert "20260101T000000Z" in str(result)

    def test_leap_second_day(self):
        """Timestamp on Feb 29 (leap year) must format correctly."""
        ts_leap = datetime(2028, 2, 29, 12, 0, 0, tzinfo=timezone.utc)
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
        # Should not raise — just produces a filename with empty host segment
        assert ".conflict-.md" in str(result)


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
        ts2 = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
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
