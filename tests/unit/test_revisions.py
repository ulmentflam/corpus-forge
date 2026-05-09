"""Failing tests for revision API methods on PostgresBackend (P1-13..P1-17)."""

from unittest.mock import MagicMock, patch

from corpus_forge.backends.postgres import PostgresBackend


def _make_backend():
    """Create a PostgresBackend instance with __init__ patched out."""
    with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._execute = MagicMock()
        return backend


# ── P1-13: insert_revision ────────────────────────────────────────────────


class TestInsertRevision:
    """P1-13 — insert_revision allocates revision_number and returns shape."""

    def test_insert_first_revision_returns_expected_keys(self):
        backend = _make_backend()
        backend._execute.side_effect = [
            [{"max": None}],  # no prior revisions
            [{"id": 1, "revision_number": 1}],  # RETURNING row
        ]
        # mock lock_source to be a no-op context manager
        backend.lock_source = MagicMock()
        backend.lock_source.return_value.__enter__ = MagicMock()
        backend.lock_source.return_value.__exit__ = MagicMock()

        result = backend.insert_revision(
            document_id=42,
            source_uri="vault://doc.md",
            content_hash="abc",
            text="hello",
            parent_revision_id=None,
            author_host="macA",
            is_tombstone=False,
        )

        assert "id" in result
        assert "revision_number" in result
        assert result["id"] == 1
        assert result["revision_number"] == 1

    def test_insert_subsequent_revision_allocates_next_number(self):
        backend = _make_backend()
        backend._execute.side_effect = [
            [{"max": 5}],  # prior max revision_number
            [{"id": 10, "revision_number": 6}],  # RETURNING row
        ]
        backend.lock_source = MagicMock()
        backend.lock_source.return_value.__enter__ = MagicMock()
        backend.lock_source.return_value.__exit__ = MagicMock()

        result = backend.insert_revision(
            document_id=42,
            source_uri="vault://doc.md",
            content_hash="def",
            text="world",
            parent_revision_id=5,
            author_host="macA",
            is_tombstone=False,
        )

        assert result["revision_number"] == 6

    def test_insert_revision_acquires_advisory_lock(self):
        backend = _make_backend()
        backend._execute.side_effect = [
            [{"max": None}],
            [{"id": 1, "revision_number": 1}],
        ]
        backend.lock_source = MagicMock()
        backend.lock_source.return_value.__enter__ = MagicMock()
        backend.lock_source.return_value.__exit__ = MagicMock()

        backend.insert_revision(
            document_id=42,
            source_uri="vault://doc.md",
            content_hash="abc",
            text="hello",
            parent_revision_id=None,
            author_host="macA",
            is_tombstone=False,
        )

        backend.lock_source.assert_called_once_with("vault://doc.md")

    def test_insert_revision_accepts_all_keyword_params(self):
        backend = _make_backend()
        backend._execute.side_effect = [
            [{"max": None}],
            [{"id": 7, "revision_number": 1}],
        ]
        backend.lock_source = MagicMock()
        backend.lock_source.return_value.__enter__ = MagicMock()
        backend.lock_source.return_value.__exit__ = MagicMock()

        result = backend.insert_revision(
            document_id=1,
            source_uri="x",
            content_hash="c",
            text="t",
            parent_revision_id=0,
            author_host="h",
            is_tombstone=True,
            metadata={"reason": "cleanup"},
        )

        assert result["id"] == 7
        assert result["revision_number"] == 1


# ── P1-14: latest_revision ────────────────────────────────────────────────


class TestLatestRevision:
    """P1-14 — latest_revision returns highest revision_number or None."""

    def test_returns_highest_revision(self):
        backend = _make_backend()
        backend._execute.return_value = [
            {"id": 3, "revision_number": 3, "content_hash": "c"},
        ]

        result = backend.latest_revision(document_id=42)

        assert result is not None
        assert result["id"] == 3
        assert result["revision_number"] == 3

    def test_returns_none_when_no_revisions(self):
        backend = _make_backend()
        backend._execute.return_value = []

        result = backend.latest_revision(document_id=999)

        assert result is None

    def test_uses_order_by_revision_number_desc_and_limit_one(self):
        backend = _make_backend()
        backend._execute.return_value = [{"id": 5, "revision_number": 10}]

        backend.latest_revision(document_id=1)

        sql = backend._execute.call_args[0][0]
        assert "REVISION_NUMBER" in sql.upper()
        assert "DESC" in sql.upper() or "desc" in sql
        assert "LIMIT 1" in sql.upper() or "limit 1" in sql


# ── P1-15: pending_remote_revisions ────────────────────────────────────────


class TestPendingRemoteRevisions:
    """P1-15 — pending_remote_revisions filters dataset, excludes self, respects limit."""

    def test_returns_revisions_from_other_hosts(self):
        backend = _make_backend()
        rows = [
            {"id": 10, "revision_number": 1, "author_host": "macB"},
            {"id": 11, "revision_number": 2, "author_host": "macC"},
        ]
        backend._execute.return_value = rows

        result = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=5, self_host="macA"
        )

        assert len(result) == 2
        assert result == rows

    def test_excludes_self_hosted_revisions(self):
        backend = _make_backend()
        backend._execute.return_value = []

        result = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=0, self_host="macA"
        )

        sql = backend._execute.call_args[0][0]
        params = backend._execute.call_args[0][1]
        assert "author_host" in sql.lower()
        assert "macA" in params

    def test_filters_by_last_pulled_revision_id(self):
        backend = _make_backend()
        backend._execute.return_value = []

        result = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=100, self_host="macA"
        )

        sql = backend._execute.call_args[0][0]
        params = backend._execute.call_args[0][1]
        assert 100 in params or "100" in str(params)

    def test_orders_by_id_ascending(self):
        backend = _make_backend()
        backend._execute.return_value = []

        backend.pending_remote_revisions(dataset_id=1, last_pulled_revision_id=0, self_host="macA")

        sql = backend._execute.call_args[0][0]
        assert "ORDER BY" in sql
        assert "ASC" in sql.upper() or "asc" in sql

    def test_respects_limit_default(self):
        backend = _make_backend()
        backend._execute.return_value = []

        backend.pending_remote_revisions(dataset_id=1, last_pulled_revision_id=0, self_host="macA")

        params = backend._execute.call_args[0][1]
        assert 1024 in params or "1024" in str(params)

    def test_respects_custom_limit(self):
        backend = _make_backend()
        backend._execute.return_value = []

        backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=0, self_host="macA", limit=50
        )

        params = backend._execute.call_args[0][1]
        assert 50 in params or "50" in str(params)

    def test_handles_none_last_pulled(self):
        backend = _make_backend()
        backend._execute.return_value = []

        result = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="macA"
        )

        assert result == []

    def test_joins_documents_for_dataset_filter(self):
        backend = _make_backend()
        backend._execute.return_value = []

        backend.pending_remote_revisions(dataset_id=7, last_pulled_revision_id=0, self_host="macA")

        sql = backend._execute.call_args[0][0]
        params = backend._execute.call_args[0][1]
        assert "document" in sql.lower() or "join" in sql.lower()
        assert 7 in params or "7" in str(params)


# ── P1-16: mark_revision_pulled ────────────────────────────────────────────


class TestMarkRevisionPulled:
    """P1-16 — mark_revision_pulled advances last_pulled_revision_id."""

    def test_updates_last_pulled_revision_id(self):
        backend = _make_backend()

        backend.mark_revision_pulled(source_id=1, revision_id=42)

        sql = backend._execute.call_args[0][0]
        assert "UPDATE" in sql.upper()
        assert "sources" in sql.lower()
        assert "last_pulled_revision_id" in sql.lower()

    def test_uses_greatest_to_prevent_regression(self):
        backend = _make_backend()

        backend.mark_revision_pulled(source_id=1, revision_id=42)

        sql = backend._execute.call_args[0][0]
        assert "GREATEST" in sql.upper() or "greatest" in sql
        assert "coalesce" in sql.lower()

    def test_passes_correct_params(self):
        backend = _make_backend()

        backend.mark_revision_pulled(source_id=99, revision_id=7)

        params = backend._execute.call_args[0][1]
        assert 7 in params or "7" in str(params)
        assert 99 in params or "99" in str(params)


# ── P1-17: set_tombstone / clear_tombstone ────────────────────────────────


class TestTombstone:
    """P1-17 — set_tombstone and clear_tombstone."""

    def test_set_tombstone_updates_documents_table(self):
        backend = _make_backend()

        backend.set_tombstone(document_id=42)

        sql = backend._execute.call_args[0][0]
        assert "UPDATE" in sql.upper()
        assert "documents" in sql.lower()
        assert "tombstoned_at" in sql.lower()

    def test_set_tombstone_uses_now(self):
        backend = _make_backend()

        backend.set_tombstone(document_id=1)

        sql = backend._execute.call_args[0][0]
        params = backend._execute.call_args[0][1]
        assert "NOW()" in sql.upper() or "NOW()" in sql or "now()" in sql
        assert 1 in params or "1" in str(params)

    def test_clear_tombstone_sets_null(self):
        backend = _make_backend()

        backend.clear_tombstone(document_id=7)

        sql = backend._execute.call_args[0][0]
        params = backend._execute.call_args[0][1]
        assert "UPDATE" in sql.upper()
        assert "documents" in sql.lower()
        assert "tombstoned_at" in sql.lower()
        assert "NULL" in sql.upper() or "null" in sql
        assert 7 in params or "7" in str(params)

    def test_clear_tombstone_does_not_set_timestamp(self):
        backend = _make_backend()

        backend.clear_tombstone(document_id=3)

        sql = backend._execute.call_args[0][0]
        assert "NOW()" not in sql.upper() and "NOW()" not in sql
