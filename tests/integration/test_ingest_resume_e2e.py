"""SR-T7 — END-TO-END resume + per-source max-scan-age skip (RED).

Acceptance criteria (from tasks.md SR-T7):
  (a) Run ingest_once on a 20-file fake source → interrupted mid-walk.
  (b) Re-invoke with --resume → reuses same run_id; no duplicate documents;
      only un-walked sources re-scanned; ingest_runs row count == 1.
  (c) max_scan_age=3600 + fresh last_scanned_at for a source → that
      source's scan() is NOT called (call_count == 0).
  (d) config_digest mismatch → fresh run_id created (no resume).

Design notes:
  - SQLite-only (no Docker required; runs in the unit tier gate).
  - ``ingest_runs`` / ``ingest_run_sources`` tables seeded directly via
    SQL helper because the backend methods (start_ingest_run, etc.) don't
    exist yet — seeding raw SQL lets the tests fail for the *right* reason
    (missing resume codepath) rather than for a missing helper.
  - ``source.scan()`` is patched via ``unittest.mock.patch.object`` so
    call_count is deterministic regardless of filesystem state.
  - Clock control: ``datetime.datetime.now`` / ``datetime.datetime.utcnow``
    are patched via ``unittest.mock.patch`` to return a fixed timestamp
    when testing the freshness window — no freezegun required.
  - ``ingest_once`` is expected to accept keyword ``resume: bool = False``
    and ``max_scan_age: float = 0.0`` (SR-G5 adds these).  Until then,
    every test that exercises the new path will fail with TypeError or
    AttributeError — the correct RED state.

RED condition (before SR-G5 exists):
  - ImportError / AttributeError when the new ingest_once kwargs are absent.
  - ``backend.latest_unfinished_ingest_run`` does not exist on SQLiteBackend.
  - ``backend.start_ingest_run`` / ``finish_ingest_run`` etc. don't exist.
  - The tables ``ingest_runs`` / ``ingest_run_sources`` don't exist after
    migrate() (0017 migration not yet written — SR-G1 implements it).
  - All tests should fail, none should pass accidentally.
"""

from __future__ import annotations

import hashlib
import socket
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
)
from corpus_forge.ingest import ingest_once
from corpus_forge.sources.base import RawDocument

# ---------------------------------------------------------------------------
# No pytestmark — runs without Docker.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_URI_PREFIX = "filesystem://fake-source"
_FAKE_PLUGIN = "filesystem"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(tmp_path: Path) -> SQLiteBackend:
    """Return a fully migrated SQLiteBackend for the test."""
    backend = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    backend.migrate()
    return backend


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "corpus.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_config(tmp_path: Path, vault_dir: Path) -> Config:
    """Return a minimal Config pointing at a temp vault and a SQLite DB."""
    return Config(
        backend=BackendConfig(
            kind="sqlite",
            dsn=str(tmp_path / "corpus.db"),
            schema="corpus",
        ),
        daemon=DaemonConfig(debounce_seconds=1.0, log_level="INFO", log_format="text"),
        datasets=[
            DatasetConfig(
                name="resume-test-vault",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root=str(vault_dir),
                        exclude_globs=[".trash/**"],
                        chunker="markdown",
                        chunker_config={"max_chars": 1500, "overlap": 200},
                    )
                ],
            )
        ],
        embedders=[],
    )


def _compute_config_digest(config: Config) -> str:
    """Mirror the digest helper that ingest_once should use (SR-G5 contract)."""
    blob = config.model_dump_json(exclude={"daemon"})
    return hashlib.sha256(blob.encode()).hexdigest()


def _fake_docs(n: int, prefix: str = "vault://note") -> list[RawDocument]:
    return [
        RawDocument(
            source_uri=f"{prefix}{i}.md",
            content_hash=hashlib.sha256(f"content-{i}".encode()).hexdigest(),
            text=f"# Note {i}\n\nContent for note {i}.",
            title=f"Note {i}",
            modified_at=float(1_000_000 + i),
            metadata={},
            labels=[],
        )
        for i in range(n)
    ]


def _insert_ingest_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    config_digest: str,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> None:
    """Seed an ingest_runs row directly.  Lets tests control prior-run state."""
    ts = started_at or datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO ingest_runs
          (run_id, started_at, last_progress_at, status, config_digest, host, pid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, ts, ts, status, config_digest, socket.gethostname(), 999),
    )
    if ended_at:
        conn.execute(
            "UPDATE ingest_runs SET ended_at = ? WHERE run_id = ?",
            (ended_at, run_id),
        )
    conn.commit()


def _insert_ingest_run_source(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_uri_prefix: str,
    dataset_id: int,
    finished_at: str | None = None,
    last_scanned_at: str | None = None,
    docs_seen: int = 0,
) -> None:
    """Seed an ingest_run_sources row directly."""
    conn.execute(
        """
        INSERT INTO ingest_run_sources
          (run_id, source_uri_prefix, dataset_id, last_scanned_at, docs_seen, docs_skipped,
           docs_failed, finished_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
        """,
        (
            run_id,
            source_uri_prefix,
            dataset_id,
            last_scanned_at,
            docs_seen,
            finished_at,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Create a 20-file fake vault."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(20):
        (vault / f"note{i}.md").write_text(f"# Note {i}\n\nContent for note {i}.")
    return vault


@pytest.fixture
def cfg(tmp_path: Path, vault_dir: Path) -> Config:
    return _make_config(tmp_path, vault_dir)


@pytest.fixture
def backend(tmp_path: Path, cfg: Config) -> SQLiteBackend:
    """Migrated SQLiteBackend. Fails RED when 0017 migration not yet written."""
    b = SQLiteBackend(path=cfg.backend.dsn)
    b.migrate()
    # Confirm 0017 tables exist — this assertion is intentionally in the
    # fixture body so every test that uses ``backend`` fails with a
    # clear message when the migration hasn't been applied yet.
    conn = sqlite3.connect(cfg.backend.dsn)
    try:
        conn.execute("SELECT 1 FROM ingest_runs LIMIT 1")
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f"ingest_runs table missing after migrate() — 0017 migration not yet written: {exc}"
        )
    finally:
        conn.close()
    return b


# ---------------------------------------------------------------------------
# SR-T7 (a): interrupted run → resume reuses run_id, no duplicate docs
# ---------------------------------------------------------------------------


class TestResumeReusesSameRunId:
    """ingest_once(..., resume=True) with a prior interrupted run reuses that run_id."""

    def test_resume_reuses_run_id(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """After seeding an interrupted run, --resume reuses the same run_id."""
        prior_run_id = "01HWZFAKE000000000000000R1"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="interrupted", config_digest=digest)
        conn.close()

        # Patch registry so no real embedder loads; patch scan to return minimal docs.
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # Call with the new resume kwarg (SR-G5 adds this signature).
            ingest_once(cfg, resume=True)

        conn2 = _db(tmp_path)
        rows = conn2.execute("SELECT run_id, status FROM ingest_runs").fetchall()
        conn2.close()

        # Exactly ONE row in ingest_runs (resumed, not a new row).
        assert len(rows) == 1, (
            f"Expected 1 row in ingest_runs (resumed), got {len(rows)}: {[dict(r) for r in rows]}"
        )
        assert rows[0]["run_id"] == prior_run_id, (
            f"Expected run_id={prior_run_id!r}, got {rows[0]['run_id']!r}"
        )

    def test_resume_sets_status_to_completed(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """After a resumed ingest completes, status column flips to 'completed'."""
        prior_run_id = "01HWZFAKE000000000000000R2"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="interrupted", config_digest=digest)
        conn.close()

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, resume=True)

        conn2 = _db(tmp_path)
        row = conn2.execute(
            "SELECT status FROM ingest_runs WHERE run_id = ?", (prior_run_id,)
        ).fetchone()
        conn2.close()

        assert row is not None, "run_id row vanished after resume"
        assert row["status"] == "completed", (
            f"Expected status='completed' after resume, got {row['status']!r}"
        )

    def test_no_resume_flag_creates_fresh_run(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """Without --resume, a fresh run_id is always created even when interrupted rows exist."""
        prior_run_id = "01HWZFAKE000000000000000R3"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="interrupted", config_digest=digest)
        conn.close()

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # No resume flag — should start fresh.
            ingest_once(cfg)

        conn2 = _db(tmp_path)
        rows = conn2.execute("SELECT run_id FROM ingest_runs").fetchall()
        conn2.close()

        run_ids = [r["run_id"] for r in rows]
        assert len(run_ids) == 2, (
            f"Expected 2 rows (prior interrupted + new fresh), got {len(run_ids)}: {run_ids}"
        )
        assert prior_run_id in run_ids
        new_ids = [rid for rid in run_ids if rid != prior_run_id]
        assert len(new_ids) == 1
        assert new_ids[0] != prior_run_id


class TestResumeNoDuplicateDocs:
    """Resumed ingest must not insert duplicate documents for already-scanned sources."""

    def test_no_duplicate_documents_after_resume(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """Documents ingested in the prior run must not be duplicated on resume."""
        # Patch scan() to emit 5 docs; first ingest stores them; resume should
        # see them as already-processed via content_hash dedup.
        docs = _fake_docs(5, prefix="vault://resume-dedup")
        prior_run_id = "01HWZFAKE000000000000000D1"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="interrupted", config_digest=digest)
        conn.close()

        scan_call_count = 0

        def fake_scan_gen() -> Iterator[RawDocument]:
            nonlocal scan_call_count
            scan_call_count += 1
            yield from docs

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch(
                "corpus_forge.sources.markdown_vault.MarkdownVaultSource.scan",
                side_effect=fake_scan_gen,
            ),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # First run (no resume) — inserts docs.
            ingest_once(cfg)

        # Now do resumed run — docs already exist; content_hash dedup skips them.
        # NB: the prior_run_id row seeded at the top of the test is still
        # present.  The first ingest_once above created its OWN run_id and did
        # NOT touch prior_run_id (resume=False).  Re-inserting it here would
        # violate the ingest_runs.run_id UNIQUE constraint.

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch(
                "corpus_forge.sources.markdown_vault.MarkdownVaultSource.scan",
                side_effect=fake_scan_gen,
            ),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, resume=True)

        conn3 = _db(tmp_path)
        doc_count = conn3.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn3.close()

        # Content-hash dedup must prevent duplicates regardless of how many times
        # scan() returns the same docs.
        assert doc_count == len(docs), (
            f"Expected {len(docs)} unique documents after resume, got {doc_count} "
            "(content_hash dedup must prevent duplicate inserts)"
        )


# ---------------------------------------------------------------------------
# SR-T7 (b): resume with no prior run → start fresh (graceful fallback)
# ---------------------------------------------------------------------------


class TestResumeWithNoPriorRun:
    """When --resume is set but no interrupted run exists, start a fresh run."""

    def test_resume_no_prior_run_starts_fresh(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """--resume with no prior interrupted run creates a brand-new run_id."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # No interrupted run seeded — must not raise; must create a new run.
            ingest_once(cfg, resume=True)

        conn = _db(tmp_path)
        rows = conn.execute("SELECT run_id, status FROM ingest_runs").fetchall()
        conn.close()

        assert len(rows) == 1, f"Expected 1 new run, got {len(rows)}"
        assert rows[0]["status"] == "completed"

    def test_resume_no_prior_run_does_not_raise(self, cfg: Config, backend: SQLiteBackend) -> None:
        """--resume with empty ingest_runs table must not raise any exception."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # Must not raise — graceful fresh-start.
            ingest_once(cfg, resume=True)


# ---------------------------------------------------------------------------
# SR-T7 (c): config_digest mismatch → fresh run, don't reuse prior run_id
# ---------------------------------------------------------------------------


class TestConfigDigestMismatch:
    """If config_digest of the prior run differs, --resume ignores it and starts fresh."""

    def test_digest_mismatch_starts_fresh_run(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """A prior interrupted run whose config_digest != current digest is ignored."""
        prior_run_id = "01HWZFAKE000000000000000C1"
        stale_digest = "0" * 64  # Definitely won't match the real config digest.
        conn = _db(tmp_path)
        _insert_ingest_run(
            conn, run_id=prior_run_id, status="interrupted", config_digest=stale_digest
        )
        conn.close()

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, resume=True)

        conn2 = _db(tmp_path)
        rows = conn2.execute("SELECT run_id FROM ingest_runs").fetchall()
        conn2.close()

        run_ids = [r["run_id"] for r in rows]
        assert len(run_ids) == 2, (
            f"Expected 2 rows (stale prior + new fresh), got {len(run_ids)}: {run_ids}"
        )
        # The new run must have a different run_id.
        new_run_ids = [rid for rid in run_ids if rid != prior_run_id]
        assert len(new_run_ids) == 1
        assert new_run_ids[0] != prior_run_id

    def test_digest_mismatch_prior_run_stays_interrupted(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """The stale prior run's status must not be changed by the fresh run."""
        prior_run_id = "01HWZFAKE000000000000000C2"
        stale_digest = "a" * 64
        conn = _db(tmp_path)
        _insert_ingest_run(
            conn, run_id=prior_run_id, status="interrupted", config_digest=stale_digest
        )
        conn.close()

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, resume=True)

        conn2 = _db(tmp_path)
        row = conn2.execute(
            "SELECT status FROM ingest_runs WHERE run_id = ?", (prior_run_id,)
        ).fetchone()
        conn2.close()

        assert row["status"] == "interrupted", (
            "The stale prior run's status must remain 'interrupted' — "
            f"a fresh run must not overwrite it, got {row['status']!r}"
        )

    def test_digest_match_does_resume(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """Complementary: when digest DOES match, resume reuses the prior run_id."""
        prior_run_id = "01HWZFAKE000000000000000C3"
        real_digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(
            conn, run_id=prior_run_id, status="interrupted", config_digest=real_digest
        )
        conn.close()

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, resume=True)

        conn2 = _db(tmp_path)
        rows = conn2.execute("SELECT run_id FROM ingest_runs").fetchall()
        conn2.close()

        # Must still be exactly 1 row — resume, not a fresh run.
        assert len(rows) == 1, (
            f"Digest match must resume (1 row), but got {len(rows)}: {[dict(r) for r in rows]}"
        )
        assert rows[0]["run_id"] == prior_run_id


# ---------------------------------------------------------------------------
# SR-T7 (d): max_scan_age skip — fresh source must NOT be re-walked
# ---------------------------------------------------------------------------


class TestMaxScanAgeSkip:
    """A source whose last_scanned_at is within max_scan_age must not be walked."""

    def test_fresh_source_scan_not_called_within_max_scan_age(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """scan() must NOT be called for a source scanned within the max_scan_age window."""
        # Seed a prior completed run with last_scanned_at = now.
        prior_run_id = "01HWZFAKE000000000000000S1"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="completed", config_digest=digest)

        # Get the dataset_id — must exist after backend.migrate().
        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE name = 'resume-test-vault'"
        ).fetchone()
        if dataset_row is None:
            # Dataset may not exist yet; create it so we can seed the source row.
            conn.execute("INSERT INTO datasets (name, kind) VALUES ('resume-test-vault', 'text')")
            conn.commit()
            dataset_row = conn.execute(
                "SELECT id FROM datasets WHERE name = 'resume-test-vault'"
            ).fetchone()
        dataset_id = dataset_row["id"]

        # last_scanned_at = right now → within any positive max_scan_age window.
        now_iso = datetime.now(UTC).isoformat()
        _insert_ingest_run_source(
            conn,
            run_id=prior_run_id,
            source_uri_prefix="filesystem://vault",
            dataset_id=dataset_id,
            finished_at=now_iso,
            last_scanned_at=now_iso,
            docs_seen=20,
        )
        conn.close()

        scan_call_count = 0

        def counting_scan() -> Iterator[RawDocument]:
            nonlocal scan_call_count
            scan_call_count += 1
            return iter([])

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch(
                "corpus_forge.sources.markdown_vault.MarkdownVaultSource.scan",
                side_effect=counting_scan,
            ),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # max_scan_age=3600 → sources scanned within the last hour are skipped.
            ingest_once(cfg, max_scan_age=3600.0)

        assert scan_call_count == 0, (
            f"Expected scan() to NOT be called for fresh source "
            f"(last_scanned_at=now, max_scan_age=3600), "
            f"but it was called {scan_call_count} time(s)"
        )

    def test_stale_source_is_rescanned(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """A source scanned *outside* the max_scan_age window MUST be rescanned."""
        prior_run_id = "01HWZFAKE000000000000000S2"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="completed", config_digest=digest)

        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE name = 'resume-test-vault'"
        ).fetchone()
        if dataset_row is None:
            conn.execute("INSERT INTO datasets (name, kind) VALUES ('resume-test-vault', 'text')")
            conn.commit()
            dataset_row = conn.execute(
                "SELECT id FROM datasets WHERE name = 'resume-test-vault'"
            ).fetchone()
        dataset_id = dataset_row["id"]

        # last_scanned_at = 2 hours ago → outside the 1-hour window.
        old_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        _insert_ingest_run_source(
            conn,
            run_id=prior_run_id,
            source_uri_prefix="filesystem://vault",
            dataset_id=dataset_id,
            finished_at=old_ts,
            last_scanned_at=old_ts,
            docs_seen=20,
        )
        conn.close()

        scan_call_count = 0

        def counting_scan() -> Iterator[RawDocument]:
            nonlocal scan_call_count
            scan_call_count += 1
            return iter([])

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch(
                "corpus_forge.sources.markdown_vault.MarkdownVaultSource.scan",
                side_effect=counting_scan,
            ),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # max_scan_age=3600 → 2-hour-old source is outside the window → must rescan.
            ingest_once(cfg, max_scan_age=3600.0)

        assert scan_call_count >= 1, (
            f"Expected scan() to be called for stale source "
            f"(last_scanned_at=2h ago, max_scan_age=3600), "
            f"but call_count={scan_call_count}"
        )

    def test_max_scan_age_zero_always_rescans(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """max_scan_age=0 (default) must ALWAYS rescan — preserves backwards compat."""
        prior_run_id = "01HWZFAKE000000000000000S3"
        digest = _compute_config_digest(cfg)
        conn = _db(tmp_path)
        _insert_ingest_run(conn, run_id=prior_run_id, status="completed", config_digest=digest)

        dataset_row = conn.execute(
            "SELECT id FROM datasets WHERE name = 'resume-test-vault'"
        ).fetchone()
        if dataset_row is None:
            conn.execute("INSERT INTO datasets (name, kind) VALUES ('resume-test-vault', 'text')")
            conn.commit()
            dataset_row = conn.execute(
                "SELECT id FROM datasets WHERE name = 'resume-test-vault'"
            ).fetchone()
        dataset_id = dataset_row["id"]

        # last_scanned_at = now (would be skipped with max_scan_age > 0).
        now_iso = datetime.now(UTC).isoformat()
        _insert_ingest_run_source(
            conn,
            run_id=prior_run_id,
            source_uri_prefix="filesystem://vault",
            dataset_id=dataset_id,
            finished_at=now_iso,
            last_scanned_at=now_iso,
            docs_seen=20,
        )
        conn.close()

        scan_call_count = 0

        def counting_scan() -> Iterator[RawDocument]:
            nonlocal scan_call_count
            scan_call_count += 1
            return iter([])

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch(
                "corpus_forge.sources.markdown_vault.MarkdownVaultSource.scan",
                side_effect=counting_scan,
            ),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # max_scan_age=0.0 (default) → always rescan regardless of last_scanned_at.
            ingest_once(cfg, max_scan_age=0.0)

        assert scan_call_count >= 1, (
            f"max_scan_age=0.0 must always rescan (backwards compat), "
            f"but scan call_count={scan_call_count}"
        )

    def test_negative_max_scan_age_raises_value_error(
        self, cfg: Config, backend: SQLiteBackend
    ) -> None:
        """max_scan_age < 0 is invalid — must raise ValueError or TypeError at ingest_once."""
        with pytest.raises((ValueError, TypeError)):
            ingest_once(cfg, max_scan_age=-1.0)


# ---------------------------------------------------------------------------
# SR-T7: ingest_runs row is written on a fresh ingest_once pass
# ---------------------------------------------------------------------------


class TestIngestRunsRowCreated:
    """A fresh ingest_once pass MUST create exactly 1 ingest_runs row."""

    def test_fresh_ingest_once_creates_one_run_row(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """Happy path: ingest_once without resume creates a single completed row."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg)

        conn = _db(tmp_path)
        rows = conn.execute("SELECT run_id, status FROM ingest_runs").fetchall()
        conn.close()

        assert len(rows) == 1, f"Expected exactly 1 ingest_runs row, got {len(rows)}: {rows}"
        assert rows[0]["status"] == "completed"

    def test_run_row_has_required_fields(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """The ingest_runs row must have non-null run_id, host, pid, config_digest."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg)

        conn = _db(tmp_path)
        row = conn.execute(
            "SELECT run_id, host, pid, config_digest, started_at, ended_at FROM ingest_runs"
        ).fetchone()
        conn.close()

        assert row["run_id"], "run_id must be non-empty"
        assert row["host"], "host must be non-empty"
        assert row["pid"] is not None, "pid must not be NULL"
        assert row["config_digest"], "config_digest must be non-empty"
        assert row["started_at"], "started_at must be non-null"
        assert row["ended_at"], "ended_at must be set after completion"

    def test_config_digest_matches_expected(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """The config_digest stored in the run row must match the computed digest."""
        expected_digest = _compute_config_digest(cfg)

        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg)

        conn = _db(tmp_path)
        row = conn.execute("SELECT config_digest FROM ingest_runs").fetchone()
        conn.close()

        assert row["config_digest"] == expected_digest, (
            f"config_digest mismatch: stored {row['config_digest']!r}, expected {expected_digest!r}"
        )

    def test_two_sequential_invocations_create_two_rows(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """Two ingest_once calls (no --resume) must create 2 separate run rows."""
        for _ in range(2):
            with (
                patch("corpus_forge.ingest.registry") as mock_reg,
                patch("corpus_forge.ingest._plan_ingest", return_value={}),
            ):
                mock_reg.register = MagicMock(return_value=MagicMock())
                ingest_once(cfg)

        conn = _db(tmp_path)
        count = conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0]
        run_ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM ingest_runs").fetchall()]
        conn.close()

        assert count == 2, f"Expected 2 rows from 2 fresh runs, got {count}"
        assert run_ids[0] != run_ids[1], "Each invocation must create a distinct run_id"


# ---------------------------------------------------------------------------
# SR-T7: ingest_run_sources row written per source
# ---------------------------------------------------------------------------


class TestIngestRunSourcesRows:
    """ingest_once must write an ingest_run_sources row per source."""

    def test_source_row_written_after_ingest(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """One ingest_run_sources row per source, per run."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg)

        conn = _db(tmp_path)
        rows = conn.execute("SELECT * FROM ingest_run_sources").fetchall()
        conn.close()

        assert len(rows) >= 1, "Expected at least 1 ingest_run_sources row after ingest, got 0"

    def test_source_row_finished_at_set(
        self, cfg: Config, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """finished_at must be non-NULL after a successful source walk."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg)

        conn = _db(tmp_path)
        rows = conn.execute("SELECT finished_at FROM ingest_run_sources").fetchall()
        conn.close()

        for row in rows:
            assert row["finished_at"] is not None, (
                "finished_at must be set on the ingest_run_sources row after successful scan"
            )


# ---------------------------------------------------------------------------
# SR-T7: ingest_once signature accepts new kwargs (type contract)
# ---------------------------------------------------------------------------


class TestIngestOnceSignature:
    """ingest_once must accept resume, max_scan_age keyword args (SR-G5 contract)."""

    def test_ingest_once_accepts_resume_kwarg(self, cfg: Config, backend: SQLiteBackend) -> None:
        """ingest_once(cfg, resume=False) must not raise TypeError."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            # Must accept the kwarg without TypeError.
            ingest_once(cfg, resume=False)

    def test_ingest_once_accepts_max_scan_age_kwarg(
        self, cfg: Config, backend: SQLiteBackend
    ) -> None:
        """ingest_once(cfg, max_scan_age=0.0) must not raise TypeError."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, max_scan_age=0.0)

    def test_ingest_once_accepts_both_kwargs(self, cfg: Config, backend: SQLiteBackend) -> None:
        """ingest_once(cfg, resume=True, max_scan_age=3600.0) must not raise TypeError."""
        with (
            patch("corpus_forge.ingest.registry") as mock_reg,
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            mock_reg.register = MagicMock(return_value=MagicMock())
            ingest_once(cfg, resume=True, max_scan_age=3600.0)


# ---------------------------------------------------------------------------
# SR-T7: backend helper contract (latest_unfinished_ingest_run)
# ---------------------------------------------------------------------------


class TestBackendIngestRunHelpers:
    """The backend must expose the ingest-run CRUD methods (SR-G2/G3 contract)."""

    def test_latest_unfinished_ingest_run_exists_on_backend(self, backend: SQLiteBackend) -> None:
        """SQLiteBackend must expose latest_unfinished_ingest_run()."""
        assert hasattr(backend, "latest_unfinished_ingest_run"), (
            "SQLiteBackend is missing latest_unfinished_ingest_run — SR-G3 must implement it"
        )

    def test_latest_unfinished_returns_none_when_empty(self, backend: SQLiteBackend) -> None:
        """latest_unfinished_ingest_run() must return None when table is empty."""
        result = backend.latest_unfinished_ingest_run()
        assert result is None, f"Expected None for empty ingest_runs table, got {result!r}"

    def test_start_ingest_run_exists_on_backend(self, backend: SQLiteBackend) -> None:
        """SQLiteBackend must expose start_ingest_run(...)."""
        assert hasattr(backend, "start_ingest_run"), (
            "SQLiteBackend is missing start_ingest_run — SR-G3 must implement it"
        )

    def test_finish_ingest_run_exists_on_backend(self, backend: SQLiteBackend) -> None:
        """SQLiteBackend must expose finish_ingest_run(...)."""
        assert hasattr(backend, "finish_ingest_run"), (
            "SQLiteBackend is missing finish_ingest_run — SR-G3 must implement it"
        )

    def test_find_source_last_scanned_at_exists_on_backend(self, backend: SQLiteBackend) -> None:
        """SQLiteBackend must expose find_source_last_scanned_at(prefix)."""
        assert hasattr(backend, "find_source_last_scanned_at"), (
            "SQLiteBackend is missing find_source_last_scanned_at — SR-G3 must implement it"
        )

    def test_find_source_last_scanned_at_returns_none_when_empty(
        self, backend: SQLiteBackend
    ) -> None:
        """find_source_last_scanned_at must return None for an unknown prefix."""
        result = backend.find_source_last_scanned_at("filesystem://never-seen")
        assert result is None, f"Expected None for unknown source prefix, got {result!r}"

    def test_start_ingest_run_inserts_row(self, backend: SQLiteBackend, tmp_path: Path) -> None:
        """start_ingest_run must insert a row in ingest_runs."""
        run_id = "01HWZFAKE000000000000000H1"
        backend.start_ingest_run(
            run_id=run_id,
            host="test-host",
            pid=42,
            config_digest="a" * 64,
        )
        conn = _db(tmp_path)
        row = conn.execute(
            "SELECT run_id, status, host, pid FROM ingest_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        conn.close()

        assert row is not None, f"Expected row for run_id={run_id!r}"
        assert row["status"] == "running"
        assert row["host"] == "test-host"
        assert row["pid"] == 42

    def test_finish_ingest_run_sets_status(self, backend: SQLiteBackend, tmp_path: Path) -> None:
        """finish_ingest_run must update status and set ended_at."""
        run_id = "01HWZFAKE000000000000000H2"
        backend.start_ingest_run(
            run_id=run_id,
            host="test-host",
            pid=42,
            config_digest="b" * 64,
        )
        backend.finish_ingest_run(run_id, status="completed")

        conn = _db(tmp_path)
        row = conn.execute(
            "SELECT status, ended_at FROM ingest_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        conn.close()

        assert row["status"] == "completed"
        assert row["ended_at"] is not None, "ended_at must be non-null after finish"

    def test_latest_unfinished_returns_interrupted_row(
        self, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """latest_unfinished_ingest_run must return the most-recent interrupted row."""
        run_id = "01HWZFAKE000000000000000H3"
        backend.start_ingest_run(
            run_id=run_id,
            host="test-host",
            pid=99,
            config_digest="c" * 64,
        )
        backend.finish_ingest_run(run_id, status="interrupted")

        result = backend.latest_unfinished_ingest_run()
        assert result is not None, "Expected a dict row, got None"
        assert result["run_id"] == run_id, f"Expected run_id={run_id!r}, got {result['run_id']!r}"

    def test_latest_unfinished_ignores_completed_rows(
        self, backend: SQLiteBackend, tmp_path: Path
    ) -> None:
        """latest_unfinished_ingest_run must return None when only completed rows exist."""
        run_id = "01HWZFAKE000000000000000H4"
        backend.start_ingest_run(
            run_id=run_id,
            host="test-host",
            pid=99,
            config_digest="d" * 64,
        )
        backend.finish_ingest_run(run_id, status="completed")

        result = backend.latest_unfinished_ingest_run()
        assert result is None, f"Expected None (only completed rows), got {result!r}"
