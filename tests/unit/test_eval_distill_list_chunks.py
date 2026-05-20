"""Unit tests for ``corpus_forge.eval.distill._list_chunks_for_dataset``.

Exercises both the SQLiteBackend and Postgres-shaped branches without
requiring Docker — uses fake backend classes whose `_execute` returns
canned rows.
"""

from __future__ import annotations

from corpus_forge.eval.distill import _list_chunks_for_dataset


class _FakeSQLiteBackend:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def _execute(self, sql: str, params: tuple) -> list[dict]:
        self.calls.append((sql, params))
        return self._rows


class _FakePostgresBackend:
    schema = "corpus"

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def _execute(self, sql: str, params: tuple) -> list[dict]:
        self.calls.append((sql, params))
        return self._rows


def test_list_chunks_for_dataset_sqlite_uses_question_marks() -> None:
    backend = _FakeSQLiteBackend([{"text": "a"}, {"text": "b"}])
    rows = _list_chunks_for_dataset(backend, dataset_id=42)
    assert rows == [{"text": "a"}, {"text": "b"}]
    assert backend.calls[0][1] == (42,)
    assert "?" in backend.calls[0][0]


def test_list_chunks_for_dataset_postgres_uses_schema_prefix_and_pct_s() -> None:
    backend = _FakePostgresBackend([{"text": "x"}])
    rows = _list_chunks_for_dataset(backend, dataset_id=7)
    assert rows == [{"text": "x"}]
    sql = backend.calls[0][0]
    # Schema prefix appears.
    assert "corpus.chunks" in sql
    assert "corpus.documents" in sql
    # Postgres placeholder style.
    assert "%s" in sql


def test_list_chunks_for_dataset_postgres_custom_schema() -> None:
    backend = _FakePostgresBackend([])
    backend.schema = "custom_ns"
    rows = _list_chunks_for_dataset(backend, dataset_id=1)
    assert rows == []
    sql = backend.calls[0][0]
    assert "custom_ns.chunks" in sql
    assert "custom_ns.documents" in sql


def test_list_chunks_for_dataset_postgres_no_schema_falls_back_to_corpus() -> None:
    class _PostgresNoSchema:
        # Class name contains 'Postgres' so the dispatch goes down that branch,
        # but no `schema` attribute → fall back to "corpus".
        def __init__(self) -> None:
            self._rows: list[dict] = []
            self.calls: list[tuple[str, tuple]] = []

        def _execute(self, sql: str, params: tuple) -> list[dict]:
            self.calls.append((sql, params))
            return self._rows

    backend = _PostgresNoSchema()
    _list_chunks_for_dataset(backend, dataset_id=1)
    assert "corpus.chunks" in backend.calls[0][0]
