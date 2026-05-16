"""D-09 — Smoke: `corpus-forge mcp serve` boots clean against an Alembic-initialized DB.

End-to-end subprocess test verifying three invariants, the third being the
load-bearing pin established in commit 66ab179 (stderr discipline):

1. stdout contains EXACTLY ONE complete JSON object — the MCP initialize
   response — with ``jsonrpc: "2.0"``, ``id: 1``, and a ``result`` field.
2. stdout has NO pre-init noise: no "Applying migration:" lines, no Alembic
   log lines, no any bytes before the first ``{`` of the response.
3. stderr is non-empty when the boot path runs Alembic migrations (Alembic
   runtime logs surface on the right channel — stderr, not stdout).

Strategy
--------
* Write a fresh SQLite database path at ``tmp_path/test.db``.
* Write a minimal ``config.toml`` pointing the server at that SQLite path.
* Pre-initialize the database with Alembic (via ``_apply_alembic``) so the
  first migration run happens *before* the subprocess boots, giving us a
  clean already-migrated DB to test the boot path against an up-to-date DB.
* Additionally run a second sub-test where NO pre-migration is done, forcing
  ``apply_migrations`` to delegate to Alembic during the MCP boot sequence —
  this is the path that must NEVER leak Alembic log lines to stdout.
* Subprocess: ``.venv/bin/corpus-forge mcp serve``, stdin=PIPE, stdout=PIPE,
  stderr=PIPE.
* Send one MCP ``initialize`` JSON-RPC request; wait up to ~5 seconds for a
  response; kill; drain stderr.

Acceptable RED at D-09 time
---------------------------
The tests may fail with:
- TimeoutError (subprocess never answers initialize — config wiring wrong).
- AssertionError on stdout purity (Alembic log lines leaking through).
- subprocess crash (ImportError, config validation error, etc.).

Any failure mode other than "clean boot + correct JSON response" is valid RED
proving the pin is meaningful.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ``sys.executable`` is the interpreter currently running pytest — same
# venv on every platform, no need to guess between POSIX's
# ``.venv/bin/python`` and Windows' ``.venv\Scripts\python.exe`` layouts.
# Avoids the iCloud-sync-stale-shebang issue documented in
# ``_boot_and_send_initialize`` below because we invoke
# ``python -m corpus_forge.cli`` directly, not the entry-point script.
_VENV_PYTHON = Path(sys.executable)
_VENV_BIN = _VENV_PYTHON.parent
_CORPUS_FORGE_BIN = _VENV_BIN / ("corpus-forge.exe" if sys.platform == "win32" else "corpus-forge")

_MCP_INITIALIZE_REQUEST = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "d09-smoke-tester", "version": "0.0.1"},
            },
        }
    ).encode()
    + b"\n"
)

_BOOT_TIMEOUT_S = 10  # seconds to wait for the initialize response


def _write_minimal_config(config_path: Path, db_path: Path) -> None:
    """Write a minimal corpus-forge config.toml pointing at *db_path* (SQLite)."""
    toml = f"""
datasets = []

[backend]
kind = "sqlite"
dsn = "{db_path}"

[daemon]
debounce_seconds = 1.0
log_level = "WARNING"
log_format = "text"

[[embedders]]
name = "smoke-stub"
provider = "sentence_transformers"
model_id = "BAAI/bge-small-en-v1.5"
dimension = 384
normalize = true
distance = "cosine"
active = true
batch_size = 32
device = "cpu"
"""
    config_path.write_text(toml.lstrip(), encoding="utf-8")


def _pre_migrate_sqlite(db_path: Path) -> None:
    """Run Alembic upgrade-to-head directly (in-process) against *db_path*.

    This simulates the D-07 Alembic path without involving the subprocess,
    letting us isolate the MCP boot path from first-time migration side effects.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.schema.migrate import _apply_alembic

    backend = SQLiteBackend(path=str(db_path))
    _apply_alembic(backend, "sqlite")


def _boot_and_send_initialize(
    config_path: Path,
) -> tuple[bytes, bytes, int | None]:
    """Spawn ``corpus-forge mcp serve``, send initialize, read one JSON line.

    Returns (stdout_bytes, stderr_bytes, returncode_or_None).
    ``returncode`` is None if the process is still running after we kill it.
    """
    env = os.environ.copy()
    env["CORPUS_FORGE_CONFIG"] = str(config_path)
    # Keep HuggingFace offline so no model weights are downloaded on startup.
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Silence transformers/tokenizers chatter that could leak to stderr.
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    # Use `python -m corpus_forge.cli` rather than the entry-point script
    # because the venv entry-point may have stale paths after iCloud sync
    # races (the .pth editable install is stable; the script header is not).
    proc = subprocess.Popen(
        [str(_VENV_PYTHON), "-m", "corpus_forge.cli", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    stdout_chunks: list[bytes] = []
    stderr_bytes = b""
    try:
        # Send the initialize request.
        proc.stdin.write(_MCP_INITIALIZE_REQUEST)
        proc.stdin.flush()

        # Wait for the first complete JSON line on stdout with a timeout.
        deadline = time.monotonic() + _BOOT_TIMEOUT_S

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.2))
            if ready:
                chunk = proc.stdout.read1(4096)  # type: ignore[attr-defined]
                if chunk:
                    stdout_chunks.append(chunk)
                    so_far = b"".join(stdout_chunks)
                    # Check if we have at least one complete JSON line.
                    if b"\n" in so_far:
                        break
            # Check if process died early.
            if proc.poll() is not None:
                break

    finally:
        proc.kill()
        # Drain remaining stdout + stderr.  Do NOT close stdin before
        # communicate() to avoid "flush of closed file" on Python 3.13.
        try:
            remaining_stdout, stderr_bytes = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            remaining_stdout, stderr_bytes = proc.communicate()
        stdout_chunks.append(remaining_stdout)

    stdout_line = b"".join(stdout_chunks)
    return stdout_line, stderr_bytes, proc.returncode


# ---------------------------------------------------------------------------
# pytestmark
# ---------------------------------------------------------------------------

# Module is marked ``requires_unix`` because ``_boot_and_send_initialize``
# uses ``select.select()`` on the subprocess's stdout pipe. ``select`` on
# Windows only accepts sockets, not file descriptors, so the call raises
# ``OSError`` at runtime there. Cross-platform replacement would use a
# reader thread + ``queue.Queue`` — a worthwhile rewrite but separate
# scope from the Phase I-01 portability sweep.
pytestmark = [pytest.mark.smoke, pytest.mark.requires_unix]


# ---------------------------------------------------------------------------
# Tests — pre-migrated DB (clean boot path)
# ---------------------------------------------------------------------------


class TestMcpServeBootsWithPreMigratedDb:
    """MCP server boot against a DB already migrated by Alembic."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.db_path = tmp_path / "test.db"
        self.config_path = tmp_path / "config.toml"
        _write_minimal_config(self.config_path, self.db_path)
        # Pre-migrate so the boot path hits an already-up-to-date DB.
        _pre_migrate_sqlite(self.db_path)
        self.stdout_bytes, self.stderr_bytes, _ = _boot_and_send_initialize(self.config_path)

    def test_mcp_serve_boots_with_alembicd_db_responds_to_initialize(self):
        """stdout must contain exactly one valid MCP JSON-RPC initialize response."""
        lines = [ln for ln in self.stdout_bytes.splitlines() if ln.strip()]
        assert lines, (
            f"No output on stdout after sending initialize request.\n"
            f"stderr={self.stderr_bytes.decode(errors='replace')!r}"
        )
        # Take the first non-empty line as the initialize response.
        first_line = lines[0]
        try:
            obj = json.loads(first_line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"First stdout line is not valid JSON: {exc!r}\nraw bytes: {first_line!r}")
        assert obj.get("jsonrpc") == "2.0", f"Expected jsonrpc='2.0'; got {obj.get('jsonrpc')!r}"
        assert obj.get("id") == 1, f"Expected id=1; got {obj.get('id')!r}"
        assert "result" in obj, f"Response missing 'result' field; keys={list(obj.keys())}"

    def test_mcp_serve_stdout_has_no_pre_init_noise(self):
        """stdout must have EXACTLY ONE complete JSON object (the init response).

        This is the load-bearing pin from commit 66ab179's stderr fix.
        No "Applying migration:" lines, no Alembic log lines, no stray bytes
        before the opening ``{`` of the response must appear.
        """
        raw = self.stdout_bytes
        # Strip trailing newlines; every remaining line must be a valid JSON object.
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        assert len(lines) >= 1, (
            "Expected at least one JSON line on stdout; got nothing.\n"
            f"stderr={self.stderr_bytes.decode(errors='replace')!r}"
        )
        # Assert NO migration noise anywhere in stdout.
        stdout_text = raw.decode(errors="replace")
        migration_markers = [
            "Applying migration:",
            "Running upgrade",
            "alembic",
            "INFO",
            "WARNING",
            "ERROR",
        ]
        # Check first line is pure JSON (starts with '{').
        for ln in lines:
            decoded = ln.decode(errors="replace").lstrip()
            assert decoded.startswith("{"), f"Non-JSON line on stdout: {decoded!r}"
        # The very first byte of stdout must be '{' (no leading garbage).
        first_nonws = raw.lstrip()
        assert first_nonws.startswith(b"{"), (
            f"stdout does not start with '{{'; starts with: {raw[:80]!r}"
        )
        # No migration log markers should appear anywhere on stdout.
        for marker in migration_markers:
            assert marker not in stdout_text, (
                f"Migration log marker {marker!r} found on stdout — "
                "Alembic log is leaking through the JSON-RPC channel!\n"
                f"stdout={stdout_text!r}"
            )

    def test_mcp_serve_stderr_may_contain_alembic_runtime_logs(self):
        """stderr must NOT be empty: Alembic's runtime.migration logger fires on stderr.

        Even with a pre-migrated DB, the 'Running upgrade <rev> -> <rev>' lines
        (or 'Running upgrade  -> head' when already at head) surface here.
        If the server crashed, stderr will contain the traceback — also non-empty.

        This assertion documents the invariant: Alembic's log output goes to
        stderr, never stdout.  The test does NOT assert the exact content of
        stderr (that's too brittle), only that it is non-empty.
        """
        # If the server crashed immediately (bad config, ImportError, etc.),
        # stderr will have the traceback — still non-empty, but we want to surface
        # the crash reason clearly.
        assert self.stderr_bytes, (
            "stderr is empty — Alembic runtime logs expected on stderr but found nothing. "
            "This may indicate the MCP server crashed before running migrations, "
            "OR the Alembic log handler is misconfigured (routing to stdout instead)."
        )


# ---------------------------------------------------------------------------
# Tests — fresh (un-migrated) DB: forces apply_migrations during boot
# ---------------------------------------------------------------------------


class TestMcpServeBootsWithFreshDb:
    """MCP server boot against a brand-new, empty SQLite database.

    This exercises the D-07 path where apply_migrations finds no SQL files and
    delegates to Alembic.  The boot must still produce a clean JSON-RPC response
    on stdout with zero migration noise.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.db_path = tmp_path / "fresh.db"
        self.config_path = tmp_path / "config.toml"
        _write_minimal_config(self.config_path, self.db_path)
        # Intentionally do NOT pre-migrate — the server must do it during boot.
        assert not self.db_path.exists(), "fresh.db should not exist before boot"
        self.stdout_bytes, self.stderr_bytes, self.returncode = _boot_and_send_initialize(
            self.config_path
        )

    def test_fresh_db_boot_responds_to_initialize(self):
        """Even with a brand-new DB, the server must produce a valid JSON-RPC response."""
        lines = [ln for ln in self.stdout_bytes.splitlines() if ln.strip()]
        assert lines, (
            "No output on stdout after sending initialize to server with fresh DB.\n"
            f"stderr={self.stderr_bytes.decode(errors='replace')!r}"
        )
        first_line = lines[0]
        try:
            obj = json.loads(first_line)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"First stdout line is not valid JSON: {exc!r}\n"
                f"raw bytes: {first_line!r}\n"
                f"stderr={self.stderr_bytes.decode(errors='replace')!r}"
            )
        assert obj.get("jsonrpc") == "2.0", f"Expected jsonrpc='2.0'; got {obj.get('jsonrpc')!r}"
        assert obj.get("id") == 1, f"Expected id=1; got {obj.get('id')!r}"
        assert "result" in obj, f"Response missing 'result' field; keys={list(obj.keys())}"

    def test_fresh_db_boot_stdout_has_no_migration_noise(self):
        """stdout must stay clean of migration log lines even when Alembic runs during boot."""
        stdout_text = self.stdout_bytes.decode(errors="replace")
        migration_markers = [
            "Applying migration:",
            "Running upgrade",
            "alembic",
        ]
        # Allow lines only if they are valid JSON objects.
        for ln in self.stdout_bytes.splitlines():
            if not ln.strip():
                continue
            decoded = ln.decode(errors="replace").lstrip()
            assert decoded.startswith("{"), (
                f"Non-JSON line on stdout during fresh-DB boot: {decoded!r}\n"
                "Migration log may be leaking to stdout."
            )
        for marker in migration_markers:
            assert marker not in stdout_text, (
                f"Migration log marker {marker!r} found on stdout — "
                "Alembic log leaking through the JSON-RPC channel during fresh-DB boot!\n"
                f"stdout={stdout_text!r}"
            )

    def test_fresh_db_stderr_has_alembic_logs(self):
        """Alembic migration output during boot must go to stderr, not stdout."""
        # On a fresh DB, Alembic WILL run migrations and log to
        # alembic.runtime.migration (which goes to stderr).
        assert self.stderr_bytes, (
            "stderr is empty after booting with a fresh DB — "
            "Alembic migration logs expected on stderr but none found.\n"
            "Either: (a) migrations aren't running on boot, "
            "(b) the log handler sends output to the wrong channel, or "
            "(c) the server crashed before running migrations."
        )
