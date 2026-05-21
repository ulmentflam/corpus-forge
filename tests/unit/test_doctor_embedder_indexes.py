"""Doctor check for per-embedder HNSW index drift (post-halfvec change).

Surfaces the "you upgraded but didn't run `corpus-forge migrate`" state
where an existing ``embeddings_<name>`` table still has its legacy
``vector_cosine_ops`` index even though its row in ``corpus.embedders``
records ``dimension > 2000`` (above pgvector's standard HNSW ceiling).

Status logic:

- ``SKIP`` for SQLite (sqlite-vec has no HNSW index strategy concept).
- ``SKIP`` when the backend isn't reachable (we don't wedge doctor on
  a temporarily-down Postgres).
- ``SKIP`` when no embedders are registered yet (fresh install).
- ``WARN`` when any embedder's index doesn't match its configured dim;
  message tells the user to run ``corpus-forge migrate`` or
  ``corpus-forge embedder repair-indexes --apply``.
- ``OK``  when every embedder's HNSW index matches the strategy
  ``_dense_index_strategy`` would produce for its dim.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.doctor.checks import CheckStatus, _check_embedder_indexes


def _cfg(kind: str = "postgres"):
    cfg = MagicMock()
    cfg.backend.kind = kind
    cfg.backend.dsn = "postgresql://x:y@localhost/z"
    cfg.backend.schema = "corpus"
    return cfg


def test_sqlite_backend_skips_immediately() -> None:
    result = _check_embedder_indexes(_cfg("sqlite"))
    assert result.status == CheckStatus.SKIP
    assert "sqlite" in result.detail.lower()


def test_postgres_backend_unreachable_yields_skip() -> None:
    """Doctor must not wedge if the backend is temporarily down."""

    with patch(
        "corpus_forge.backends.postgres.PostgresBackend",
        side_effect=RuntimeError("connection refused"),
    ):
        result = _check_embedder_indexes(_cfg())
    assert result.status == CheckStatus.SKIP
    assert "unreachable" in result.detail


def test_returns_skip_when_no_embedders_registered() -> None:
    """Pre-ingest state: corpus.embedders is empty → SKIP, not WARN."""

    fake_backend = MagicMock()
    with (
        patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            return_value=fake_backend,
        ),
        patch(
            "corpus_forge.admin.embedder.audit_embedder_indexes",
            return_value=[],
        ),
    ):
        result = _check_embedder_indexes(_cfg())
    assert result.status == CheckStatus.SKIP
    assert "no embedders" in result.detail.lower()


def test_ok_when_every_embedder_matches() -> None:
    from corpus_forge.admin.embedder import IndexAuditRow

    rows = [
        IndexAuditRow(
            name="small",
            dimension=1024,
            table_name="embeddings_small",
            current_indexdef="CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)",
            target_indexdef="CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)",
            status="OK",
        ),
        IndexAuditRow(
            name="qwen3_4096",
            dimension=4096,
            table_name="embeddings_qwen3_4096",
            current_indexdef=(
                "CREATE INDEX ... USING hnsw ((embedding::halfvec(4000)) halfvec_cosine_ops)"
            ),
            target_indexdef=(
                "CREATE INDEX ... USING hnsw ((embedding::halfvec(4000)) halfvec_cosine_ops)"
            ),
            status="OK",
        ),
    ]
    with (
        patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            return_value=MagicMock(),
        ),
        patch("corpus_forge.admin.embedder.audit_embedder_indexes", return_value=rows),
    ):
        result = _check_embedder_indexes(_cfg())
    assert result.status == CheckStatus.OK
    assert "small(1024d)" in result.detail
    assert "qwen3_4096(4096d)" in result.detail


def test_warn_when_dim_exceeds_2000_with_legacy_vector_ops_index() -> None:
    """The exact "upgraded but didn't migrate" failure mode the
    halfvec change exists to surface."""
    from corpus_forge.admin.embedder import IndexAuditRow

    rows = [
        IndexAuditRow(
            name="qwen3_4096",
            dimension=4096,
            table_name="embeddings_qwen3_4096",
            current_indexdef="CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)",
            target_indexdef=(
                "CREATE INDEX ... USING hnsw ((embedding::halfvec(4000)) halfvec_cosine_ops)"
            ),
            status="DRIFT",
        ),
    ]
    with (
        patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            return_value=MagicMock(),
        ),
        patch("corpus_forge.admin.embedder.audit_embedder_indexes", return_value=rows),
    ):
        result = _check_embedder_indexes(_cfg())
    assert result.status == CheckStatus.WARN
    # Must tell the user EXACTLY which CLI verbs fix this — otherwise
    # the WARN is just noise.
    assert "corpus-forge migrate" in result.detail
    assert "repair-indexes" in result.detail
    assert "qwen3_4096(4096d) = DRIFT" in result.detail


def test_warn_when_index_is_missing_entirely() -> None:
    """MISSING (table exists, no HNSW index) is the partial-failure
    state where a previous CREATE INDEX crashed mid-flight."""
    from corpus_forge.admin.embedder import IndexAuditRow

    rows = [
        IndexAuditRow(
            name="halfvec_unindexed",
            dimension=4096,
            table_name="embeddings_halfvec_unindexed",
            current_indexdef=None,
            target_indexdef=(
                "CREATE INDEX ... USING hnsw ((embedding::halfvec(4000)) halfvec_cosine_ops)"
            ),
            status="MISSING",
        ),
    ]
    with (
        patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            return_value=MagicMock(),
        ),
        patch("corpus_forge.admin.embedder.audit_embedder_indexes", return_value=rows),
    ):
        result = _check_embedder_indexes(_cfg())
    assert result.status == CheckStatus.WARN
    assert "MISSING" in result.detail


def test_audit_helper_failure_yields_skip_not_fail() -> None:
    """If ``audit_embedder_indexes`` itself blows up (e.g. the
    ``corpus.embedders`` table doesn't exist yet because the user
    hasn't run ``migrate``), surface that as SKIP — not as FAIL —
    so doctor remains actionable even mid-setup.
    """
    with (
        patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            return_value=MagicMock(),
        ),
        patch(
            "corpus_forge.admin.embedder.audit_embedder_indexes",
            side_effect=RuntimeError("relation corpus.embedders does not exist"),
        ),
    ):
        result = _check_embedder_indexes(_cfg())
    assert result.status == CheckStatus.SKIP
    assert "pre-migrate" in result.detail
