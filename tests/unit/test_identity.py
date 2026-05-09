"""Unit tests for identity utilities."""

import hashlib

from corpus_forge.identity import (
    advisory_lock_key,
    chunk_content_hash,
    content_hash,
    file_content_hash,
    source_uri,
)


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


# ── chunk_content_hash ──────────────────────────────────────────────


def test_chunk_content_hash_basic():
    """Happy path: basic ASCII text produces correct sha256 hex."""
    text = "hello world"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert chunk_content_hash(text) == expected


def test_chunk_content_hash_empty_string():
    """Boundary: empty string produces sha256 of empty bytes."""
    result = chunk_content_hash("")
    expected = hashlib.sha256(b"").hexdigest()
    assert result == expected


def test_chunk_content_hash_single_char():
    """Boundary: single character input."""
    result = chunk_content_hash("x")
    expected = hashlib.sha256(b"x").hexdigest()
    assert result == expected


def test_chunk_content_hash_multiline():
    """Boundary: multi-line text with newlines."""
    text = "line one\nline two\nline three"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert chunk_content_hash(text) == expected


def test_chunk_content_hash_unicode():
    """Type/format: non-ASCII / Unicode characters encoded as UTF-8."""
    text = "café résumé naïve 日本語 🌍"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert chunk_content_hash(text) == expected


def test_chunk_content_hash_emoji():
    """Type/format: emoji (multi-byte UTF-8 sequences)."""
    text = "😀😁😂🤣"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert chunk_content_hash(text) == expected


def test_chunk_content_hash_long_text():
    """Boundary: long text (simulating a document chunk)."""
    text = "The quick brown fox jumps over the lazy dog. " * 1000
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert chunk_content_hash(text) == expected


def test_chunk_content_hash_deterministic():
    """State: same input always produces the same output (pure function)."""
    text = "determinism test"
    results = [chunk_content_hash(text) for _ in range(10)]
    assert len(set(results)) == 1


def test_chunk_content_hash_equivalence_to_content_hash():
    """Equivalence: chunk_content_hash(text) == content_hash(text.encode('utf-8'))."""
    texts = [
        "",
        "a",
        "hello world",
        "café",
        "日本語",
        "line1\nline2",
        "special chars: !@#$%^&*()",
    ]
    for text in texts:
        assert chunk_content_hash(text) == content_hash(text.encode("utf-8"))


def test_chunk_content_hash_different_inputs_different_hashes():
    """Regression: distinct inputs produce distinct hashes (collision resistance sanity)."""
    results = {chunk_content_hash(t) for t in ["a", "b", "c", "d", "e"]}
    assert len(results) == 5


def test_chunk_content_hash_whitespace_preserved():
    """Boundary: leading/trailing/internal whitespace is preserved in hash."""
    # Leading space
    assert chunk_content_hash(" hello") != chunk_content_hash("hello")
    # Trailing space
    assert chunk_content_hash("hello ") != chunk_content_hash("hello")
    # Internal spaces
    assert chunk_content_hash("hello  world") != chunk_content_hash("hello world")
    # Tabs
    assert chunk_content_hash("hello\tworld") != chunk_content_hash("hello world")
    # Newlines
    assert chunk_content_hash("hello\nworld") != chunk_content_hash("hello world")


def test_chunk_content_hash_returns_str():
    """Type: return value is a string."""
    result = chunk_content_hash("test")
    assert isinstance(result, str)


def test_chunk_content_hash_output_length():
    """Type: output is 64 hex characters (SHA256 hex digest length)."""
    result = chunk_content_hash("any text")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
