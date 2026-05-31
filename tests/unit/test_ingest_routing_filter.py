"""RED tests — `_write_embeddings_for_chunks` filters by routing rule.

PR #81: ingest-time per-flush writes (called every
``_FLUSH_EMBEDDINGS_EVERY_N_FILES`` files plus at end-of-source) must
respect the same routing rule as the backfill path: only embedders that
*claim* a chunk under the route see it.

The internal helper signature changes to accept the list of *active*
embedders (already in scope at the call site via
``_flush_all_pending_embeddings``), so it can resolve the route per chunk.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest


def _mk_runtime_embedder(name: str, *, extensions: list[str] | None = None) -> MagicMock:
    emb = MagicMock()
    emb.name = name
    emb.extensions = list(extensions or [])
    emb.last_failed_indices = []

    def _encode(texts, **_kwargs):
        return [[0.1] * 8 for _ in texts]

    emb.encode.side_effect = _encode
    return emb


def _mk_backend(rows: list[tuple[int, str, str]]) -> MagicMock:
    """Backend whose ``chunks_missing_embedding`` yields the new 3-tuple
    shape on the first call and empty thereafter (so the ingest flush
    loop terminates after draining once)."""
    backend = MagicMock()
    calls = {"n": 0}

    def _yielder(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return iter(rows)
        return iter([])

    backend.chunks_missing_embedding.side_effect = _yielder
    return backend


# ──────────────────────────────────────────────────────────────────────────
# _write_embeddings_for_chunks honours active_embedders for routing
# ──────────────────────────────────────────────────────────────────────────


class TestIngestRouting:
    def test_specialist_writes_only_its_extensions(self) -> None:
        from corpus_forge.ingest import _write_embeddings_for_chunks

        text = _mk_runtime_embedder("nomic")  # catchall
        code = _mk_runtime_embedder("nomic-code", extensions=[".py"])
        active_embedders = [text, code]

        backend = _mk_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
                (3, "py 2", "filesystem://a/bar.PY"),
            ]
        )

        n_written = _write_embeddings_for_chunks(
            backend,
            embedder_id=42,
            embedder=code,
            active_embedders=active_embedders,
        )

        # Code embedder only embeds chunks 1 and 3.
        assert n_written == 2
        # encode received only the .py texts.
        passed = list(code.encode.call_args[0][0])
        assert "md text" not in passed
        assert "py text" in passed
        assert "py 2" in passed
        # write_embeddings received only (1, ...) and (3, ...).
        pairs = backend.write_embeddings.call_args[0][1]
        assert {cid for cid, _v in pairs} == {1, 3}

    def test_catchall_skips_specialist_claimed_extensions(self) -> None:
        from corpus_forge.ingest import _write_embeddings_for_chunks

        text = _mk_runtime_embedder("nomic")
        code = _mk_runtime_embedder("nomic-code", extensions=[".py"])
        active_embedders = [text, code]

        backend = _mk_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
                (3, "txt text", "filesystem://a/note.txt"),
            ]
        )

        n_written = _write_embeddings_for_chunks(
            backend,
            embedder_id=7,
            embedder=text,
            active_embedders=active_embedders,
        )

        assert n_written == 2
        passed = list(text.encode.call_args[0][0])
        assert "py text" not in passed
        assert "md text" in passed
        assert "txt text" in passed
        pairs = backend.write_embeddings.call_args[0][1]
        assert {cid for cid, _v in pairs} == {2, 3}

    def test_backcompat_all_catchall_every_chunk_embedded(self) -> None:
        """No specialist in play → every chunk is claimed by every active
        catchall (today's behaviour preserved)."""
        from corpus_forge.ingest import _write_embeddings_for_chunks

        catchall = _mk_runtime_embedder("nomic")
        active_embedders = [catchall]

        backend = _mk_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
                (3, "no-ext", "filesystem://a/README"),
            ]
        )

        n_written = _write_embeddings_for_chunks(
            backend,
            embedder_id=1,
            embedder=catchall,
            active_embedders=active_embedders,
        )
        assert n_written == 3
        pairs = backend.write_embeddings.call_args[0][1]
        assert {cid for cid, _v in pairs} == {1, 2, 3}

    def test_zero_pending_returns_zero_no_writes(self) -> None:
        from corpus_forge.ingest import _write_embeddings_for_chunks

        catchall = _mk_runtime_embedder("nomic")
        backend = _mk_backend([])
        n_written = _write_embeddings_for_chunks(
            backend,
            embedder_id=1,
            embedder=catchall,
            active_embedders=[catchall],
        )
        assert n_written == 0
        backend.write_embeddings.assert_not_called()

    def test_route_filter_doesnt_call_encode_when_nothing_claims(self) -> None:
        """If routing rejects every chunk for this embedder, ``encode``
        must not be called (avoids gratuitous wedge-risk on edge inputs)
        and the function returns 0."""
        from corpus_forge.ingest import _write_embeddings_for_chunks

        text = _mk_runtime_embedder("nomic")  # catchall
        code = _mk_runtime_embedder("nomic-code", extensions=[".py"])
        active_embedders = [text, code]

        backend = _mk_backend(
            [
                (1, "md text", "filesystem://a/foo.md"),
                (2, "txt text", "filesystem://a/note.txt"),
            ]
        )

        n_written = _write_embeddings_for_chunks(
            backend,
            embedder_id=42,
            embedder=code,
            active_embedders=active_embedders,
        )

        assert n_written == 0
        code.encode.assert_not_called()
        backend.write_embeddings.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────
# Pin _flush_all_pending_embeddings threads active_embedders through
# ──────────────────────────────────────────────────────────────────────────


def test_flush_all_pending_embeddings_passes_active_list() -> None:
    """``_flush_all_pending_embeddings`` already iterates ``embedders``;
    it must forward that list to ``_write_embeddings_for_chunks`` as the
    ``active_embedders`` kwarg so the route is computed correctly."""
    from unittest.mock import patch

    from corpus_forge.ingest import _flush_all_pending_embeddings

    text = _mk_runtime_embedder("nomic")
    code = _mk_runtime_embedder("nomic-code", extensions=[".py"])
    backend = MagicMock()
    backend.register_embedder.side_effect = [10, 11]

    seen_active: list[list] = []

    def _stub_writer(_backend, _embedder_id, _embedder, *, active_embedders):
        seen_active.append(list(active_embedders))
        return 0

    with patch(
        "corpus_forge.ingest._write_embeddings_for_chunks",
        side_effect=_stub_writer,
    ):
        _flush_all_pending_embeddings(backend, [text, code])

    # Was called once per embedder; each call received the full active list.
    assert len(seen_active) == 2
    for active in seen_active:
        assert {e.name for e in active} == {"nomic", "nomic-code"}


# Smoke pin — keep the legacy call signature working when no active list is
# passed (back-compat for any caller that hasn't migrated; we want the
# default to be "this embedder is the only one, claims everything").
def test_write_embeddings_default_active_is_single_self_catchall() -> None:
    from corpus_forge.ingest import _write_embeddings_for_chunks

    catchall = _mk_runtime_embedder("solo")
    backend = _mk_backend(
        [
            (1, "a", "filesystem://x/a.md"),
            (2, "b", "filesystem://x/b.py"),
        ]
    )

    n_written = _write_embeddings_for_chunks(
        backend,
        embedder_id=1,
        embedder=catchall,
        # ``active_embedders`` omitted → defaults to ``[embedder]``
        # i.e. this embedder is treated as a sole catchall.
    )
    assert n_written == 2
    pairs = backend.write_embeddings.call_args[0][1]
    assert {cid for cid, _v in pairs} == {1, 2}


# Negative pin — if the test inadvertently passes a non-3-tuple shape,
# the production code should surface a clear error rather than silently
# routing on an empty source_uri.
def test_legacy_2tuple_shape_raises_clear_error() -> None:
    """If a caller (or stale test) still yields 2-tuples, the production
    helper must raise a clear ValueError naming the new shape — not
    silently route everything to the catchall.

    Production code: when unpacking the iterator, expect 3 elements; on
    mismatch, raise with a greppable message that names ``source_uri``.
    """
    from corpus_forge.ingest import _write_embeddings_for_chunks

    catchall = _mk_runtime_embedder("nomic")
    backend = MagicMock()
    backend.chunks_missing_embedding.side_effect = lambda *_a, **_kw: iter(
        [(1, "text-only"), (2, "still text-only")]
    )

    with pytest.raises(ValueError, match="source_uri"):
        _write_embeddings_for_chunks(
            backend,
            embedder_id=1,
            embedder=catchall,
            active_embedders=[catchall],
        )


# Sanity pin — types.SimpleNamespace stand-in for active_embedders also
# works (the helper duck-types on ``.extensions`` / ``.name``).
def test_simplenamespace_active_embedders_supported() -> None:
    from corpus_forge.ingest import _write_embeddings_for_chunks

    catchall_real = _mk_runtime_embedder("real-text")  # used for encode
    code_stub = types.SimpleNamespace(name="code", extensions=[".py"])
    active_embedders = [catchall_real, code_stub]

    backend = _mk_backend(
        [
            (1, "py text", "filesystem://a/foo.py"),
            (2, "md text", "filesystem://a/foo.md"),
        ]
    )

    n_written = _write_embeddings_for_chunks(
        backend,
        embedder_id=1,
        embedder=catchall_real,
        active_embedders=active_embedders,
    )

    # Specialist (code_stub) claims chunk 1 → catchall only sees chunk 2.
    assert n_written == 1
    pairs = backend.write_embeddings.call_args[0][1]
    assert {cid for cid, _v in pairs} == {2}
