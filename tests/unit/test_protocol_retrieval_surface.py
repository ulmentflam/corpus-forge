"""R1-01 — pin the new ``StorageBackend`` protocol surface for retrieval.

Five methods land on the Protocol in Phase R1:

- ``search_dense(embedder_id, query_vector, *, k, dataset_id=None) -> list[Hit]``
- ``search_lexical(query, *, k, dataset_id=None) -> list[Hit]``
- ``get_chunk(chunk_id) -> dict | None``
- ``list_datasets() -> list[dict]``
- ``backfill_lexical_index() -> int``

These tests pin signatures via ``inspect.signature``. The Protocol uses
``TYPE_CHECKING`` for the ``Hit`` import so we do **not** assert that
``Hit`` is reachable at import time from ``backends.base`` at runtime —
that would force a circular import.

The dual concrete backends (SQLite, Postgres) are tested separately for
runtime behaviour; this file pins the type-level surface only.
"""

from __future__ import annotations

import inspect
import typing

import pytest

PROTOCOL_METHODS = (
    "search_dense",
    "search_lexical",
    "get_chunk",
    "list_datasets",
    "backfill_lexical_index",
)


# ── Protocol surface ─────────────────────────────────────────────────────


class TestProtocolHasMethods:
    """The five retrieval methods exist on ``StorageBackend``."""

    @pytest.fixture(scope="class")
    def proto(self):
        from corpus_forge.backends.base import StorageBackend

        return StorageBackend

    @pytest.mark.parametrize("name", PROTOCOL_METHODS)
    def test_method_present(self, proto, name):
        assert hasattr(proto, name), f"StorageBackend missing method {name!r}"


class TestSignatures:
    """Argument names + keyword-only flags pinned via inspect.signature."""

    @pytest.fixture(scope="class")
    def proto(self):
        from corpus_forge.backends.base import StorageBackend

        return StorageBackend

    def test_search_dense_signature(self, proto):
        sig = inspect.signature(proto.search_dense)
        params = list(sig.parameters.values())
        # self + embedder_id + query_vector + kw-only k + kw-only dataset_id=None
        names = [p.name for p in params]
        assert names == ["self", "embedder_id", "query_vector", "k", "dataset_id"], (
            f"search_dense params: {names}"
        )
        assert params[3].kind == inspect.Parameter.KEYWORD_ONLY
        assert params[4].kind == inspect.Parameter.KEYWORD_ONLY
        assert params[4].default is None

    def test_search_lexical_signature(self, proto):
        sig = inspect.signature(proto.search_lexical)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self", "query", "k", "dataset_id"], f"search_lexical params: {names}"
        assert params[2].kind == inspect.Parameter.KEYWORD_ONLY
        assert params[3].kind == inspect.Parameter.KEYWORD_ONLY
        assert params[3].default is None

    def test_get_chunk_signature(self, proto):
        sig = inspect.signature(proto.get_chunk)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self", "chunk_id"]

    def test_list_datasets_signature(self, proto):
        sig = inspect.signature(proto.list_datasets)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self"], f"list_datasets params: {names}"

    def test_backfill_lexical_index_signature(self, proto):
        sig = inspect.signature(proto.backfill_lexical_index)
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["self"], f"backfill_lexical_index params: {names}"


class TestReturnAnnotations:
    """Pin return-type annotations (string-form OK)."""

    @pytest.fixture(scope="class")
    def proto(self):
        from corpus_forge.backends.base import StorageBackend

        return StorageBackend

    def _ret_text(self, fn) -> str:
        ann = inspect.signature(fn).return_annotation
        if ann is inspect.Signature.empty:
            return ""
        if isinstance(ann, str):
            return ann
        try:
            return typing.get_type_hints(fn, include_extras=False).get("return").__name__  # type: ignore[union-attr]
        except Exception:
            return repr(ann)

    def test_search_dense_returns_list_hit(self, proto):
        text = self._ret_text(proto.search_dense)
        assert "list" in text and "Hit" in text, f"search_dense -> {text!r}"

    def test_search_lexical_returns_list_hit(self, proto):
        text = self._ret_text(proto.search_lexical)
        assert "list" in text and "Hit" in text, f"search_lexical -> {text!r}"

    def test_get_chunk_returns_optional_dict(self, proto):
        text = self._ret_text(proto.get_chunk)
        assert "dict" in text and "None" in text, f"get_chunk -> {text!r}"

    def test_list_datasets_returns_list_dict(self, proto):
        text = self._ret_text(proto.list_datasets)
        assert "list" in text and "dict" in text, f"list_datasets -> {text!r}"

    def test_backfill_lexical_returns_int(self, proto):
        text = self._ret_text(proto.backfill_lexical_index)
        assert text == "int", f"backfill_lexical_index -> {text!r}"


class TestNoRuntimeHitImportFromBackendsBase:
    """The protocol module must NOT import ``Hit`` at runtime (TYPE_CHECKING only).

    If ``Hit`` were imported eagerly, importing ``corpus_forge.backends.base``
    would force ``corpus_forge.retrieval.types`` to load, which is fine in
    isolation but creates a cycle once R2 retrievers import the backend
    protocol. Keep the runtime surface clean.
    """

    def test_no_top_level_hit_in_module_dict(self):
        import corpus_forge.backends.base as base_mod

        # Hit must NOT be a top-level attribute of the module
        assert not hasattr(base_mod, "Hit"), (
            "corpus_forge.backends.base must import Hit under TYPE_CHECKING only; "
            "found top-level Hit — fix the import."
        )
