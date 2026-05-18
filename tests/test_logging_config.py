"""Tests for ``corpus_forge.logging_config``.

The init function installs three handlers on the root ``corpus_forge``
logger:

  1. rotating file at ``platformdirs.user_cache_dir / logs / <component>.log``,
  2. stderr ``RichHandler`` (skipped for stdio-MCP),
  3. in-memory ring buffer (``MemoryHandler`` capacity 200).

Tests pin the file path, level routing (default INFO, ``--verbose``
widens to DEBUG, ``--quiet`` narrows to WARNING), the env-var overrides
``CF_LOG_LEVEL`` and ``CF_LOG_DIR``, idempotency, and the helpers used
by Wave 6's bug-report.
"""

from __future__ import annotations

import contextlib
import logging
from logging.handlers import MemoryHandler, RotatingFileHandler
from pathlib import Path

import pytest


def _reset_root_logger() -> None:
    root = logging.getLogger("corpus_forge")
    for h in list(root.handlers):
        root.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    root.setLevel(logging.NOTSET)


@pytest.fixture(autouse=True)
def _clean_logger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test starts from a clean ``corpus_forge`` logger and a
    redirected log dir."""

    monkeypatch.setenv("CF_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("CF_LOG_LEVEL", raising=False)
    monkeypatch.delenv("CF_TRANSPORT", raising=False)
    _reset_root_logger()
    yield
    _reset_root_logger()


def test_init_logging_creates_rotating_file_at_expected_path(tmp_path: Path) -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    log_path = tmp_path / "logs" / "cli.log"
    logging.getLogger("corpus_forge.test").info("hello world")

    # Force any handler buffer to write.
    for h in logging.getLogger("corpus_forge").handlers:
        with contextlib.suppress(Exception):
            h.flush()

    assert log_path.exists(), f"expected log file at {log_path}"
    assert "hello world" in log_path.read_text()


def test_init_logging_installs_rotating_file_handler() -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    handlers = logging.getLogger("corpus_forge").handlers
    file_handlers = [h for h in handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    fh = file_handlers[0]
    assert fh.maxBytes == 10 * 1024 * 1024
    assert fh.backupCount == 5


def test_init_logging_installs_memory_ring_buffer() -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    mem = [h for h in logging.getLogger("corpus_forge").handlers if isinstance(h, MemoryHandler)]
    assert len(mem) == 1
    assert mem[0].capacity == 200


def test_default_level_is_info_for_stderr_handler(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    log = logging.getLogger("corpus_forge.example")
    log.info("user-facing info")
    log.debug("noisy debug")

    captured = capsys.readouterr()
    # RichHandler writes to stderr by default.
    assert "user-facing info" in captured.err
    assert "noisy debug" not in captured.err


def test_verbose_widens_stderr_to_debug(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli", verbose=True)
    log = logging.getLogger("corpus_forge.example")
    log.debug("now visible")

    captured = capsys.readouterr()
    assert "now visible" in captured.err


def test_quiet_narrows_stderr_to_warning(capsys: pytest.CaptureFixture[str]) -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli", quiet=True)
    log = logging.getLogger("corpus_forge.example")
    log.info("info should be hidden")
    log.warning("warning should appear")

    captured = capsys.readouterr()
    assert "info should be hidden" not in captured.err
    assert "warning should appear" in captured.err


def test_cf_log_level_env_overrides_flags(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CF_LOG_LEVEL", "DEBUG")
    from corpus_forge.logging_config import init_logging

    # quiet=True normally narrows to WARNING; env var must override.
    init_logging("cli", quiet=True)
    log = logging.getLogger("corpus_forge.example")
    log.debug("via env override")
    captured = capsys.readouterr()
    assert "via env override" in captured.err


def test_mcp_stdio_mode_skips_rich_handler(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``mcp`` component + ``CF_TRANSPORT=stdio`` must not write to
    stderr (the MCP host captures it)."""

    monkeypatch.setenv("CF_TRANSPORT", "stdio")
    from corpus_forge.logging_config import init_logging

    init_logging("mcp")
    log = logging.getLogger("corpus_forge.example")
    log.error("must not appear on stderr")

    captured = capsys.readouterr()
    assert "must not appear on stderr" not in captured.err
    assert "must not appear on stderr" not in captured.out


def test_init_logging_is_idempotent() -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    init_logging("cli")  # second call replaces handlers, not appends.

    root = logging.getLogger("corpus_forge")
    # Exactly one rotating file + one memory + at most one rich handler.
    file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    mem_handlers = [h for h in root.handlers if isinstance(h, MemoryHandler)]
    assert len(file_handlers) == 1
    assert len(mem_handlers) == 1


def test_get_log_dir_returns_path(tmp_path: Path) -> None:
    from corpus_forge.logging_config import get_log_dir, init_logging

    init_logging("cli")
    log_dir = get_log_dir()
    assert isinstance(log_dir, Path)
    assert log_dir == tmp_path / "logs"


def test_get_ring_buffer_returns_memory_handler() -> None:
    from corpus_forge.logging_config import get_ring_buffer, init_logging

    init_logging("cli")
    rb = get_ring_buffer()
    assert isinstance(rb, MemoryHandler)
    assert rb.capacity == 200


def test_init_logging_writes_component_specific_filename(tmp_path: Path) -> None:
    from corpus_forge.logging_config import init_logging

    init_logging("daemon")
    logging.getLogger("corpus_forge.x").info("daemon line")

    for h in logging.getLogger("corpus_forge").handlers:
        with contextlib.suppress(Exception):
            h.flush()

    assert (tmp_path / "logs" / "daemon.log").exists()


def test_ring_buffer_captures_recent_events() -> None:
    """The memory handler must record INFO+ records the bug-report can
    flush later."""

    from corpus_forge.logging_config import get_ring_buffer, init_logging

    init_logging("cli")
    log = logging.getLogger("corpus_forge.example")
    log.info("event one")
    log.warning("event two")
    rb = get_ring_buffer()
    messages = [r.getMessage() for r in rb.buffer]
    assert "event one" in messages
    assert "event two" in messages
