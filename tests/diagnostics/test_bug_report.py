"""Phase L Wave 6 — ``corpus-forge bug-report`` bundler (W6-03).

The bundler writes a zip a user (or triaging agent) can read top-to-
bottom to reproduce/diagnose without back-and-forth. Hard contracts:

- zip path matches ``corpus-forge-bugreport-<ISO date>-<8-char-hash>.zip``
  in the CWD by default.
- short hash is deterministic from manifest contents — re-running with
  identical manifest produces the same hash.
- every output passes through :func:`corpus_forge.diagnostics.redact.redact_string`
  so a grep over the final zip finds no raw DSN / API key shape.
- ``--no-zip`` writes the staging directory uncompressed.
- ``--no-logs`` / ``--no-db`` omit the corresponding sections.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``CF_LOG_DIR`` at a fresh tmp dir for the duration of the test."""

    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    # Drop a synthetic cli.log so the bundler has something to bundle.
    (log_dir / "cli.log").write_text(
        "2026-05-18 12:00:00 [INFO   ] corpus_forge.cli: starting up\n"
        "2026-05-18 12:00:01 [INFO   ] corpus_forge.cli: api_key=sk-abcdef1234567890ABCDEFGH\n"
    )
    return log_dir


@pytest.fixture
def cwd_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Change CWD to a tmp dir for the test so bug-report writes there."""

    monkeypatch.chdir(tmp_path)
    return tmp_path


# ─── collect() — main bundler ────────────────────────────────────────


class TestCollect:
    def test_produces_zip_in_cwd(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False)

        assert report.path.exists()
        assert report.path.parent == cwd_tmp
        assert report.path.suffix == ".zip"
        assert report.path.name.startswith("corpus-forge-bugreport-")
        # Filename ends with ``-<short-hash>.zip`` where short-hash is
        # exactly 8 hex characters.
        stem = report.path.stem  # corpus-forge-bugreport-YYYY-MM-DD-<hash>
        assert stem.split("-")[-1] == report.short_hash
        assert len(report.short_hash) == 8

    def test_zip_contents_match_manifest(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False)

        with zipfile.ZipFile(report.path) as zf:
            names = set(zf.namelist())
            # Required files.
            assert "README.txt" in names
            assert "manifest.json" in names
            assert "doctor.json" in names
            assert "config.redacted.toml" in names
            assert "env.txt" in names
            assert "deps.txt" in names
            assert "logs/recent_events.txt" in names
            # Phase L Wave 8 — service status snapshot is part of the bundle.
            assert "service_status.txt" in names
            # cli.log was synthesized so its sweep should be present.
            assert "logs/cli.log.txt" in names

    def test_manifest_keys(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False)

        with zipfile.ZipFile(report.path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        # Required fields per Phase L Wave 6 spec.
        expected_keys = {
            "corpus_forge_version",
            "os",
            "os_version",
            "python_version",
            "arch",
            "ts_utc",
            "hostname_hash",
            "tool_path",
            "redaction_log",
            "agent_mode_at_time_of_capture",
        }
        assert expected_keys.issubset(manifest.keys())
        # Hostname is hashed, not literal — first 16 hex chars.
        import socket as _socket

        assert manifest["hostname_hash"] != _socket.gethostname()
        assert len(manifest["hostname_hash"]) == 16

    def test_no_secrets_in_zip(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        """The cli.log has a fake API key — it must not survive the sweep."""
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False)

        with zipfile.ZipFile(report.path) as zf:
            for member in zf.namelist():
                contents = zf.read(member).decode("utf-8", errors="replace")
                # No raw API-key shape anywhere.
                assert "sk-abcdef1234567890" not in contents, member
                # No raw DSN shape either.
                assert "postgresql://" not in contents or "«redacted»" in contents, member

    def test_no_zip_flag_writes_directory(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False, zip_bundle=False)

        assert report.path.is_dir()
        assert not report.path.name.endswith(".zip")
        # Same files exist on disk.
        assert (report.path / "manifest.json").exists()
        assert (report.path / "README.txt").exists()

    def test_no_logs_flag(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_logs=False, include_db=False)

        with zipfile.ZipFile(report.path) as zf:
            names = zf.namelist()
        # No logs/ entries when --no-logs is set.
        assert not any(name.startswith("logs/") for name in names)

    def test_no_db_flag(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False)

        with zipfile.ZipFile(report.path) as zf:
            names = zf.namelist()
        # No db_summary.json.
        assert "db_summary.json" not in names

    def test_short_hash_deterministic(self, isolated_log_dir: Path, cwd_tmp: Path) -> None:
        """Identical manifest input → identical short hash.

        We freeze the clock and hostname so the manifest content is
        identical across calls.  The hash is short_hash(manifest) so
        equal input → equal hash.
        """

        from corpus_forge.diagnostics import bug_report as br

        # Freeze ``_now()`` and ``_hostname_hash``.
        frozen_ts = "2026-05-18T12:00:00.000+00:00"
        with (
            patch.object(br, "_now_iso", return_value=frozen_ts),
            patch.object(br, "_hostname_hash", return_value="0123456789abcdef"),
            patch.object(br, "_collect_env", return_value="CF_FAKE=x\n"),
            patch.object(br, "_collect_deps", return_value="pkg==0.0.0\n"),
        ):
            first = br.collect(include_db=False)
            second = br.collect(include_db=False)

        assert first.short_hash == second.short_hash

    def test_redacted_count_positive_when_secrets_present(
        self, isolated_log_dir: Path, cwd_tmp: Path
    ) -> None:
        from corpus_forge.diagnostics.bug_report import collect

        report = collect(include_db=False)

        # cli.log has a fake API key → at least one redaction must
        # have happened.
        assert report.redacted_count >= 1


# ─── CLI registration ────────────────────────────────────────────────


class TestCLI:
    def test_bug_report_command_registered(self) -> None:
        """The Typer ``bug-report`` command must be reachable from ``corpus-forge``."""

        from corpus_forge.cli import app

        names = {cmd.name for cmd in app.registered_commands}
        # Either ``bug-report`` (the canonical) or ``bug_report`` (typer
        # auto-fallback) acceptable; we pin the hyphen form.
        assert "bug-report" in names


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
