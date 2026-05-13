"""Shared test fixtures and configuration."""

import importlib
import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import settings as hypothesis_settings

from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage
from tests.fuzz.profiles import register_hypothesis_profiles

# ── Hypothesis profile resolution (CI-1) ─────────────────────────────────────
# Register dev/ci/nightly profiles once at conftest import. The active
# profile is selected via the ``HYPOTHESIS_PROFILE`` env var; ``dev`` is the
# default for local runs. CI sets ``HYPOTHESIS_PROFILE=ci`` explicitly in
# the workflow rather than relying on the ``CI`` boolean — see
# ``tests/fuzz/profiles.py`` for the rationale.
register_hypothesis_profiles()
_ACTIVE_HYPOTHESIS_PROFILE = os.environ.get("HYPOTHESIS_PROFILE", "dev")
hypothesis_settings.load_profile(_ACTIVE_HYPOTHESIS_PROFILE)


def _docker_available() -> bool:
    """Check if Docker (or Docker-compatible runtime) is available."""
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
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
    from testcontainers.postgres import PostgresContainer


@pytest.fixture
def temp_dir() -> Path:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def pytest_configure(config: pytest.Config) -> None:
    """Register Docker availability marker."""
    config.addinivalue_line(
        "markers",
        "requires_docker: mark test as requiring Docker (will be skipped if unavailable)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests when Docker is not available."""
    if not _docker_available() or not _testcontainers_available():
        skip = pytest.mark.skip(reason="Docker or testcontainers not available")
        for item in items:
            if "integration" in item.keywords or item.module.__name__.startswith(
                "tests.integration"
            ):
                item.add_marker(skip)


@pytest.fixture
def sample_vault_dir(temp_dir: Path) -> Path:
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
def sample_claude_code_dir(temp_dir: Path) -> Path:
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
def sample_opencode_dir(temp_dir: Path) -> Path:
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


@pytest.fixture(scope="session")
def postgres_container():  # type: ignore[return]
    """Session-scoped PostgreSQL+pgvector container shared across all integration tests."""
    with PostgresContainer("pgvector/pgvector:pg17", port=5432) as container:
        yield container


@pytest.fixture
def pg_dsn(postgres_container) -> str:  # type: ignore[return]
    """Function-scoped libpq DSN string built from the shared postgres_container.

    Returns a bare postgresql:// DSN that psycopg.connect() accepts directly,
    rather than the SQLAlchemy-style postgresql+psycopg2:// returned by
    postgres_container.get_connection_url().
    """
    c = postgres_container
    return (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )


@pytest.fixture
def pg(postgres_container):  # type: ignore[return]
    """Function-scoped alias for postgres_container.

    Provides backward-compatible access to the shared container for legacy test
    methods that reference ``pg`` directly (e.g. ``pg.get_connection()`` calls
    that are pre-existing and will be triaged in INT-02).
    """
    return postgres_container


@pytest.fixture
def sample_document() -> RawDocument:
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
def sample_conversation() -> RawConversation:
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


@pytest.fixture(params=["postgres", "sqlite"])
def backend_kind(request: pytest.FixtureRequest) -> str:
    """Parametrize fixture yielding each backend kind as a string.

    - ``"postgres"`` — skipped at runtime when Docker / testcontainers are
      unavailable (``pytest.skip`` inside ``storage_backend``).
    - ``"sqlite"`` — always available; never requires Docker.

    Consume this via the ``storage_backend`` fixture, which wires the correct
    backend implementation.  Do **not** use ``backend_kind`` on its own in
    tests — use ``storage_backend`` which handles Docker availability.
    """
    return request.param  # type: ignore[return-value]


@pytest.fixture
def storage_backend(backend_kind: str, request: pytest.FixtureRequest, tmp_path: Path):  # type: ignore[return]
    """Yield a migrated StorageBackend instance for the requested backend kind.

    - ``postgres``: skipped if Docker or testcontainers are unavailable.
      Uses the session-scoped ``pg_dsn`` fixture (lazy via
      ``request.getfixturevalue``).  Each test gets a fresh schema created
      inside the shared container.
    - ``sqlite``: always available.  Uses a per-test ``tmp_path / "corpus.db"``
      file so tests are fully isolated.

    This fixture is the entry point for B-16 dual-backend parametrized tests.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend

    if backend_kind == "postgres":
        if not _docker_available() or not _testcontainers_available():
            pytest.skip("Docker / testcontainers not available — skipping postgres backend")
        from corpus_forge.backends.postgres import PostgresBackend

        pg_dsn_value: str = request.getfixturevalue("pg_dsn")
        backend = PostgresBackend(dsn=pg_dsn_value, schema="corpus")
        backend.migrate()
        yield backend
    else:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path))
        backend.migrate()
        yield backend


@pytest.fixture
def sample_config_content() -> str:
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
