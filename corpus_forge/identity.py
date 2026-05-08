"""Identity utilities for corpus-forge."""

import hashlib
from pathlib import Path


def chunk_content_hash(text: str) -> str:
    """Compute SHA256 hex digest of text encoded as UTF-8."""
    return content_hash(text.encode("utf-8"))


def content_hash(data: bytes) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(data).hexdigest()


def file_content_hash(filepath: Path) -> str:
    """Compute SHA256 hash of file content."""
    return content_hash(filepath.read_bytes())


def source_uri(dataset_name: str, plugin: str, identity: str) -> str:
    """Generate canonical source URI."""
    return f"{dataset_name}://{plugin}/{identity}"


def advisory_lock_key(source_uri: str) -> int:
    """Generate advisory lock key from source URI."""
    # Simple hash to integer for advisory lock
    return int(hashlib.md5(source_uri.encode()).hexdigest()[:16], 16) % 2**31
