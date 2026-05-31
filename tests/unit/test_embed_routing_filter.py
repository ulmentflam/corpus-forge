"""RED tests — `backfill_embedder` filters pending chunks by routing rule.

PR #81: when multiple active embedders exist, `corpus-forge embed -e <name>`
must only embed chunks that the routing rule (extension match → first
specialist; fallback → first catchall) assigns to `<name>`. Backwards-
compat: when no embedder declares ``extensions``, every embedder still sees
every chunk.

The backend's ``chunks_missing_embedding`` is updated (in T6) to yield
``(chunk_id, text, source_uri)`` 3-tuples; these tests pin that shape AND
the filter that the backfill applies on top of it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.config import Config
from corpus_forge.embed import backfill_embedder


def _mk_embedder_config(
    name: str,
    *,
    extensions: list[str] | None = None,
    active: bool = True,
) -> MagicMock:
    cfg = MagicMock()
    cfg.name = name
    cfg.provider = "sentence_transformers"
    cfg.model_id = f"model-{name}"
    cfg.dimension = 384
    cfg.normalize = True
    cfg.distance = "cosine"
    cfg.active = active
    cfg.batch_size = 32
    cfg.device = "auto"
    cfg.api_key_env = "OPENAI_API_KEY"
    cfg.extensions = list(extensions or [])
    return cfg


def _mk_runtime_embedder(name: str, *, extensions: list[str] | None = None) -> MagicMock:
    emb = MagicMock()
    emb.name = name
    emb.extensions = list(extensions or [])
    emb.last_failed_indices = []

    # Mirror `encode` behaviour — return one vector per input.
    def _encode(texts, **_kwargs):
        return [[0.1] * 384 for _ in texts]

    emb.encode.side_effect = _encode
    return emb


def _mk_mock_backend(
    rows: list[tuple[int, str, str]],
) -> MagicMock:
    """Backend whose ``chunks_missing_embedding`` yields the new 3-tuple
    shape (chunk_id, text, source_uri) once, then empty on subsequent
    calls (simulating "all pending rows fetched on first iter; nothing
    new on the second")."""
    backend = MagicMock()
    backend.register_embedder.return_value = 1
    backend.count_chunks_missing_embedding.return_value = len(rows)

    calls = {"n": 0}

    def _yielder(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return iter(rows)
        return iter([])

    backend.chunks_missing_embedding.side_effect = _yielder
    return backend


# ──────────────────────────────────────────────────────────────────────────
# Routing filter — backfill_embedder respects the rule
# ──────────────────────────────────────────────────────────────────────────


def _name_dispatched_register(runtime_by_name: dict[str, MagicMock]):
    """Build a ``register_from_config`` side_effect that returns the
    runtime embedder matching ``embedder_config.name``.  Lets tests pin
    multiple distinct embedders into the active list (text catchall +
    code specialist), which the real registry would otherwise do.
    """

    def _side_effect(_registry, embedder_config):
        try:
            return runtime_by_name[embedder_config.name]
        except KeyError as exc:
            raise AssertionError(
                f"test stub: no runtime embedder for {embedder_config.name!r}; "
                f"declared runtime names: {list(runtime_by_name)!r}"
            ) from exc

    return _side_effect


class TestBackfillRoutesByExtension:
    def test_specialist_only_sees_its_extensions(self) -> None:
        """A code specialist's backfill must not embed `.md` chunks."""
        text_cfg = _mk_embedder_config("nomic")  # catchall
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])

        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        backend = _mk_mock_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
                (3, "py 2", "filesystem://a/bar.PY"),
            ]
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic-code")

        # encode should be called with only the .py chunk texts.
        encode_calls = runtime_code.encode.call_args_list
        assert encode_calls, "embedder.encode was never called"
        first_texts = encode_calls[0][0][0]
        # tuples-to-list defensively, since the prod code may pass either
        passed_texts = list(first_texts)
        assert "md text" not in passed_texts
        assert "py text" in passed_texts
        assert "py 2" in passed_texts

        # write_embeddings should only carry pairs for chunk_ids 1, 3 — NOT 2.
        assert backend.write_embeddings.called
        write_pairs = backend.write_embeddings.call_args_list[0][0][1]
        written_chunk_ids = {cid for cid, _vec in write_pairs}
        assert written_chunk_ids == {1, 3}

    def test_catchall_only_sees_unclaimed_extensions(self) -> None:
        """When a specialist claims `.py`, the catchall backfill must not
        re-embed those chunks; it sees only the rest."""
        text_cfg = _mk_embedder_config("nomic")
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])

        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")  # catchall
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        backend = _mk_mock_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
                (3, "txt text", "filesystem://a/note.txt"),
            ]
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic")

        encode_calls = runtime_text.encode.call_args_list
        assert encode_calls
        passed_texts = list(encode_calls[0][0][0])
        assert "py text" not in passed_texts
        assert "md text" in passed_texts
        assert "txt text" in passed_texts

        write_pairs = backend.write_embeddings.call_args_list[0][0][1]
        written_chunk_ids = {cid for cid, _vec in write_pairs}
        assert written_chunk_ids == {2, 3}

    def test_backcompat_single_embedder_no_extensions_sees_everything(self) -> None:
        """No embedder declares extensions → no routing → catchall sees
        every chunk (today's behaviour preserved)."""
        text_cfg = _mk_embedder_config("nomic")  # only embedder, catchall
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg]

        runtime_text = _mk_runtime_embedder("nomic")

        backend = _mk_mock_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
                (3, "no-ext", "filesystem://a/README"),
            ]
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                return_value=runtime_text,
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic")

        write_pairs = backend.write_embeddings.call_args_list[0][0][1]
        written_chunk_ids = {cid for cid, _vec in write_pairs}
        assert written_chunk_ids == {1, 2, 3}

    def test_no_match_no_catchall_skips_chunk(self) -> None:
        """When only a specialist is active (no catchall), a chunk whose
        extension doesn't match the specialist is silently skipped — not
        written, not crashed. The Config-level invariant would normally
        catch this at config-load time; the backfill itself must defend
        in case tests / programmatic callers bypass the Config gate."""
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [code_cfg]

        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        backend = _mk_mock_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (2, "md text", "filesystem://a/foo.md"),
            ]
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                return_value=runtime_code,
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic-code")

        write_pairs = backend.write_embeddings.call_args_list[0][0][1]
        written_chunk_ids = {cid for cid, _vec in write_pairs}
        assert written_chunk_ids == {1}


# ──────────────────────────────────────────────────────────────────────────
# Post-PR #81 bugfix — SQL-side `extensions=` push + paging fix
# ──────────────────────────────────────────────────────────────────────────
#
# Three things to pin (the symptom + the fix):
#
# 1. ``backfill_embedder`` MUST pass ``extensions=embedder.extensions or None``
#    to BOTH ``chunks_missing_embedding`` and ``count_chunks_missing_embedding``.
#    Otherwise the backend can't filter, the count over-reports, and the
#    progress bar lies.
#
# 2. When the embedder has no extensions (catchall), the backend calls must
#    receive ``extensions=None`` — not ``[]`` — so the SQL fast-path stays
#    unchanged and back-compat tests still pass.
#
# 3. The original bug — ``if not chunks_needing: break`` was wrong because
#    paging is non-cursored. Now that the SQL filter is in place, the
#    in-memory ``route_for`` filter is defense-in-depth; an empty Python-
#    filter result on one page must ``continue`` (skip to next page), not
#    ``break``. Only ``raw_rows == []`` is a real end-of-stream signal.


class TestBackfillPassesExtensionsKwarg:
    """T2 — embed.backfill_embedder threads ``extensions=`` through to the
    backend. Without this, the bug is unfixed even if the backend implements
    the filter correctly.
    """

    def test_specialist_passes_its_extensions_kwarg(self) -> None:
        """Specialist embedder → both ``chunks_missing_embedding`` and
        ``count_chunks_missing_embedding`` get called with
        ``extensions=[".py"]``."""
        text_cfg = _mk_embedder_config("nomic")
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])
        backend = _mk_mock_backend(
            [(1, "py text", "filesystem://a/foo.py")],
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic-code")

        # chunks_missing_embedding was called with extensions=[".py"]
        cme_calls = backend.chunks_missing_embedding.call_args_list
        assert cme_calls, "chunks_missing_embedding was never called"
        first_kwargs = cme_calls[0][1]
        assert "extensions" in first_kwargs, (
            f"backfill must pass extensions= to chunks_missing_embedding; "
            f"got kwargs={first_kwargs!r}"
        )
        assert first_kwargs["extensions"] == [".py"], (
            f"specialist's extensions must reach the backend verbatim; got "
            f"{first_kwargs['extensions']!r}"
        )

        # count_chunks_missing_embedding was called with extensions=[".py"]
        cce_calls = backend.count_chunks_missing_embedding.call_args_list
        assert cce_calls, "count_chunks_missing_embedding was never called"
        cce_kwargs = cce_calls[0][1]
        assert cce_kwargs.get("extensions") == [".py"], (
            f"count_chunks_missing_embedding must receive the same extensions; got {cce_kwargs!r}"
        )

    def test_catchall_passes_none_not_empty_list(self) -> None:
        """A catchall embedder (no ``extensions``) → backend receives
        ``extensions=None``, NOT ``[]``. The backend treats both the same,
        but keeping ``None`` on the wire makes back-compat reasoning obvious
        and matches the documented contract."""
        text_cfg = _mk_embedder_config("nomic")  # catchall — no extensions
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg]

        runtime_text = _mk_runtime_embedder("nomic")  # extensions=[]
        backend = _mk_mock_backend(
            [(1, "md text", "filesystem://a/foo.md")],
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                return_value=runtime_text,
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic")

        cme_calls = backend.chunks_missing_embedding.call_args_list
        assert cme_calls
        cme_kwargs = cme_calls[0][1]
        assert cme_kwargs.get("extensions") is None, (
            f"catchall (empty extensions) must send extensions=None to backend; got {cme_kwargs!r}"
        )

        cce_kwargs = backend.count_chunks_missing_embedding.call_args_list[0][1]
        assert cce_kwargs.get("extensions") is None, (
            f"count must also receive extensions=None for catchall; got {cce_kwargs!r}"
        )


class TestBackfillRouteForIsNoopWhenSqlFilterWorks:
    """T2 — when the backend's SQL filter does its job, the in-memory
    ``route_for`` filter is a no-op. This pins that defense-in-depth
    didn't accidentally re-filter the page (which would be a perf/cost
    regression even if behaviour stays correct)."""

    def test_no_chunks_dropped_by_in_memory_filter_when_sql_filter_is_correct(self) -> None:
        text_cfg = _mk_embedder_config("nomic")
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        # Simulate a correctly-filtering backend: only .py rows come back.
        backend = _mk_mock_backend(
            [
                (1, "py text", "filesystem://a/foo.py"),
                (3, "py 2", "filesystem://a/bar.PY"),
            ]
        )

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic-code")

        # Every fetched chunk should have been written — route_for filtered nothing.
        write_pairs = backend.write_embeddings.call_args_list[0][0][1]
        assert len(write_pairs) == 2, (
            f"in-memory route_for filter dropped chunks the SQL filter already "
            f"approved; expected 2 written pairs, got {len(write_pairs)}"
        )


class TestBackfillContinuesWhenPageEmptiesAfterInMemoryFilter:
    """T2 + regression for the bug — the original code had
    ``if not chunks_needing: break`` which gave up the entire backfill
    when the first 1000-row page happened to contain zero matches.

    The fix turns that into ``continue`` (skip this page, fetch the next)
    so a backend that doesn't (yet) implement the SQL filter still
    eventually drains the corpus. Only ``raw_rows == []`` from the backend
    is a real end-of-stream signal that should break the loop.

    We simulate a "broken" backend that ignores ``extensions=`` and returns
    a page of all-non-matching rows on iter 1, matching rows on iter 2,
    then empty on iter 3. The backfill MUST call
    ``chunks_missing_embedding`` at least 3 times to prove it didn't
    early-break.
    """

    def test_in_memory_filter_empty_page_does_not_break_backfill(self) -> None:
        text_cfg = _mk_embedder_config("nomic")
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        # "Broken" backend: ignores extensions=. Returns:
        #   iter 1 → all .md rows  (in-memory filter drops everything)
        #   iter 2 → all .py rows  (in-memory filter keeps them)
        #   iter 3 → []            (real end-of-stream)
        backend = MagicMock()
        backend.register_embedder.return_value = 1
        # Count is also wrong (it's the unfiltered total), which is fine —
        # this test is about the *paging* break bug, not the count.
        backend.count_chunks_missing_embedding.return_value = 4

        calls = {"n": 0}

        def _yielder(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return iter(
                    [
                        (10, "md 1", "filesystem://a/a.md"),
                        (11, "md 2", "filesystem://a/b.md"),
                    ]
                )
            if calls["n"] == 2:
                return iter(
                    [
                        (20, "py 1", "filesystem://a/x.py"),
                        (21, "py 2", "filesystem://a/y.py"),
                    ]
                )
            return iter([])

        backend.chunks_missing_embedding.side_effect = _yielder

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic-code")

        # The bug: iter 1's in-memory filter empties → old code would break,
        # calls["n"] would stop at 1, .py rows on iter 2 would never embed.
        # The fix: iter 1 is "continue", iter 2 actually writes, iter 3 is the
        # real break. So chunks_missing_embedding must have been called >= 3 times.
        assert calls["n"] >= 3, (
            f"backfill broke out of the loop after the first empty in-memory page — "
            f"the very bug this PR fixes. Expected >= 3 fetches; got {calls['n']}"
        )

        # And the .py chunks DID get embedded (iter 2).
        assert backend.write_embeddings.called, (
            "write_embeddings was never called — .py rows on page 2 never embedded"
        )
        all_written_ids: set[int] = set()
        for call in backend.write_embeddings.call_args_list:
            for cid, _vec in call[0][1]:
                all_written_ids.add(cid)
        assert {20, 21}.issubset(all_written_ids), (
            f".py chunks on page 2 must be embedded; got written ids {all_written_ids}"
        )

    def test_empty_raw_rows_still_breaks_the_loop(self) -> None:
        """Sanity check: when the backend genuinely returns no rows, the loop
        DOES exit — we're not introducing an infinite loop."""
        text_cfg = _mk_embedder_config("nomic")
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg]

        runtime_text = _mk_runtime_embedder("nomic")

        backend = MagicMock()
        backend.register_embedder.return_value = 1
        backend.count_chunks_missing_embedding.return_value = 0
        backend.chunks_missing_embedding.return_value = iter([])

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                return_value=runtime_text,
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            backfill_embedder("nomic")  # must return without hanging

        assert not backend.write_embeddings.called, (
            "write_embeddings should not be called when there are no chunks to embed"
        )

    def test_catchall_advances_past_specialist_owned_pages_without_aborting(self) -> None:
        """Catchall backfill must not be aborted by all-skip pages.

        Scenario the previous abort guard mis-handled: a catchall embedder
        (``extensions=None``) backfills while many pending chunks are
        specialist-owned (route_for sends them to the specialist). With
        the SQL ``after_id`` cursor in place, each empty-after-routing
        page advances ``last_seen_id`` and the loop terminates naturally
        when the backend returns ``[]`` — without raising RuntimeError.
        """
        text_cfg = _mk_embedder_config("nomic")
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        backend = MagicMock()
        backend.register_embedder.return_value = 1
        backend.count_chunks_missing_embedding.return_value = 30

        # Simulate a real backend that honors ``after_id``: 3 pages of
        # all-.py rows the catchall must skip, then the cursor walks past
        # the end and the next fetch returns []. Without the cursor, the
        # first page would come back on every iter and the old abort
        # guard would have fired.
        pages = [
            [(1, "a", "filesystem://x/a.py"), (2, "b", "filesystem://x/b.py")],
            [(3, "c", "filesystem://x/c.py"), (4, "d", "filesystem://x/d.py")],
            [(5, "e", "filesystem://x/e.py"), (6, "f", "filesystem://x/f.py")],
        ]

        def _yielder(*_a, after_id=None, **_kw):
            cutoff = after_id or 0
            for p in pages:
                if p[-1][0] > cutoff:
                    return iter(p)
            return iter([])

        backend.chunks_missing_embedding.side_effect = _yielder

        with (
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            # Must return cleanly — no RuntimeError despite 3 all-skip pages.
            backfill_embedder("nomic")

        assert backend.chunks_missing_embedding.call_count == 4, (
            "Expected 3 paged fetches + 1 terminal empty fetch; got "
            f"{backend.chunks_missing_embedding.call_count}"
        )
        # The catchall never owned any of these chunks → no writes.
        assert not backend.write_embeddings.called

    def test_consecutive_empty_pages_abort_with_clear_error(self) -> None:
        """Hostile backend: ignores ``extensions=`` AND returns the same
        non-matching page forever. Without a guard, the ``continue`` branch
        would spin indefinitely. Verify the loop aborts with a clear
        ``RuntimeError`` after the empty-page streak threshold."""
        import pytest

        text_cfg = _mk_embedder_config("nomic")
        code_cfg = _mk_embedder_config("nomic-code", extensions=[".py"])
        mock_config = MagicMock()
        mock_config.backend.kind = "postgres"
        mock_config.backend.dsn = "postgresql://x"
        mock_config.backend.schema = "corpus"
        mock_config.embedders = [text_cfg, code_cfg]

        runtime_text = _mk_runtime_embedder("nomic")
        runtime_code = _mk_runtime_embedder("nomic-code", extensions=[".py"])

        # Broken backend: returns the same all-.md page every call, no
        # forward progress. Without the abort guard, this is an infinite loop.
        backend = MagicMock()
        backend.register_embedder.return_value = 1
        backend.count_chunks_missing_embedding.return_value = 2
        backend.chunks_missing_embedding.side_effect = lambda *_a, **_kw: iter(
            [
                (10, "md 1", "filesystem://a/a.md"),
                (11, "md 2", "filesystem://a/b.md"),
            ]
        )

        with (  # noqa: SIM117
            patch.object(Config, "load", return_value=mock_config),
            patch(
                "corpus_forge.embed.register_from_config",
                side_effect=_name_dispatched_register(
                    {"nomic": runtime_text, "nomic-code": runtime_code}
                ),
            ),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        ):
            with pytest.raises(RuntimeError, match=r"consecutive pages had zero matches"):
                backfill_embedder("nomic-code")

        # Must have called the backend enough times to trip the streak guard
        # but NOT spun indefinitely.
        assert 10 <= backend.chunks_missing_embedding.call_count <= 20, (
            f"Expected loop to abort after ~10 empty pages; got "
            f"{backend.chunks_missing_embedding.call_count} calls"
        )
        assert not backend.write_embeddings.called, (
            "No matching rows ever appeared; write_embeddings must not be called"
        )
