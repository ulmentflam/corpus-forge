"""Unit tests for sources/base.py — WatchedSource extended coverage."""

from pathlib import Path

import pytest

from corpus_forge.sources.base import RawDocument, WatchedSource


class MockSource(WatchedSource):
    """Concrete implementation of WatchedSource for testing."""

    name = "mock"
    dataset_kind = "text"

    def discover(self):
        return iter([])

    def parse(self, path):
        return RawDocument(
            source_uri=f"mock://{path.name}",
            content_hash="abc",
            text="",
            title=None,
            modified_at=0.0,
            metadata={},
            labels=[],
        )


class TestWatchedSourceInit:
    def test_init_sets_root(self, temp_dir):
        """Test that __init__ sets the root."""
        source = MockSource(root=temp_dir)
        assert source.root == temp_dir

    def test_init_sets_debounce(self, temp_dir):
        """Test that __init__ sets the debounce."""
        source = MockSource(root=temp_dir, debounce=5.0)
        assert source.debounce == 5.0

    def test_init_default_debounce(self, temp_dir):
        """Test that __init__ uses default debounce of 2.0."""
        source = MockSource(root=temp_dir)
        assert source.debounce == 2.0


class TestWatchedSourceIdentity:
    def test_identity_returns_resolved_path(self, temp_dir):
        """Test that identity() returns the resolved path."""
        source = MockSource(root=temp_dir)
        identity = source.identity()
        assert isinstance(identity, str)
        assert len(identity) > 0

    def test_identity_is_string(self, temp_dir):
        """Test that identity() returns a string."""
        source = MockSource(root=temp_dir)
        identity = source.identity()
        assert isinstance(identity, str)


class TestWatchedSourceScan:
    def test_scan_yields_none_for_empty_discover(self, temp_dir):
        """Test that scan yields nothing when discover returns nothing."""
        source = MockSource(root=temp_dir)
        items = list(source.scan())
        assert items == []

    def test_scan_calls_discover_and_parse(self, temp_dir):
        """Test that scan calls discover and parse."""
        test_file = temp_dir / "test.md"
        test_file.write_text("# Test\n\nContent.")

        class TestSource(WatchedSource):
            name = "test"
            dataset_kind = "text"

            def discover(self):
                return iter([test_file])

            def parse(self, path):
                return RawDocument(
                    source_uri=f"test://{path.name}",
                    content_hash="abc",
                    text=path.read_text(),
                    title=None,
                    modified_at=path.stat().st_mtime,
                    metadata={},
                    labels=[],
                )

        source = TestSource(root=temp_dir)
        items = list(source.scan())
        assert len(items) == 1
        assert items[0].source_uri == f"test://{test_file.name}"


class TestWatchedSourceWatch:
    def test_watch_is_noop_by_default(self, temp_dir):
        """Test that the base watch() method is a no-op."""
        source = MockSource(root=temp_dir)
        # Should not raise
        source.watch(lambda event: None)

    def test_watch_accepts_callback(self, temp_dir):
        """Test that watch() accepts a callback without error."""
        source = MockSource(root=temp_dir)
        events = []

        def callback(event):
            events.append(event)

        source.watch(callback)
        assert events == []  # No events should be fired


class TestWatchedSourceFileContentHash:
    def test_file_content_hash_returns_string(self, temp_dir):
        """Test that file_content_hash returns a string."""
        test_file = temp_dir / "hash-test.txt"
        test_file.write_text("hash test content")

        source = MockSource(root=temp_dir)
        result = source.file_content_hash(test_file)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_file_content_hash_deterministic(self, temp_dir):
        """Test that file_content_hash is deterministic."""
        test_file = temp_dir / "hash-test2.txt"
        test_file.write_text("deterministic content")

        source = MockSource(root=temp_dir)
        h1 = source.file_content_hash(test_file)
        h2 = source.file_content_hash(test_file)
        assert h1 == h2

    def test_file_content_hash_different_for_different_content(self, temp_dir):
        """Test that different content produces different hashes."""
        file1 = temp_dir / "hash-a.txt"
        file1.write_text("content a")
        file2 = temp_dir / "hash-b.txt"
        file2.write_text("content b")

        source = MockSource(root=temp_dir)
        h1 = source.file_content_hash(file1)
        h2 = source.file_content_hash(file2)
        assert h1 != h2

    def test_file_content_hash_matches_identity(self, temp_dir):
        """Test that file_content_hash matches identity module."""
        from corpus_forge.identity import file_content_hash

        test_file = temp_dir / "hash-compare.txt"
        test_file.write_text("compare content")

        source = MockSource(root=temp_dir)
        h1 = source.file_content_hash(test_file)
        h2 = file_content_hash(test_file)
        assert h1 == h2


class TestWatchedSourceAbstractMethods:
    def test_discover_is_abstract(self):
        """Test that WatchedSource cannot be instantiated without discover."""
        with pytest.raises(TypeError):
            WatchedSource(root=Path("/tmp"))

    def test_parse_is_abstract(self):
        """Test that WatchedSource cannot be instantiated without parse."""
        with pytest.raises(TypeError):
            WatchedSource(root=Path("/tmp"))
