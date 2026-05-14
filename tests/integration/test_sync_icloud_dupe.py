"""E2E integration tests for iCloud-duplicate-file cleanup (P1-32).

Production code is already shipped. These are characterization-style pins that
assert the accepted behaviours from tasks.md §P1-32:

  1. Drop 'Foo 2.md' with the SAME content as 'Foo.md'
     → push deletes 'Foo 2.md'; exactly one documents row exists, no extra revision.

  2. Drop 'Foo 2.md' with DIFFERENT content from 'Foo.md'
     → push renames it to 'Foo.conflict-icloud-macA-<ts>.md' and inserts a conflict
        revision (second documents row).

iCloud path simulation (Wave 13 risk-mitigation):
  production detect_cloud_provider does substring match on str(path.resolve()) for
  'Library/Mobile Documents/com~apple~CloudDocs'. We build that suffix under
  tmp_path — no real iCloud needed.

Production-code bugs surfaced by this suite (do NOT paper over):
  BUG-PUSH-DUPE: PushPipeline.handle_change() never calls _handle_cloud_duplicate().
    The _DebouncedHandler routes on_created/on_modified → handle_change only.
    The cloud-dupe cleanup branch is dead code from the watchdog perspective.
  BUG-PUSH-RESOLVE: handle_change() calls self._backend.resolve_document() which
    does not exist on PostgresBackend (not in base.py protocol either).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.sync.cloud import detect_cloud_provider
from corpus_forge.sync.echo import EchoSuppressor
from corpus_forge.sync.push import PushPipeline

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST = "macA"
_DATASET_NAME = "icloud-dupe-test"
_POLL_INTERVAL = 0.5


def _make_backend(pg_dsn: str) -> PostgresBackend:
    return PostgresBackend(dsn=pg_dsn, schema="corpus")


def _icloud_root(tmp_path):
    """Build a directory whose resolved path contains the iCloud substring.

    detect_cloud_provider does:
        "Library/Mobile Documents/com~apple~CloudDocs" in str(path.resolve())

    Building this suffix under tmp_path satisfies the substring check without
    requiring real iCloud.
    """
    root = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "test-vault"
    root.mkdir(parents=True)
    return root


def _wait_for(condition, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    """Spin-poll *condition()* until it returns truthy or *timeout* elapses.

    Returns True if condition was satisfied, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _doc_count(pg_dsn: str, dataset_name: str) -> int:
    """Return the total number of documents rows for a dataset."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n
            FROM corpus.documents d
            JOIN corpus.datasets ds ON ds.id = d.dataset_id
            WHERE ds.name = %s
            """,
            (dataset_name,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def _revision_count(pg_dsn: str, dataset_name: str) -> int:
    """Return total revision rows for all documents in a dataset."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n
            FROM corpus.document_revisions r
            JOIN corpus.documents d ON d.id = r.document_id
            JOIN corpus.datasets ds ON ds.id = d.dataset_id
            WHERE ds.name = %s
            """,
            (dataset_name,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def _ensure_dataset(pg_dsn: str, dataset_name: str) -> int:
    """Ensure a 'text' dataset row exists and return its id."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO corpus.datasets (name, kind)
            VALUES (%s, 'text')
            ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
            RETURNING id
            """,
            (dataset_name,),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row[0])


# ---------------------------------------------------------------------------
# Test 1 — same-hash dedup: Foo 2.md matches Foo.md → delete
# ---------------------------------------------------------------------------


class TestICloudDupeSameHashDeleted:
    """Dropping a same-content duplicate triggers cloud-dupe deletion.

    Acceptance: push deletes 'Foo 2.md'; exactly one documents row; no extra
    revision beyond the initial create of 'Foo.md'.
    """

    def test_icloud_substring_detected(self, tmp_path):
        """Sanity: our iCloud-shaped path actually triggers detect_cloud_provider.

        This is the highest-risk technicality from the Wave 13 notes.
        """
        root = _icloud_root(tmp_path)
        assert detect_cloud_provider(root) == "icloud", (
            f"iCloud path simulation failed — detect_cloud_provider returned "
            f"'none' for {root.resolve()}.  The substring "
            f"'Library/Mobile Documents/com~apple~CloudDocs' must appear in the "
            f"resolved path."
        )

    def test_icloud_dupe_same_hash_deleted(self, pg_dsn: str, tmp_path):
        """Drop Foo 2.md with same hash as Foo.md → push deletes Foo 2.md.

        Acceptance invariants (from tasks.md §P1-32):
          - Foo 2.md no longer exists on disk after push processes it.
          - Exactly one documents row exists (Foo.md only).
          - No extra revision row (original create of Foo.md counts as 1;
            the dupe must not add a second).
        """
        backend = _make_backend(pg_dsn)
        backend.migrate()
        dataset_id = _ensure_dataset(pg_dsn, _DATASET_NAME + "-same")
        icloud_root = _icloud_root(tmp_path)

        echo = EchoSuppressor()
        pipeline = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST,
        )
        pipeline.start(
            source_root=icloud_root,
            exclude_globs=(".obsidian/**", ".trash/**", ".*", "*.icloud"),
            debounce_seconds=0.1,
        )

        try:
            # 1. Create Foo.md and wait for it to be ingested
            foo_content = "# Foo\n\nbody text for same-hash test"
            (icloud_root / "Foo.md").write_text(foo_content, encoding="utf-8")

            ingested = _wait_for(
                lambda: _doc_count(pg_dsn, _DATASET_NAME + "-same") >= 1,
                timeout=15,
            )
            assert ingested, "Foo.md was not ingested within timeout"

            initial_doc_count = _doc_count(pg_dsn, _DATASET_NAME + "-same")
            initial_rev_count = _revision_count(pg_dsn, _DATASET_NAME + "-same")

            # 2. Drop Foo 2.md with IDENTICAL content (same hash)
            (icloud_root / "Foo 2.md").write_text(foo_content, encoding="utf-8")

            # 3. Wait for the dupe to be removed from disk
            dupe_gone = _wait_for(
                lambda: not (icloud_root / "Foo 2.md").exists(),
                timeout=10,
            )
            assert dupe_gone, (
                "Foo 2.md still exists — cloud-dupe cleanup (same-hash path) did "
                "not fire within timeout.  "
                "BUG-PUSH-DUPE: PushPipeline.handle_change() never calls "
                "_handle_cloud_duplicate(); the _DebouncedHandler routes all "
                "on_created/on_modified events to handle_change only."
            )

            # 4. Still exactly one documents row (no duplicate row created)
            final_doc_count = _doc_count(pg_dsn, _DATASET_NAME + "-same")
            assert final_doc_count == initial_doc_count, (
                f"Expected {initial_doc_count} document row(s) but found "
                f"{final_doc_count}.  The cloud-dupe should not create a new row."
            )

            # 5. No extra revision row from the duplicate
            final_rev_count = _revision_count(pg_dsn, _DATASET_NAME + "-same")
            assert final_rev_count == initial_rev_count, (
                f"Expected {initial_rev_count} revision(s) but found {final_rev_count}. "
                "The same-hash cloud-dupe must not insert any revision."
            )

        finally:
            pipeline.stop()


# ---------------------------------------------------------------------------
# Test 2 — differing-hash: Foo 2.md differs from Foo.md → conflict rename
# ---------------------------------------------------------------------------


class TestICloudDupeDiffHashRenamed:
    """Dropping a different-content duplicate triggers conflict-file rename.

    Acceptance: push renames 'Foo 2.md' to
    'Foo.conflict-icloud-macA-<ts>.md' and inserts a conflict revision
    (second documents row visible to queries).
    """

    def test_icloud_dupe_diff_hash_renamed(self, pg_dsn: str, tmp_path):
        """Drop Foo 2.md with different hash → renamed to conflict file.

        Acceptance invariants (from tasks.md §P1-32):
          - 'Foo 2.md' no longer exists on disk after push processes it.
          - A file matching 'Foo.conflict-icloud-macA-*.md' appears.
          - Two documents rows exist (Foo.md + the conflict file).
        """
        backend = _make_backend(pg_dsn)
        backend.migrate()
        dataset_id = _ensure_dataset(pg_dsn, _DATASET_NAME + "-diff")
        icloud_root = _icloud_root(tmp_path)

        echo = EchoSuppressor()
        pipeline = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST,
        )
        pipeline.start(
            source_root=icloud_root,
            exclude_globs=(".obsidian/**", ".trash/**", ".*", "*.icloud"),
            debounce_seconds=0.1,
        )

        try:
            # 1. Create Foo.md and wait for ingestion
            (icloud_root / "Foo.md").write_text(
                "# Foo\n\noriginal body for diff-hash test", encoding="utf-8"
            )

            ingested = _wait_for(
                lambda: _doc_count(pg_dsn, _DATASET_NAME + "-diff") >= 1,
                timeout=15,
            )
            assert ingested, "Foo.md was not ingested within timeout"

            # 2. Drop Foo 2.md with DIFFERENT content (different hash)
            (icloud_root / "Foo 2.md").write_text(
                "# Foo\n\nDIFFERENT body — this should become a conflict file",
                encoding="utf-8",
            )

            # 3. Wait for the conflict file to appear on disk
            conflict_files: list = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                conflict_files = list(icloud_root.glob("Foo.conflict-icloud-macA-*.md"))
                if conflict_files:
                    break
                time.sleep(0.1)

            assert len(conflict_files) == 1, (
                f"Expected exactly one 'Foo.conflict-icloud-macA-*.md' but found "
                f"{len(conflict_files)}: {conflict_files}.  "
                "BUG-PUSH-DUPE: PushPipeline.handle_change() never calls "
                "_handle_cloud_duplicate(); the _DebouncedHandler routes all "
                "on_created/on_modified events to handle_change only.  "
                "The diff-hash rename path is therefore unreachable from the "
                "watchdog event loop."
            )

            # 4. Foo 2.md must be gone (renamed, not just copied)
            assert not (icloud_root / "Foo 2.md").exists(), (
                "Foo 2.md still exists — rename did not happen."
            )

            # 5. Two documents rows: Foo.md and the conflict file.
            # The conflict file was just created on disk (step 3); the daemon
            # needs a debounce cycle + ingest pass to surface it in the DB.
            # Wait up to 15s for the second document to appear.
            expected_doc_count = 2
            ingested_conflict = _wait_for(
                lambda: _doc_count(pg_dsn, _DATASET_NAME + "-diff") >= expected_doc_count,
                timeout=15,
            )
            final_doc_count = _doc_count(pg_dsn, _DATASET_NAME + "-diff")
            assert ingested_conflict and final_doc_count == expected_doc_count, (
                f"Expected {expected_doc_count} document rows (Foo.md + conflict file) "
                f"but found {final_doc_count} after waiting for ingest."
            )

        finally:
            pipeline.stop()


# ---------------------------------------------------------------------------
# Test 3 — conflict-file naming format pin
# ---------------------------------------------------------------------------


class TestConflictFilenameFormat:
    """Pin the conflict filename format independently of the E2E watchdog path.

    This calls _handle_cloud_duplicate directly with a real on-disk setup
    so we can exercise the rename logic without depending on the (broken)
    watchdog routing.
    """

    def test_conflict_file_name_format_with_provider(self, pg_dsn: str, tmp_path):
        """_handle_cloud_duplicate renames Foo 2.md → Foo.conflict-icloud-macA-<ts>.md.

        This is a direct-call test that bypasses the watchdog, exercising the
        rename logic in isolation.  The format must match
        <stem>.conflict-<provider>-<host>-<ts><suffix>.
        """
        backend = _make_backend(pg_dsn)
        backend.migrate()
        dataset_id = _ensure_dataset(pg_dsn, _DATASET_NAME + "-fmt")
        icloud_root = _icloud_root(tmp_path)

        # Ensure canonical Foo.md exists on disk (needed by _handle_cloud_duplicate
        # to compare hashes)
        (icloud_root / "Foo.md").write_text("# Foo\n\ncanonical content", encoding="utf-8")

        # Write Foo 2.md with DIFFERENT content
        dupe = icloud_root / "Foo 2.md"
        dupe.write_text("# Foo\n\ndifferent content", encoding="utf-8")

        echo = EchoSuppressor()
        pipeline = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST,
        )
        # Start + stop immediately to wire dataset_id; we call _handle_cloud_duplicate
        # directly, so we don't need the observer running.
        pipeline.start(source_root=icloud_root, debounce_seconds=0.1)
        try:
            # Call the cloud-duplicate handler directly
            handled = pipeline._handle_cloud_duplicate(dupe)

            assert handled is True, (
                "_handle_cloud_duplicate returned False — either is_cloud_duplicate "
                "did not recognise 'Foo 2.md' as a duplicate or canonical_path "
                "('Foo.md') was not found."
            )

            # Foo 2.md must be gone (renamed)
            assert not dupe.exists(), (
                "Foo 2.md still exists after _handle_cloud_duplicate with diff hash"
            )

            # A conflict file matching the expected pattern must exist
            conflict_files = list(icloud_root.glob("Foo.conflict-icloud-macA-*.md"))
            assert len(conflict_files) == 1, (
                f"Expected exactly one 'Foo.conflict-icloud-macA-*.md' but found "
                f"{len(conflict_files)}: {conflict_files}"
            )

            # Validate the name format: Foo.conflict-icloud-macA-<ts>.md
            conflict_name = conflict_files[0].name
            # Must start with 'Foo.conflict-icloud-macA-'
            assert conflict_name.startswith("Foo.conflict-icloud-macA-"), (
                f"Conflict filename '{conflict_name}' does not match expected "
                f"'Foo.conflict-icloud-macA-<ts>.md' format"
            )
            # Must end with '.md'
            assert conflict_name.endswith(".md"), (
                f"Conflict filename '{conflict_name}' does not end with '.md'"
            )

        finally:
            pipeline.stop()

    def test_same_hash_dupe_deleted_direct(self, tmp_path):
        """_handle_cloud_duplicate deletes Foo 2.md when hashes match.

        Direct-call test bypassing watchdog — exercises the delete path.
        No DB interaction needed (delete path doesn't insert revisions).
        """
        icloud_root = _icloud_root(tmp_path)
        (icloud_root / "Foo.md").write_text("# same content", encoding="utf-8")
        dupe = icloud_root / "Foo 2.md"
        dupe.write_text("# same content", encoding="utf-8")  # identical → same hash

        mock_backend = MagicMock()
        echo = EchoSuppressor()
        pipeline = PushPipeline(
            backend=mock_backend,
            dataset_id=1,
            echo_suppressor=echo,
            host_id=_HOST,
        )
        # No start() needed — calling _handle_cloud_duplicate directly
        pipeline._exclude_globs = ()

        handled = pipeline._handle_cloud_duplicate(dupe)

        assert handled is True, (
            "_handle_cloud_duplicate returned False — is_cloud_duplicate must "
            "recognise 'Foo 2.md' as an iCloud duplicate of 'Foo.md'."
        )
        assert not dupe.exists(), "Foo 2.md was not deleted — same-hash dupe cleanup failed."
        # No revision should be inserted for a same-hash dupe
        mock_backend.insert_revision.assert_not_called()
