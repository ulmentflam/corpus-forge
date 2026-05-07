"""Unit tests for identity utilities."""

import hashlib

from corpus_forge.identity import advisory_lock_key, content_hash, file_content_hash, source_uri


def test_content_hash():
    """Test content hash function."""
    data = b"hello world"
    expected = hashlib.sha256(data).hexdigest()
    assert content_hash(data) == expected


def test_file_content_hash(tmp_path):
    """Test file content hash function."""
    file_path = tmp_path / "test.txt"
    content = b"hello world"
    file_path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert file_content_hash(file_path) == expected


def test_source_uri():
    """Test source URI generation."""
    uri = source_uri("test-vault", "markdown_vault", "/path/to/vault")
    assert uri == "test-vault://markdown_vault//path/to/vault"


def test_advisory_lock_key():
    """Test advisory lock key generation."""
    key1 = advisory_lock_key("test://source/uri")
    key2 = advisory_lock_key("test://source/uri")
    key3 = advisory_lock_key("different://source/uri")

    # Same input should produce same key
    assert key1 == key2
    # Different input should produce different key (with high probability)
    assert key1 != key3

    # Key should be within reasonable range for advisory lock
    assert 0 <= key1 < 2**31
