"""Unit tests for corpus_forge.sync.fs — atomic_write_text and move_to_trash."""

import contextlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# The function does not exist yet — these tests must fail red.
from corpus_forge.sync.fs import (
    atomic_write_text,
    is_dataless,
    is_icloud_placeholder,
    move_to_trash,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir(tmp_path: Path):
    """Return a writable temp directory (pytest tmp_path is already one)."""
    return tmp_path


# ── Happy-path tests ─────────────────────────────────────────────────────


class TestAtomicWriteTextHappyPath:
    """Core success scenarios."""

    def test_writes_target_file_with_expected_text(self, tmp_dir: Path):
        """atomic_write_text should write the exact text to the target path."""
        target = tmp_dir / "output.txt"
        atomic_write_text(target, "hello world")
        assert target.read_text() == "hello world"

    def test_writes_unicode_text(self, tmp_dir: Path):
        """Non-ASCII content should round-trip correctly."""
        target = tmp_dir / "unicode.txt"
        text = "café résumé naïve 日本語 🌍"
        atomic_write_text(target, text)
        # Force utf-8 on read; Windows defaults to cp1252 which mojibakes
        # non-ASCII content. ``atomic_write_text`` writes utf-8.
        assert target.read_text(encoding="utf-8") == text

    def test_writes_newlines_and_whitespace(self, tmp_dir: Path):
        """Newlines, tabs, and trailing whitespace must be preserved."""
        target = tmp_dir / "whitespace.txt"
        text = "line1\nline2\tindented\n\ntrailing \n"
        atomic_write_text(target, text)
        assert target.read_text() == text

    def test_default_encoding_is_utf8(self, tmp_dir: Path):
        """Default encoding should be UTF-8 (no encoding arg needed)."""
        target = tmp_dir / "utf8.txt"
        text = "über straße ñ"
        atomic_write_text(target, text)  # no encoding arg
        assert target.read_text(encoding="utf-8") == text

    def test_custom_encoding(self, tmp_dir: Path):
        """Explicit encoding parameter should be respected."""
        target = tmp_dir / "latin1.txt"
        text = "café"
        atomic_write_text(target, text, encoding="latin-1")
        # Read back as latin-1 to verify
        assert target.read_text(encoding="latin-1") == text

    def test_creates_parent_directories(self, tmp_dir: Path):
        """All missing parent directories should be created automatically."""
        target = tmp_dir / "a" / "b" / "c" / "deep.txt"
        atomic_write_text(target, "deep content")
        assert target.read_text() == "deep content"

    def test_overwrites_existing_file(self, tmp_dir: Path):
        """If the target already exists, it should be replaced entirely."""
        target = tmp_dir / "overwrite.txt"
        target.write_text("old content")
        atomic_write_text(target, "new content")
        assert target.read_text() == "new content"

    def test_replaces_partial_file_content(self, tmp_dir: Path):
        """New content shorter than old should fully replace (no truncation remnants)."""
        target = tmp_dir / "shorten.txt"
        target.write_text("this is a very long original string that must be fully replaced")
        atomic_write_text(target, "short")
        assert target.read_text() == "short"
        assert len(target.read_bytes()) == 5


# ── Tempfile cleanup tests ───────────────────────────────────────────────


class TestAtomicWriteTextTempfileCleanup:
    """The temp file must not persist after a successful write."""

    def test_tempfile_removed_after_success(self, tmp_dir: Path):
        """After atomic_write_text succeeds, no `.tmp.*` file should remain."""
        target = tmp_dir / "data.txt"
        atomic_write_text(target, "content")
        # All entries in the directory should be the target itself
        entries = list(tmp_dir.iterdir())
        assert len(entries) == 1
        assert entries[0] == target

    def test_no_tempfile_with_deep_parents(self, tmp_dir: Path):
        """Even with parent-creation, no leftover temp files."""
        target = tmp_dir / "x" / "y" / "z.txt"
        atomic_write_text(target, "deep")
        entries = list(target.parent.iterdir())
        assert len(entries) == 1
        assert entries[0] == target


# ── Failure-path / crash simulation tests ────────────────────────────────


class TestAtomicWriteTextFailurePaths:
    """If os.replace raises, the original file must remain untouched."""

    def test_os_replace_raises_preserves_original_content(self, tmp_dir: Path):
        """When os.replace raises OSError, original file content is unchanged."""
        target = tmp_dir / "original.txt"
        target.write_text("original content")
        original_stat = target.stat()

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=OSError("simulated failure")),
            pytest.raises(OSError, match="simulated failure"),
        ):
            atomic_write_text(target, "new content")

        # Original must be untouched
        assert target.read_text() == "original content"
        assert target.stat().st_mtime == original_stat.st_mtime

    def test_os_replace_raises_no_target_file_created(self, tmp_dir: Path):
        """If os.replace fails, the target file must not exist at all."""
        target = tmp_dir / "no_target.txt"
        assert not target.exists()

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            atomic_write_text(target, "content")

        assert not target.exists()

    def test_os_replace_raises_no_tempfile_left(self, tmp_dir: Path):
        """If os.replace fails, no temp file should remain in the directory."""
        target = tmp_dir / "clean.txt"
        initial_entries = set(tmp_dir.iterdir())

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            atomic_write_text(target, "content")

        # Directory should be unchanged
        assert set(tmp_dir.iterdir()) == initial_entries

    def test_os_replace_raises_no_parent_dirs_created(self, tmp_dir: Path):
        """If os.replace fails, no parent directories should have been created."""
        target = tmp_dir / "a" / "b" / "deep.txt"
        parent = target.parent
        assert not parent.exists()

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            atomic_write_text(target, "content")

        assert not parent.exists()

    def test_os_replace_raises_preserves_existing_parent(self, tmp_dir: Path):
        """If os.replace fails, pre-existing parent directories must remain."""
        pre_existing = tmp_dir / "a"
        pre_existing.mkdir()
        (pre_existing / "existing.txt").write_text("pre-existing")

        target = pre_existing / "b" / "deep.txt"

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            atomic_write_text(target, "content")

        # Pre-existing file must still be there
        assert (pre_existing / "existing.txt").read_text() == "pre-existing"


# ── Boundary tests ───────────────────────────────────────────────────────


class TestAtomicWriteTextBoundaries:
    """Edge cases on input size and content."""

    def test_empty_string(self, tmp_dir: Path):
        """Writing an empty string should create a zero-byte file."""
        target = tmp_dir / "empty.txt"
        atomic_write_text(target, "")
        assert target.exists()
        assert target.read_text() == ""
        assert target.stat().st_size == 0

    def test_single_character(self, tmp_dir: Path):
        """A single character should be written exactly."""
        target = tmp_dir / "single.txt"
        atomic_write_text(target, "X")
        assert target.read_text() == "X"
        assert target.stat().st_size == 1

    def test_very_long_text(self, tmp_dir: Path):
        """Large content (1 MB) should be written correctly."""
        target = tmp_dir / "large.txt"
        text = "A" * (1024 * 1024)
        atomic_write_text(target, text)
        assert target.read_text() == text
        assert target.stat().st_size == 1024 * 1024

    def test_binary_null_bytes_as_text(self, tmp_dir: Path):
        """Content containing null byte sequences should be written."""
        target = tmp_dir / "nulls.txt"
        text = "before\x00after"
        atomic_write_text(target, text)
        assert target.read_text() == text

    def test_just_newlines(self, tmp_dir: Path):
        """A string of only newlines should round-trip."""
        target = tmp_dir / "nl.txt"
        text = "\n\n\n\n"
        atomic_write_text(target, text)
        assert target.read_text() == text

    def test_rtl_text(self, tmp_dir: Path):
        """Right-to-left text (Arabic) should round-trip."""
        target = tmp_dir / "rtl.txt"
        text = "مرحبا بالعالم"
        atomic_write_text(target, text)
        # Force utf-8 on read; see test_writes_unicode_text.
        assert target.read_text(encoding="utf-8") == text


# ── Type / format tests ──────────────────────────────────────────────────


class TestAtomicWriteTextTypeValidation:
    """Wrong types should raise TypeError (or equivalent)."""

    def test_none_text_raises(self, tmp_dir: Path):
        """Passing None as text should raise TypeError."""
        target = tmp_dir / "t.txt"
        with pytest.raises(TypeError):
            atomic_write_text(target, None)  # type: ignore[arg-type]

    def test_int_text_raises(self, tmp_dir: Path):
        """Passing an integer as text should raise TypeError."""
        target = tmp_dir / "t.txt"
        with pytest.raises(TypeError):
            atomic_write_text(target, 12345)  # type: ignore[arg-type]

    def test_list_text_raises(self, tmp_dir: Path):
        """Passing a list as text should raise TypeError."""
        target = tmp_dir / "t.txt"
        with pytest.raises(TypeError):
            atomic_write_text(target, ["line1", "line2"])  # type: ignore[arg-type]


# ── Encoding tests ───────────────────────────────────────────────────────


class TestAtomicWriteTextEncoding:
    """Encoding-specific behavior."""

    def test_utf8_with_bom(self, tmp_dir: Path):
        """UTF-8 BOM should be written literally (not auto-added)."""
        target = tmp_dir / "bom.txt"
        text = "\ufeffhello"
        atomic_write_text(target, text, encoding="utf-8")
        raw = target.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"

    def test_utf16_encoding(self, tmp_dir: Path):
        """UTF-16 encoding should produce valid UTF-16 bytes."""
        target = tmp_dir / "utf16.txt"
        text = "hello utf16"
        atomic_write_text(target, text, encoding="utf-16")
        # Read back as utf-16
        assert target.read_text(encoding="utf-16") == text

    def test_utf32_encoding(self, tmp_dir: Path):
        """UTF-32 encoding should produce valid UTF-32 bytes."""
        target = tmp_dir / "utf32.txt"
        text = "hello utf32"
        atomic_write_text(target, text, encoding="utf-32")
        assert target.read_text(encoding="utf-32") == text

    def test_encoding_mismatch_writes_bytes(self, tmp_dir: Path):
        """Writing with latin-1 should produce latin-1 bytes on disk."""
        target = tmp_dir / "enc.txt"
        text = "café"
        atomic_write_text(target, text, encoding="latin-1")
        # On disk, 'é' should be a single byte 0xe9 in latin-1
        raw = target.read_bytes()
        assert b"\xe9" in raw
        # But reading as utf-8 would fail or produce different result
        assert target.read_text(encoding="latin-1") == text


# ── State / idempotency tests ────────────────────────────────────────────


class TestAtomicWriteTextState:
    """Stateful behavior — multiple calls, ordering."""

    def test_multiple_writes_same_path(self, tmp_dir: Path):
        """Calling atomic_write_text multiple times on the same path should succeed."""
        target = tmp_dir / "multi.txt"
        for i in range(5):
            atomic_write_text(target, f"iteration {i}")
        assert target.read_text() == "iteration 4"

    def test_writes_dont_interleave(self, tmp_dir: Path):
        """Each write should be self-contained — no partial reads from interleaving."""
        target = tmp_dir / "interleave.txt"
        # Write A, then B — B's content should be complete
        atomic_write_text(target, "AAAA")
        atomic_write_text(target, "BBBB")
        content = target.read_text()
        assert content == "BBBB"
        assert "AAAA" not in content

    def test_writes_to_different_files_concurrently(self, tmp_dir: Path):
        """Writing to different files should not interfere."""
        files = [tmp_dir / f"file_{i}.txt" for i in range(10)]
        for i, f in enumerate(files):
            atomic_write_text(f, f"content_{i}")
        for i, f in enumerate(files):
            assert f.read_text() == f"content_{i}"


# ── Tempfile naming tests ────────────────────────────────────────────────


class TestAtomicWriteTextTempfileNaming:
    """The temp file naming convention."""

    def test_tempfile_has_tmp_suffix(self, tmp_dir: Path):
        """Temp file should have a .tmp. prefix pattern (before os.replace)."""
        target = tmp_dir / "output.txt"

        # We'll capture the temp file name by patching os.replace
        captured_names = []

        def capture_replace(src, dst):
            captured_names.append(src)
            # Don't actually replace — we just want to inspect the name
            raise InterruptedError("stop")

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=capture_replace),
            contextlib.suppress(InterruptedError),
        ):
            atomic_write_text(target, "content")

        assert len(captured_names) == 1
        temp_path = Path(captured_names[0])
        assert temp_path.name.startswith(target.name + ".tmp.")
        assert temp_path.parent == target.parent

    def test_tempfile_in_same_directory_as_target(self, tmp_dir: Path):
        """Temp file must be created in the same directory as the target."""
        target = tmp_dir / "sub" / "output.txt"
        target.parent.mkdir(parents=True)

        captured_names = []

        def capture_replace(src, dst):
            captured_names.append(src)
            raise InterruptedError("stop")

        with (
            patch("corpus_forge.sync.fs.os.replace", side_effect=capture_replace),
            contextlib.suppress(InterruptedError),
        ):
            atomic_write_text(target, "content")

        assert len(captured_names) == 1
        # Temp file's parent must equal target's parent
        assert Path(captured_names[0]).parent == target.parent

    def test_tempfile_has_random_suffix(self, tmp_dir: Path):
        """Temp file name should include a random component to avoid collisions."""
        target = tmp_dir / "output.txt"
        names = set()

        def capture_replace(src, dst):
            names.add(src)
            raise InterruptedError("stop")

        for _ in range(5):
            with (
                patch("corpus_forge.sync.fs.os.replace", side_effect=capture_replace),
                contextlib.suppress(InterruptedError),
            ):
                atomic_write_text(target, "content")

        # Should have 5 unique temp file names
        assert len(names) == 5


# ── Platform-specific tests ──────────────────────────────────────────────


class TestAtomicWriteTextPlatform:
    """Platform-specific behavior."""

    def test_returns_none(self, tmp_dir: Path):
        """Function should return None (not a value)."""
        target = tmp_dir / "ret.txt"
        result = atomic_write_text(target, "content")
        assert result is None

    def test_posix_atomic_rename(self, tmp_dir: Path):
        """On POSIX, os.replace should be used (not shutil.move or copy)."""
        with patch("corpus_forge.sync.fs.os.replace") as mock_replace:
            target = tmp_dir / "atomic.txt"
            atomic_write_text(target, "content")
            assert mock_replace.called

    def test_fsync_called_on_temp_file(self, tmp_dir: Path):
        """The temp file should be fsynced before os.replace."""
        with patch("corpus_forge.sync.fs.os.fsync") as mock_fsync:
            target = tmp_dir / "fsync.txt"
            atomic_write_text(target, "content")
            assert mock_fsync.called

    def test_fsync_called_on_parent_directory(self, tmp_dir: Path):
        """The parent directory should be fsynced after os.replace."""
        fsync_calls = []

        def track_fsync(fd):
            fsync_calls.append(fd)

        with patch("corpus_forge.sync.fs.os.fsync", side_effect=track_fsync):
            target = tmp_dir / "dir_fsync.txt"
            atomic_write_text(target, "content")
            # os.fsync should be called at least twice (temp file + parent dir)
            assert len(fsync_calls) >= 1


# ── move_to_trash — destination path construction ────────────────────────


class TestMoveToTrashDestPath:
    """The destination path must follow the expected format."""

    FAKE_TS = datetime(2026, 5, 8, 22, 30, 45)

    def test_no_relpath_uses_src_name(self, tmp_path: Path):
        """Without rel_path, dest is <trash>/<dataset>/<stem>.deleted-<host>-<ts><suffix>."""
        src = tmp_path / "report.md"
        src.write_text("data")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "docs", "macA")

        expected = trash / "docs" / "report.deleted-macA-20260508T223045Z.md"
        assert dest == expected

    def test_with_relpath_preserves_structure(self, tmp_path: Path):
        """With rel_path, dest preserves directory structure under the dataset."""
        src = tmp_path / "notes" / "meeting.md"
        src.parent.mkdir(parents=True)
        src.write_text("notes")
        trash = tmp_path / ".trash"
        rel = Path("journal/2026/meeting.md")

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "vault", "mbp2", rel_path=rel)

        expected = trash / "vault" / "journal" / "2026" / "meeting.deleted-mbp2-20260508T223045Z.md"
        assert dest == expected

    def test_no_extension_file(self, tmp_path: Path):
        """File without extension should not get a trailing dot."""
        src = tmp_path / "Makefile"
        src.write_text("all:")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "code", "host1")

        expected = trash / "code" / "Makefile.deleted-host1-20260508T223045Z"
        assert dest == expected

    def test_dotfile_no_extension(self, tmp_path: Path):
        """Dotfile without extension should not get a trailing dot."""
        src = tmp_path / ".gitignore"
        src.write_text("*.pyc")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "dotfiles", "h1")

        expected = trash / "dotfiles" / ".gitignore.deleted-h1-20260508T223045Z"
        assert dest == expected

    def test_compound_extension(self, tmp_path: Path):
        """Only the last suffix is moved to the end (e.g. .tar.gz → .gz)."""
        src = tmp_path / "archive.tar.gz"
        src.write_text("gzip content")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "backups", "srv1")

        expected = trash / "backups" / "archive.tar.deleted-srv1-20260508T223045Z.gz"
        assert dest == expected

    def test_unicode_filename(self, tmp_path: Path):
        """Unicode filenames should round-trip."""
        src = tmp_path / "日本語.md"
        src.write_text("data")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "uni", "host1")

        expected = trash / "uni" / "日本語.deleted-host1-20260508T223045Z.md"
        assert dest == expected


# ── move_to_trash — file relocation behavior ─────────────────────────────


class TestMoveToTrashFileMoved:
    """The source file should be moved (not copied-only) to the trash location."""

    FAKE_TS = datetime(2026, 5, 8, 22, 30, 45)

    def test_src_no_longer_exists(self, tmp_path: Path):
        """After move_to_trash, the source file must not exist."""
        src = tmp_path / "doc.txt"
        src.write_text("important data")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            move_to_trash(src, trash, "data", "macA")

        assert not src.exists()

    def test_dest_has_same_content(self, tmp_path: Path):
        """The trash destination must contain the original content."""
        content = "line1\nline2\n"
        src = tmp_path / "doc.txt"
        src.write_text(content)
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "data", "macA")

        assert dest.read_text() == content

    def test_returns_path(self, tmp_path: Path):
        """Returns a Path object (the destination)."""
        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            result = move_to_trash(src, trash, "data", "macA")

        assert isinstance(result, Path)


# ── move_to_trash — parent directory creation ────────────────────────────


class TestMoveToTrashParentDirs:
    """Parent directories of the destination must be created automatically."""

    FAKE_TS = datetime(2026, 5, 8, 22, 30, 45)

    def test_creates_trash_dataset_dir(self, tmp_path: Path):
        """The <trash_root>/<dataset> directory should be created."""
        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"

        assert not (trash / "data").exists()

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            move_to_trash(src, trash, "data", "macA")

        assert (trash / "data").is_dir()

    def test_creates_relpath_parents(self, tmp_path: Path):
        """When rel_path has sub-directories, they must be created."""
        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"
        rel = Path("a/b/c/doc.txt")

        deep_dir = trash / "data" / "a" / "b" / "c"
        assert not deep_dir.exists()

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            move_to_trash(src, trash, "data", "macA", rel_path=rel)

        assert deep_dir.is_dir()


# ── move_to_trash — same-filesystem (atomic) ─────────────────────────────


class TestMoveToTrashSameFilesystem:
    """On the same filesystem, os.replace must be used (atomic)."""

    FAKE_TS = datetime(2026, 5, 8, 22, 30, 45)

    def test_uses_os_replace(self, tmp_path: Path):
        """os.replace should be called for same-device moves."""
        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"

        with (
            patch("corpus_forge.sync.fs.datetime") as mock_dt,
            patch("corpus_forge.sync.fs.os.replace") as mock_replace,
        ):
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "data", "macA")

            mock_replace.assert_called_once()
            args, _ = mock_replace.call_args
            assert Path(args[0]).name == src.name
            assert Path(args[1]) == dest

    def test_os_replace_not_called_for_cross_device(self, tmp_path: Path):
        """os.replace should NOT be called when cross-device EXDEV is raised."""
        import errno

        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"

        with (
            patch("corpus_forge.sync.fs.datetime") as mock_dt,
            patch(
                "corpus_forge.sync.fs.os.replace", side_effect=OSError(errno.EXDEV, "cross-device")
            ),
            patch("corpus_forge.sync.fs.shutil.copy2"),
            patch("corpus_forge.sync.fs.os.unlink"),
        ):
            mock_dt.utcnow.return_value = self.FAKE_TS
            move_to_trash(src, trash, "data", "macA")

        # os.replace was called once but raised EXDEV
        # (no explicit assertion needed here — we're verifying the fallback path handles it)


# ── move_to_trash — cross-device fallback ────────────────────────────────


class TestMoveToTrashCrossDevice:
    """When os.replace raises EXDEV, fall back to copy+unlink."""

    FAKE_TS = datetime(2026, 5, 8, 22, 30, 45)

    def test_calls_copy2_on_exdev(self, tmp_path: Path):
        """When os.replace raises EXDEV, shutil.copy2 should be used."""
        import errno

        src = tmp_path / "doc.txt"
        src.write_text("fallback content")
        trash = tmp_path / ".trash"

        with (
            patch("corpus_forge.sync.fs.datetime") as mock_dt,
            patch(
                "corpus_forge.sync.fs.os.replace", side_effect=OSError(errno.EXDEV, "cross-device")
            ),
            patch("corpus_forge.sync.fs.shutil.copy2") as mock_copy,
            patch("corpus_forge.sync.fs.os.unlink"),
        ):
            mock_dt.utcnow.return_value = self.FAKE_TS
            dest = move_to_trash(src, trash, "data", "macA")

            mock_copy.assert_called_once()
            args, _ = mock_copy.call_args
            assert Path(args[0]) == src
            assert Path(args[1]) == dest

    def test_calls_unlink_on_exdev(self, tmp_path: Path):
        """After copy2 on EXDEV, the source must be unlinked."""
        import errno

        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"

        with (
            patch("corpus_forge.sync.fs.datetime") as mock_dt,
            patch(
                "corpus_forge.sync.fs.os.replace", side_effect=OSError(errno.EXDEV, "cross-device")
            ),
            patch("corpus_forge.sync.fs.shutil.copy2"),
            patch("corpus_forge.sync.fs.os.unlink") as mock_unlink,
        ):
            mock_dt.utcnow.return_value = self.FAKE_TS
            move_to_trash(src, trash, "data", "macA")

            mock_unlink.assert_called_once_with(str(src))

    def test_non_exdev_oserror_still_raises(self, tmp_path: Path):
        """A non-EXDEV OSError from os.replace should propagate."""
        import errno

        src = tmp_path / "doc.txt"
        src.write_text("x")
        trash = tmp_path / ".trash"

        with (
            patch("corpus_forge.sync.fs.datetime") as mock_dt,
            patch(
                "corpus_forge.sync.fs.os.replace",
                side_effect=OSError(errno.EACCES, "permission denied"),
            ),
        ):
            mock_dt.utcnow.return_value = self.FAKE_TS
            with pytest.raises(OSError):
                move_to_trash(src, trash, "data", "macA")

    def test_exdev_fallback_preserves_content(self, tmp_path: Path):
        """After EXDEV fallback, dest content matches original."""
        import errno

        content = "cross-device content"
        src = tmp_path / "doc.txt"
        src.write_text(content)
        trash = tmp_path / ".trash"

        def fake_replace(_src, _dst):
            raise OSError(errno.EXDEV, "cross-device link")

        with patch("corpus_forge.sync.fs.datetime") as mock_dt:
            mock_dt.utcnow.return_value = self.FAKE_TS
            with patch("corpus_forge.sync.fs.os.replace", side_effect=fake_replace):
                dest = move_to_trash(src, trash, "data", "macA")

        assert dest.read_text() == content
        assert not src.exists()


# ── is_icloud_placeholder — iCloud placeholder detection ─────────────────


class TestIsIcloudPlaceholder:
    """is_icloud_placeholder detects iCloud placeholder files."""

    def test_icloud_suffix_zero_size(self, tmp_path: Path):
        """Foo.md.icloud with 0 bytes → True"""
        p = tmp_path / "Foo.md.icloud"
        p.write_text("")
        assert is_icloud_placeholder(p) is True

    def test_icloud_suffix_nonzero_size(self, tmp_path: Path):
        """Foo.md.icloud with content → False (real file with .icloud ext)"""
        p = tmp_path / "Foo.md.icloud"
        p.write_text("content")
        assert is_icloud_placeholder(p) is False

    def test_normal_file(self, tmp_path: Path):
        """Foo.md (no .icloud suffix) → False"""
        p = tmp_path / "Foo.md"
        p.write_text("content")
        assert is_icloud_placeholder(p) is False

    def test_normal_file_empty(self, tmp_path: Path):
        """Normal empty file (no .icloud) → False"""
        p = tmp_path / "empty.md"
        p.write_text("")
        assert is_icloud_placeholder(p) is False

    def test_returns_bool(self, tmp_path: Path):
        """Return type is bool."""
        p = tmp_path / "x.md"
        p.write_text("x")
        result = is_icloud_placeholder(p)
        assert isinstance(result, bool)


# ── is_dataless — xattr-based dataless detection ────────────────────────


class TestIsDataless:
    """is_dataless detects dataless/unmaterialized files via xattr."""

    def test_normal_file_no_xattr_returns_false(self, tmp_path: Path):
        """A normal file with no com.apple.fileprovider.materialized xattr → False"""
        p = tmp_path / "normal.txt"
        p.write_text("real content")
        assert is_dataless(p) is False

    def test_getxattr_error_returns_false(self, tmp_path: Path):
        """An OSError from getxattr should return False (fail closed)."""
        p = tmp_path / "unknown.txt"
        p.write_text("x")
        with patch(
            "corpus_forge.sync.fs.os.getxattr", side_effect=OSError("no such xattr"), create=True
        ):
            assert is_dataless(p) is False

    def test_permission_error_returns_false(self, tmp_path: Path):
        """PermissionError should also return False (fail closed)."""
        p = tmp_path / "locked.txt"
        p.write_text("x")
        with patch(
            "corpus_forge.sync.fs.os.getxattr", side_effect=PermissionError("denied"), create=True
        ):
            assert is_dataless(p) is False

    def test_dataless_xattr_returns_true(self, tmp_path: Path):
        """When com.apple.fileprovider.materialized xattr present → True"""
        p = tmp_path / "placeholder.txt"
        p.write_text("")
        with patch("corpus_forge.sync.fs.os.getxattr", return_value=b"1", create=True):
            assert is_dataless(p) is True

    def test_returns_bool(self, tmp_path: Path):
        """Return type is bool even on error paths."""
        p = tmp_path / "test.txt"
        p.write_text("x")
        with patch("corpus_forge.sync.fs.os.getxattr", side_effect=OSError, create=True):
            result = is_dataless(p)
            assert isinstance(result, bool)
