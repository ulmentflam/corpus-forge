"""Unit tests for the per-source cap enforcement module.

RFC ``rfc-corpus-growth-controls`` — fourth-item enforcement. Covers
:func:`corpus_forge.admin.source_caps.derive_source_uri_prefix` and
:func:`corpus_forge.admin.source_caps.enforce_source_caps`.

The fake backend mirrors the shape used by
``tests/unit/test_prune_scorer.py``: a small in-memory store that
responds to ``_execute(SQL, params)`` for both COUNT-style selects
and DELETE statements and additionally ships an
``iter_curation_candidates`` hook so the scoring path doesn't need
to issue any extra SQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from corpus_forge.admin import source_caps as source_caps_mod
from corpus_forge.admin.source_caps import (
    CapEnforcementReport,
    derive_source_uri_prefix,
    enforce_source_caps,
)

# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_STALE = _NOW - timedelta(days=365)


def _row(
    chunk_id: int,
    *,
    text: str = "lorem ipsum dolor sit amet",
    document_id: int | None = 1,
    source_uri: str | None = "vault://notes/note.md",
    classifier_confidence: float | None = 0.8,
    modified_at: datetime | None = _STALE,
    description: str | None = "d",
    heading: str | None = "h",
    document_title: str | None = "title",
    metadata: dict[str, Any] | None = None,
    labels: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a backend row in the shape `_iter_curation_candidates` returns."""

    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "text": text,
        "heading": heading,
        "description": description,
        "metadata": dict(metadata if metadata is not None else {"language": "en"}),
        "document_title": document_title,
        "source_uri": source_uri,
        "modified_at": modified_at,
        "labels": list(labels if labels is not None else [("class", "topic_a")]),
        "classifier_label": "topic_a",
        "classifier_confidence": classifier_confidence,
        "embedding": None,
    }


class _FakeBackend:
    """Backend covering the cap-enforcement surface.

    - ``iter_curation_candidates`` yields the in-memory rows so the
      scorer never has to issue chunk-level SQL.
    - ``_execute`` services the COUNT-style queries from
      :func:`count_source_rows` (matched by SQL fingerprint) plus the
      DELETE bulk-prune fallback.
    - Records every DELETE invocation so tests can assert eviction
      shape without poking the row store.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        is_postgres: bool = True,
    ) -> None:
        self._rows = list(rows)
        # ``_paramstyle = "pyformat"`` is the Postgres-shaped signal
        # `_is_postgres_like` looks at. Toggle off for the SQLite-shaped
        # path.
        if is_postgres:
            self._paramstyle = "pyformat"
        self.delete_calls: list[list[int]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def iter_curation_candidates(
        self, *, dataset: str | None, limit: int
    ) -> Iterable[dict[str, Any]]:
        del dataset, limit  # unused — we always return everything
        yield from self._rows

    def delete_chunks_by_ids(self, chunk_ids: list[int]) -> int:
        self.delete_calls.append(list(chunk_ids))
        # Mutate the in-memory store so subsequent counts reflect the
        # deletion (not strictly required for these tests, but matches
        # what a real backend would do).
        kept = [r for r in self._rows if r["chunk_id"] not in set(chunk_ids)]
        self._rows = kept
        return len(chunk_ids)

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.executed.append((sql, params))

        # Documents COUNT query.
        if "FROM corpus.chunks c JOIN corpus.documents d" in sql or (
            "FROM chunks c JOIN documents d" in sql
        ):
            return self._count(params, table="documents")

        # Conversations COUNT query.
        if "FROM corpus.chunks c JOIN corpus.conversations cv" in sql or (
            "FROM chunks c JOIN conversations cv" in sql
        ):
            return self._count(params, table="conversations")

        # Feedback probes — empty.
        if "chunk_feedback" in sql or "FROM corpus.feedback" in sql:
            return []

        return []

    # The fake aggregator: pretends every row in ``self._rows`` is on
    # the documents path; the conversations path always returns zero.
    def _count(self, params: tuple[Any, ...], *, table: str) -> list[dict[str, Any]]:
        if table == "conversations":
            return [{"cnt": 0, "total_bytes": 0}]
        # params is (dataset_id, pattern); pattern is `prefix + "%"`.
        if len(params) < 2:
            return [{"cnt": 0, "total_bytes": 0}]
        prefix = str(params[1]).rstrip("%")
        matching = [r for r in self._rows if (r.get("source_uri") or "").startswith(prefix)]
        total_bytes = sum(len(r.get("text") or "") for r in matching)
        return [{"cnt": len(matching), "total_bytes": total_bytes}]


# ─────────────────────────────────────────────────────────────────────────
# derive_source_uri_prefix
# ─────────────────────────────────────────────────────────────────────────


def test_derive_prefix_markdown_vault() -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/A/B")
    assert derive_source_uri_prefix(cfg) == "vault://B/"


def test_derive_prefix_markdown_vault_missing_root_returns_none() -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root=None)
    assert derive_source_uri_prefix(cfg) is None


def test_derive_prefix_claude_code() -> None:
    cfg = SimpleNamespace(plugin="claude_code")
    assert derive_source_uri_prefix(cfg) == "claude-code://"


def test_derive_prefix_known_schemes() -> None:
    expected: dict[str, str] = {
        "opencode": "opencode://",
        "gemini_cli": "gemini-cli://",
        "codex_cli": "codex-cli://",
        "chatgpt_export": "chatgpt-export://",
        "jsonl_chat": "jsonl-chat://",
    }
    for plugin, prefix in expected.items():
        cfg = SimpleNamespace(plugin=plugin)
        assert derive_source_uri_prefix(cfg) == prefix, plugin


def test_derive_prefix_zotero_with_user_id() -> None:
    nested = SimpleNamespace(user_id="123456", group_id=None)
    cfg = SimpleNamespace(plugin="zotero", zotero=nested)
    assert derive_source_uri_prefix(cfg) == "zotero://123456/"


def test_derive_prefix_zotero_with_group_id() -> None:
    nested = SimpleNamespace(user_id=None, group_id="999")
    cfg = SimpleNamespace(plugin="zotero", zotero=nested)
    assert derive_source_uri_prefix(cfg) == "zotero://999/"


def test_derive_prefix_zotero_without_library_id_returns_none() -> None:
    nested = SimpleNamespace(user_id=None, group_id=None)
    cfg = SimpleNamespace(plugin="zotero", zotero=nested)
    assert derive_source_uri_prefix(cfg) is None


def test_derive_prefix_filesystem() -> None:
    cfg = SimpleNamespace(plugin="filesystem", root="/var/data/mydata")
    assert derive_source_uri_prefix(cfg) == "filesystem://mydata/"


def test_derive_prefix_unknown_plugin_returns_none() -> None:
    cfg = SimpleNamespace(plugin="weird-plugin")
    assert derive_source_uri_prefix(cfg) is None


def test_derive_prefix_missing_plugin_attribute_returns_none() -> None:
    cfg = SimpleNamespace()
    assert derive_source_uri_prefix(cfg) is None


# ─────────────────────────────────────────────────────────────────────────
# enforce_source_caps — no-op branches
# ─────────────────────────────────────────────────────────────────────────


def test_enforce_no_cap_returns_no_cap_reason() -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/v", max_rows=None, max_bytes=None)
    backend = _FakeBackend([])
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    assert report.reason == "no_cap"
    assert report.rows_evicted == 0
    assert backend.delete_calls == []


def test_enforce_no_prefix_returns_no_prefix_reason() -> None:
    cfg = SimpleNamespace(plugin="weird-plugin", max_rows=10, max_bytes=None)
    backend = _FakeBackend([])
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    assert report.reason == "no_prefix"
    assert report.cap_max_rows == 10
    assert report.source_uri_prefix is None
    assert backend.delete_calls == []


def test_enforce_under_max_rows_is_noop() -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/v", max_rows=10, max_bytes=None)
    rows = [_row(i, source_uri=f"vault://v/{i}.md") for i in range(1, 6)]
    backend = _FakeBackend(rows)
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    assert report.reason == "under_cap"
    assert report.rows_before == 5
    assert report.rows_evicted == 0
    assert backend.delete_calls == []


# ─────────────────────────────────────────────────────────────────────────
# enforce_source_caps — eviction branches
# ─────────────────────────────────────────────────────────────────────────


def test_enforce_over_max_rows_evicts_correct_count() -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/v", max_rows=5, max_bytes=None)
    # Same byte-cost per row; same modified_at + confidence so every
    # chunk scores identically — the cap-trimmer picks any 5.
    rows = [_row(i, source_uri=f"vault://v/{i}.md") for i in range(1, 11)]
    backend = _FakeBackend(rows)
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    assert report.reason == "evicted_max_rows"
    assert report.rows_before == 10
    assert report.rows_evicted == 5
    # One bulk-delete call carrying 5 ids.
    assert len(backend.delete_calls) == 1
    assert len(backend.delete_calls[0]) == 5


def test_enforce_over_max_bytes_evicts() -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/v", max_rows=None, max_bytes=30)
    # Each row has text len == 10; total = 50 > cap of 30. Need to
    # evict 2 rows to bring totals to 30.
    rows = [_row(i, source_uri=f"vault://v/{i}.md", text="x" * 10) for i in range(1, 6)]
    backend = _FakeBackend(rows)
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    assert report.reason == "evicted_max_bytes"
    assert report.bytes_before == 50
    assert report.rows_evicted == 2
    assert report.bytes_evicted == 20


def test_enforce_keeps_highest_scoring_rows() -> None:
    """The pristine chunks survive; the broken ones get evicted."""
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/v", max_rows=2, max_bytes=None)
    pristine = [
        _row(
            10,
            source_uri="vault://v/keep1.md",
            classifier_confidence=1.0,
            modified_at=_NOW,
            description="rich",
            heading="rich",
            document_title="rich",
            metadata={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6},
        ),
        _row(
            11,
            source_uri="vault://v/keep2.md",
            classifier_confidence=1.0,
            modified_at=_NOW,
            description="rich",
            heading="rich",
            document_title="rich",
            metadata={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6},
        ),
    ]
    broken = [
        _row(
            20,
            source_uri="vault://v/drop1.md",
            classifier_confidence=None,
            modified_at=_STALE,
            description=None,
            heading=None,
            document_title=None,
            metadata={},
            labels=[],
        ),
        _row(
            21,
            source_uri="vault://v/drop2.md",
            classifier_confidence=None,
            modified_at=_STALE,
            description=None,
            heading=None,
            document_title=None,
            metadata={},
            labels=[],
        ),
    ]
    backend = _FakeBackend(pristine + broken)
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)

    assert report.reason == "evicted_max_rows"
    assert report.rows_evicted == 2
    deleted_ids = set(backend.delete_calls[0])
    # Both broken rows must be in the deletion list; neither pristine.
    assert {20, 21}.issubset(deleted_ids)
    assert deleted_ids.isdisjoint({10, 11})


def test_enforce_both_caps_evicts_until_both_satisfied() -> None:
    """When both caps are set, eviction runs until the stricter is met."""
    cfg = SimpleNamespace(
        plugin="markdown_vault",
        vault_root="/v",
        max_rows=4,
        max_bytes=20,  # ⌊20/10⌋ = 2 rows max under the byte cap → stricter
    )
    rows = [
        _row(i, source_uri=f"vault://v/{i}.md", text="x" * 10)
        for i in range(1, 6)  # 5 rows of 10 bytes each = 50 bytes total
    ]
    backend = _FakeBackend(rows)
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    # over_rows = 5 > 4 → True, so reason names the row cap (it's the
    # outer branch). But eviction count is driven by the stricter byte
    # cap → need to drop 3 rows to bring bytes from 50 to 20.
    assert report.reason == "evicted_max_rows"
    assert report.rows_evicted == 3
    assert report.bytes_evicted == 30


# ─────────────────────────────────────────────────────────────────────────
# enforce_source_caps — claude_code secondary prefix
# ─────────────────────────────────────────────────────────────────────────


def test_enforce_claude_code_sums_both_schemes() -> None:
    cfg = SimpleNamespace(plugin="claude_code", max_rows=3, max_bytes=None)
    rows = [
        _row(1, source_uri="claude-code://proj/s1"),
        _row(2, source_uri="claude-code://proj/s2"),
        _row(3, source_uri="claude-code-history://sid-a"),
        _row(4, source_uri="claude-code-history://sid-b"),
    ]
    backend = _FakeBackend(rows)
    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    # 4 rows attributed to claude_code (2 per scheme), cap=3 → evict 1.
    assert report.rows_before == 4
    assert report.rows_evicted == 1
    assert report.reason == "evicted_max_rows"


# ─────────────────────────────────────────────────────────────────────────
# CapEnforcementReport is a frozen dataclass
# ─────────────────────────────────────────────────────────────────────────


def test_cap_enforcement_report_is_frozen() -> None:
    report = CapEnforcementReport(
        dataset_id=1,
        source_uri_prefix="vault://v/",
        rows_before=5,
        bytes_before=50,
        rows_evicted=0,
        bytes_evicted=0,
        cap_max_rows=10,
        cap_max_bytes=None,
        reason="under_cap",
    )
    with pytest.raises(AttributeError):
        report.rows_evicted = 99  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────
# Failure isolation — `enforce_source_caps` exceptions don't escape past
# the caller's try/except (verified at the ingest wiring; here we just
# pin that `_evict_lowest_scoring` returns ([], 0) when no scorable
# candidates are found).
# ─────────────────────────────────────────────────────────────────────────


class _DatasetScopedFakeBackend(_FakeBackend):
    """Extension of :class:`_FakeBackend` that scopes candidates by dataset.

    Each row carries a synthetic ``_dataset_id`` field. The fake answers
    the ``SELECT name FROM (corpus.)?datasets WHERE id = ?`` lookup that
    :func:`source_caps._resolve_dataset_name` issues, and its
    ``iter_curation_candidates`` filters by the requested dataset name —
    so a leak from dataset A's candidate pool into dataset B's eviction
    list reproduces the bug under test.

    ``_count`` also scopes to ``dataset_id`` (the first ``_execute``
    parameter), matching the production SQL.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        dataset_id_by_name: dict[str, int],
        is_postgres: bool = True,
    ) -> None:
        super().__init__(rows, is_postgres=is_postgres)
        # name <-> id map; both directions used.
        self._id_by_name = dict(dataset_id_by_name)
        self._name_by_id = {v: k for k, v in dataset_id_by_name.items()}

    def iter_curation_candidates(
        self, *, dataset: str | None, limit: int
    ) -> Iterable[dict[str, Any]]:
        del limit
        if dataset is None:
            yield from self._rows
            return
        wanted = self._id_by_name.get(dataset)
        if wanted is None:
            return
        for r in self._rows:
            if r.get("_dataset_id") == wanted:
                yield r

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.executed.append((sql, params))

        # Dataset name lookup (id -> name) issued by
        # _resolve_dataset_name.
        if "FROM corpus.datasets" in sql or "FROM datasets" in sql:
            if len(params) >= 1:
                ds_id = int(params[0])
                name = self._name_by_id.get(ds_id)
                if name is not None:
                    return [{"name": name}]
            return []

        # Documents / conversations COUNT — scope by dataset_id too so
        # the over-cap detection is dataset-correct.
        if "FROM corpus.chunks c JOIN corpus.documents d" in sql or (
            "FROM chunks c JOIN documents d" in sql
        ):
            return self._scoped_count(params, table="documents")
        if "FROM corpus.chunks c JOIN corpus.conversations cv" in sql or (
            "FROM chunks c JOIN conversations cv" in sql
        ):
            return self._scoped_count(params, table="conversations")
        if "chunk_feedback" in sql or "FROM corpus.feedback" in sql:
            return []
        return []

    def _scoped_count(self, params: tuple[Any, ...], *, table: str) -> list[dict[str, Any]]:
        if table == "conversations":
            return [{"cnt": 0, "total_bytes": 0}]
        if len(params) < 2:
            return [{"cnt": 0, "total_bytes": 0}]
        ds_id = int(params[0])
        prefix = str(params[1]).rstrip("%")
        matching = [
            r
            for r in self._rows
            if r.get("_dataset_id") == ds_id and (r.get("source_uri") or "").startswith(prefix)
        ]
        total_bytes = sum(len(r.get("text") or "") for r in matching)
        return [{"cnt": len(matching), "total_bytes": total_bytes}]


def test_evict_only_touches_input_dataset() -> None:
    """Cross-dataset URI collision must not delete from the wrong dataset.

    Two datasets, both with a ``claude_code`` source (URI scheme
    ``claude-code://``). ``max_rows=1`` on dataset 1; inject 5 chunks
    under each dataset, all matching the shared prefix. After running
    :func:`enforce_source_caps` for dataset 1, every evicted chunk
    must belong to dataset 1.

    Regression for the bug where ``_iter_curation_candidates`` was
    called with ``dataset=None`` and Python-side prefix filtering would
    let cross-dataset matches leak into the eviction list.
    """

    cfg = SimpleNamespace(plugin="claude_code", max_rows=1, max_bytes=None)
    ds_a_rows = [
        {**_row(100 + i, source_uri=f"claude-code://A/sess{i}"), "_dataset_id": 1} for i in range(5)
    ]
    ds_b_rows = [
        {**_row(200 + i, source_uri=f"claude-code://B/sess{i}"), "_dataset_id": 2} for i in range(5)
    ]
    backend = _DatasetScopedFakeBackend(
        ds_a_rows + ds_b_rows,
        dataset_id_by_name={"alpha": 1, "beta": 2},
    )

    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)

    # Dataset 1 sees 5 rows attributed → cap=1 → evict 4. Dataset 2's
    # 5 rows must NOT be counted or touched.
    assert report.rows_before == 5
    assert report.rows_evicted == 4
    assert report.reason == "evicted_max_rows"

    # Every deleted chunk_id must belong to dataset 1 (ids 100-104).
    assert len(backend.delete_calls) == 1
    deleted = set(backend.delete_calls[0])
    ds_a_ids = {100, 101, 102, 103, 104}
    ds_b_ids = {200, 201, 202, 203, 204}
    assert deleted.issubset(ds_a_ids), (
        f"eviction leaked across datasets: {deleted - ds_a_ids} belong to dataset 2"
    )
    assert deleted.isdisjoint(ds_b_ids)


def test_evict_returns_empty_when_no_candidates_match_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(plugin="markdown_vault", vault_root="/v", max_rows=1, max_bytes=None)
    # Backend reports 5 rows under the prefix via `_execute`, but the
    # candidate iterator yields rows for a DIFFERENT prefix —
    # simulates a brief schema/URI mismatch where attribution can't
    # find anything to score.
    rows_other = [_row(i, source_uri=f"different-scheme://x/{i}") for i in range(1, 6)]
    backend = _FakeBackend(rows_other)

    # Override `_count` to claim 5 rows under the vault prefix even
    # though our iterable returns none matching it.
    def fake_execute(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        backend.executed.append((sql, params))
        if "documents d" in sql:
            return [{"cnt": 5, "total_bytes": 50}]
        if "conversations cv" in sql:
            return [{"cnt": 0, "total_bytes": 0}]
        return []

    monkeypatch.setattr(backend, "_execute", fake_execute)
    monkeypatch.setattr(source_caps_mod, "_minhash_available", lambda: False)

    report = enforce_source_caps(backend, dataset_id=1, source_config=cfg)
    # Over cap (5 > 1), but no scorable candidates → no rows evicted.
    assert report.rows_before == 5
    assert report.rows_evicted == 0
    assert backend.delete_calls == []
