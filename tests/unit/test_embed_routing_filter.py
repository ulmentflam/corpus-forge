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
