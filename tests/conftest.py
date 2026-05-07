"""Shared test fixtures and configuration."""

import importlib
import tempfile
from pathlib import Path

import pytest

from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage


def _docker_available() -> bool:
    """Check if Docker (or Docker-compatible runtime) is available."""
    import subprocess  # noqa: PLC0415

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _testcontainers_available() -> bool:
    """Check if testcontainers is importable."""
    try:
        importlib.import_module("testcontainers.postgres")
        return True
    except ImportError:
        return False


# Lazy import — only when Docker is available
if _docker_available() and _testcontainers_available():
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def pytest_configure(config):
    """Register Docker availability marker."""
    config.addinivalue_line(
        "markers",
        "requires_docker: mark test as requiring Docker (will be skipped if unavailable)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when Docker is not available."""
    if not _docker_available() or not _testcontainers_available():
        skip = pytest.mark.skip(reason="Docker or testcontainers not available")
        for item in items:
            if "integration" in item.keywords or item.module.__name__.startswith("tests.integration"):
                item.add_marker(skip)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_vault_dir(temp_dir):
    """Create a sample markdown vault directory."""
    vault_dir = temp_dir / "vault"
    vault_dir.mkdir()

    # Create sample markdown files
    (vault_dir / "note1.md").write_text("# Note 1\n\nThis is the first note.")
    (vault_dir / "note2.md").write_text(
        "# Note 2\n\nMore content here.\n\n## Subsection\n\nDetailed content."
    )
    (vault_dir / ".obsidian").mkdir()
    (vault_dir / ".obsidian" / "config.json").write_text("{}")

    return vault_dir


@pytest.fixture
def sample_claude_code_dir(temp_dir):
    """Create a sample Claude Code projects directory."""
    projects_dir = temp_dir / "projects"
    projects_dir.mkdir()

    # Create sample project directory
    project_dir = projects_dir / "test_project"
    project_dir.mkdir()

    # Create sample session file
    session_file = project_dir / "session1.jsonl"
    session_lines = [
        '{"uuid": "msg1", "message": {"role": "user", "content": "Hello"}, "timestamp": 1000}',
        '{"uuid": "msg2", "message": {"role": "assistant", "content": "Hi there!"},'
        ' "timestamp": 1001}',
    ]
    session_file.write_text("\n".join(session_lines))

    return projects_dir


@pytest.fixture
def sample_opencode_dir(temp_dir):
    """Create a sample OpenCode storage directory."""
    storage_dir = temp_dir / "storage"
    storage_dir.mkdir()

    # Create the expected directory structure
    (storage_dir / "session" / "sess1").mkdir(parents=True)
    (storage_dir / "message" / "msg1").mkdir(parents=True)
    (storage_dir / "part" / "part1").mkdir(parents=True)

    # Create sample message file
    message_file = storage_dir / "message" / "msg1" / "message.json"
    message_content = """{
  "id": "msg1",
  "parentId": null,
  "role": "assistant",
  "content": "This is a test message",
  "timestamp": 1000,
  "parts": [
    {"type": "text", "content": "Hello "},
    {"type": "text", "content": "world!"}
  ]
}"""
    message_file.write_text(message_content)

    return storage_dir


@pytest.fixture
def pgvector_container():
    """Create a PostgreSQL container with pgvector extension."""
    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        # Enable pgvector extension
        with postgres.get_connection() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
        yield postgres


@pytest.fixture
def sample_document():
    """Create a sample RawDocument for testing."""
    return RawDocument(
        source_uri="test://vault/test.md",
        content_hash="abc123",
        text="# Test\n\nThis is a test document.",
        title="Test",
        modified_at=1000.0,
        metadata={},
        labels=[],
    )


@pytest.fixture
def sample_conversation():
    """Create a sample RawConversation for testing."""
    return RawConversation(
        source_uri="test://claude-code/project/session1",
        external_id="session1",
        content_hash="def456",
        title="Test Conversation",
        started_at=1000.0,
        ended_at=1005.0,
        messages=[
            RawMessage(
                external_uuid="msg1",
                parent_uuid=None,
                role="user",
                content="Hello",
                tool_calls=None,
                tool_results=None,
                ts=1000.0,
                metadata={},
            ),
            RawMessage(
                external_uuid="msg2",
                parent_uuid="msg1",
                role="assistant",
                content="Hi there!",
                tool_calls=None,
                tool_results=None,
                ts=1001.0,
                metadata={},
            ),
        ],
        metadata={},
        labels=[],
    )


@pytest.fixture
def sample_config_content():
    """Sample TOML configuration content."""
    return """
[backend]
kind = "postgres"
dsn = "postgresql://memory@localhost/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test-vault"
kind = "text"
  [[datasets.sources]]
  plugin         = "markdown_vault"
  vault_root     = "~/test-vault"
  exclude_globs  = [".obsidian/**", ".trash/**", ".*"]
  chunker        = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

[[datasets]]
name = "test-claude"
kind = "chat"
  [[datasets.sources]]
  plugin            = "claude_code"
  projects_root     = "~/test-projects"
  include_subagents = true
  chunker           = "conversation"
  chunker_config    = { mode = "per_message", role_prefix = true }

[[embedders]]
name      = "test-embedder"
provider  = "sentence_transformers"
model_id  = "test-model"
dimension = 384
normalize = true
distance  = "cosine"
active    = true
batch_size = 32
device    = "cpu"
"""
