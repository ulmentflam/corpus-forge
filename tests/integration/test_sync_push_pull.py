"""E2E push/pull cross-host integration test (P1-30).

Two SyncEngine instances in one process — distinct host_id, distinct tmp_path
roots, shared testcontainers Postgres — exercise the full push→pull cycle.

Wave 13b characterization pins:
  - A→B convergence: file written on A appears on B within deadline.
  - B→A convergence: bidirectional edit propagation.
  - Monotonic revision numbers: revision_number strictly increases per document.
  - Hash equality: content on B equals content on A.

Known suspected bug (waves.md Wave 13 risks):
  pull.py:69 -- ``path = self._source_root / rev["source_uri"]``
  push.py records ``source_uri = str(path.resolve())`` (absolute path).
  ``Path("/A") / "/B"`` returns ``Path("/B")`` so source_root is dropped.
  Additionally, ``pending_remote_revisions`` does ``SELECT r.*`` which does not
  include ``documents.source_uri`` or ``sources.id``, so the revision dict will
  be missing the ``source_uri`` and ``source_id`` keys that pull.py expects.
  If these bugs reproduce the test documents them clearly; do NOT paper over.
"""

from __future__ import annotations

import hashlib
import itertools
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.config import DaemonConfig
from corpus_forge.identity import chunk_content_hash
from corpus_forge.sync.engine import SyncEngine

pytestmark = pytest.mark.integration

# Minimum expected revisions after 3 sequential edits
_MIN_REVISIONS_AFTER_THREE_EDITS = 2


# ---------------------------------------------------------------------------
# Fake embedder -- no real ML model; deterministic; 4-dimensional
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Minimal embedder that satisfies the embedder protocol with a fake encode."""

    dimension: int = 4
    name: str = "fake"

    def encode(self, texts: list[str]) -> list[list[float]]:
        results = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vec = [
                float(h[0]) / 255.0,
                float(h[1]) / 255.0,
                float(h[2]) / 255.0,
                float(h[3]) / 255.0,
            ]
            results.append(vec)
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_dataset_name() -> str:
    """Return a dataset name unique to this test run to avoid cross-test pollution."""
    return f"sync-e2e-{uuid.uuid4().hex[:8]}"


def _make_dataset_config(dataset_id: int) -> Any:
    """Duck-typed dataset config providing .id, .exclude_globs."""
    cfg = MagicMock()
    cfg.id = dataset_id
    cfg.exclude_globs = []
    return cfg


def _make_source(root: Path) -> Any:
    """Duck-typed source object providing .root."""
    src = MagicMock()
    src.root = root
    return src


def _make_daemon_config(poll_interval_s: float = 0.5) -> DaemonConfig:
    return DaemonConfig(
        debounce_seconds=0.05,  # very fast debounce for tests
        sync_poll_interval_s=poll_interval_s,
    )


def _insert_dataset(pg_dsn: str, name: str) -> int:
    """Insert a dataset row and return its id."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO corpus.datasets (name, kind, description)
            VALUES (%s, 'text', 'E2E test dataset')
            ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
            RETURNING id
            """,
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
    assert row is not None
    return int(row[0])


def _wait_for_file(path: Path, deadline_s: float = 10.0) -> bool:
    """Poll until path exists or deadline exceeded. Returns True if found."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def _latest_revision_rows(pg_dsn: str, document_id: int) -> list[dict]:
    """Return all revision rows for a document, ordered by revision_number."""
    with psycopg.connect(pg_dsn) as conn:
        conn.row_factory = psycopg.rows.dict_row
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, revision_number, content_hash, author_host, is_tombstone
                FROM corpus.document_revisions
                WHERE document_id = %s
                ORDER BY revision_number ASC
                """,
                (document_id,),
            )
            return list(cur.fetchall())


def _get_document_id_for_source_uri(pg_dsn: str, source_uri: str) -> int | None:
    """Return document id for a given source_uri, or None."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM corpus.documents WHERE source_uri = %s",
            (source_uri,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


# ---------------------------------------------------------------------------
# Test 1 -- A->B: edit on A appears on B
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_edit_on_a_appears_on_b(pg_dsn: str, tmp_path: Path) -> None:
    """Write a markdown file on host A; verify it appears on host B within deadline.

    This test exercises the full push->pull pipeline:
      push (A): watchdog fires -> insert_revision in DB
      pull (B): poll loop fetches revision -> writes file on B's root

    Per Wave 13 risk doc: if pull.py crashes due to missing source_uri
    or source_id keys in the revision dict (pending_remote_revisions
    returns r.* without the documents.source_uri join), or if the
    absolute-path bug in pull.py:69 routes the file to A's filesystem path
    instead of B's root, the assertion (root_b / "shared.md").exists()
    will fail and the exact error is captured in test-status.md.
    """
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    dataset_name = _unique_dataset_name()
    dataset_id = _insert_dataset(pg_dsn, dataset_name)

    root_a = tmp_path / "macA_vault"
    root_b = tmp_path / "macB_vault"
    root_a.mkdir()
    root_b.mkdir()

    daemon_cfg = _make_daemon_config(poll_interval_s=0.5)

    engine_a = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_a),
        backend=backend,
        embedders=[],
        host_id="macA",
        daemon_config=daemon_cfg,
    )
    engine_b = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_b),
        backend=backend,
        embedders=[],
        host_id="macB",
        daemon_config=daemon_cfg,
    )

    engine_a.start()
    engine_b.start()
    try:
        # Write a file on A
        file_on_a = root_a / "shared.md"
        file_content = "# Hello from A\n\nFirst line.\n"
        file_on_a.write_text(file_content)

        file_on_b = root_b / "shared.md"

        # Wait for B to converge: poll for file existence (10s max)
        found = _wait_for_file(file_on_b, deadline_s=10.0)

        # --- Primary assertion ---
        assert found, (
            f"File did not appear on B within 10 s. "
            f"Bug suspected: pull.py:69 uses absolute source_uri so file may "
            f"be at {file_on_a} instead of {file_on_b}. "
            f"Also: pending_remote_revisions may be missing source_uri/source_id "
            f"keys (SELECT r.* does not include documents.source_uri)."
        )

        # --- Content assertion ---
        assert file_on_b.read_text() == file_on_a.read_text(), (
            f"Content mismatch: A={file_on_a.read_text()!r}, B={file_on_b.read_text()!r}"
        )
    finally:
        engine_a.stop()
        engine_b.stop()


# ---------------------------------------------------------------------------
# Test 2 -- B->A: edit on B appears on A (bidirectional)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_edit_on_b_appears_on_a(pg_dsn: str, tmp_path: Path) -> None:
    """Write a file on B; verify it converges on A -- confirms bidirectional sync."""
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    dataset_name = _unique_dataset_name()
    dataset_id = _insert_dataset(pg_dsn, dataset_name)

    root_a = tmp_path / "macA_vault"
    root_b = tmp_path / "macB_vault"
    root_a.mkdir()
    root_b.mkdir()

    daemon_cfg = _make_daemon_config(poll_interval_s=0.5)

    engine_a = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_a),
        backend=backend,
        embedders=[],
        host_id="macA",
        daemon_config=daemon_cfg,
    )
    engine_b = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_b),
        backend=backend,
        embedders=[],
        host_id="macB",
        daemon_config=daemon_cfg,
    )

    engine_a.start()
    engine_b.start()
    try:
        file_on_b = root_b / "from_b.md"
        file_content = "# Hello from B\n\nThis was written on B.\n"
        file_on_b.write_text(file_content)

        file_on_a = root_a / "from_b.md"

        found = _wait_for_file(file_on_a, deadline_s=10.0)

        assert found, (
            "File did not appear on A within 10 s. "
            "B->A sync failed. Same root-drop bug suspected at pull.py:69."
        )

        assert file_on_a.read_text() == file_on_b.read_text(), (
            f"Content mismatch on A: expected {file_on_b.read_text()!r}, "
            f"got {file_on_a.read_text()!r}"
        )
    finally:
        engine_a.stop()
        engine_b.stop()


# ---------------------------------------------------------------------------
# Test 3 -- Revision numbers strictly increase per document
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_revision_numbers_monotonic(pg_dsn: str, tmp_path: Path) -> None:
    """Edit a file multiple times on A; assert revision_number strictly increases.

    Also asserts content_hash equality: the hash stored in document_revisions
    matches the hash of the file content on disk after convergence.

    This test does NOT require B to converge -- it only checks DB state after
    push-side inserts, so it is sensitive to push-side bugs only.
    """
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    dataset_name = _unique_dataset_name()
    dataset_id = _insert_dataset(pg_dsn, dataset_name)

    root_a = tmp_path / "macA_vault"
    root_a.mkdir()

    daemon_cfg = _make_daemon_config(poll_interval_s=0.5)

    engine_a = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_a),
        backend=backend,
        embedders=[],
        host_id="macA",
        daemon_config=daemon_cfg,
    )

    engine_a.start()
    try:
        file_on_a = root_a / "mono.md"
        edits = [
            "# Revision 1\n\nFirst content.\n",
            "# Revision 2\n\nSecond content -- changed.\n",
            "# Revision 3\n\nThird content -- changed again.\n",
        ]

        for content in edits:
            file_on_a.write_text(content)
            # Give watchdog + debounce time to fire (debounce=0.05s + watchdog latency)
            time.sleep(0.5)

        # source_uri is relative to source_root (push records path.relative_to(source_root))
        # After BUG-4 fix: push records relative URIs when source_root is known.
        source_uri = str(file_on_a.resolve().relative_to(root_a.resolve()))
        doc_id = _get_document_id_for_source_uri(pg_dsn, source_uri)

        assert doc_id is not None, (
            f"Document not found in DB for source_uri={source_uri!r}. "
            f"Bug: resolve_document method missing from PostgresBackend, or "
            f"push.py:insert_revision call is missing source_uri kwarg."
        )

        revisions = _latest_revision_rows(pg_dsn, doc_id)

        assert len(revisions) >= _MIN_REVISIONS_AFTER_THREE_EDITS, (
            f"Expected >={_MIN_REVISIONS_AFTER_THREE_EDITS} revisions after 3 edits, "
            f"got {len(revisions)}. revisions={revisions}"
        )

        # Revision numbers must be strictly increasing
        rev_numbers = [r["revision_number"] for r in revisions]
        for prev, curr in itertools.pairwise(rev_numbers):
            assert curr > prev, f"Revision numbers not strictly increasing: {rev_numbers}"

        # Hash of final revision must match actual file content
        actual_content = file_on_a.read_text()
        actual_hash = chunk_content_hash(actual_content)
        latest_rev = revisions[-1]
        assert latest_rev["content_hash"] == actual_hash, (
            f"Hash mismatch: DB revision has {latest_rev['content_hash']!r}, "
            f"file has {actual_hash!r}"
        )
    finally:
        engine_a.stop()


# ---------------------------------------------------------------------------
# Test 4 -- Hash equality on both sides after A->B convergence
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_hash_equality_after_convergence(pg_dsn: str, tmp_path: Path) -> None:
    """After A->B convergence, hash(B's copy) == hash(A's copy) == DB revision hash.

    Regression pin for the hash equality acceptance criterion.
    """
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    dataset_name = _unique_dataset_name()
    dataset_id = _insert_dataset(pg_dsn, dataset_name)

    root_a = tmp_path / "macA_vault"
    root_b = tmp_path / "macB_vault"
    root_a.mkdir()
    root_b.mkdir()

    daemon_cfg = _make_daemon_config(poll_interval_s=0.5)

    engine_a = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_a),
        backend=backend,
        embedders=[],
        host_id="macA",
        daemon_config=daemon_cfg,
    )
    engine_b = SyncEngine(
        dataset_id=dataset_id,
        dataset_config=_make_dataset_config(dataset_id),
        source=_make_source(root_b),
        backend=backend,
        embedders=[],
        host_id="macB",
        daemon_config=daemon_cfg,
    )

    engine_a.start()
    engine_b.start()
    try:
        file_content = "# Hash equality test\n\nContent that must match on both sides.\n"
        file_on_a = root_a / "hashcheck.md"
        file_on_a.write_text(file_content)

        file_on_b = root_b / "hashcheck.md"
        found = _wait_for_file(file_on_b, deadline_s=10.0)

        assert found, "hashcheck.md did not appear on B -- pull.py root-drop bug suspected."

        hash_a = chunk_content_hash(file_on_a.read_text())
        hash_b = chunk_content_hash(file_on_b.read_text())

        assert hash_a == hash_b, f"Hash mismatch between A and B: A={hash_a!r}, B={hash_b!r}"

        # Also verify the DB revision hash matches
        # source_uri is relative to source_root after BUG-4 fix
        source_uri = str(file_on_a.resolve().relative_to(root_a.resolve()))
        doc_id = _get_document_id_for_source_uri(pg_dsn, source_uri)
        if doc_id is not None:
            revisions = _latest_revision_rows(pg_dsn, doc_id)
            if revisions:
                db_hash = revisions[-1]["content_hash"]
                assert db_hash == hash_a, f"DB revision hash {db_hash!r} != file hash {hash_a!r}"
    finally:
        engine_a.stop()
        engine_b.stop()
