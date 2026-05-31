"""Embed backfill utility for corpus-forge.

Phase G (G-15): adds an image-embedding code path. ``main(image=True)``
walks chunks bearing the ``format=image`` label, fetches the image
bytes via the chunk metadata (``image_uri`` key — written by the
ImageExtractor with the file's source URI), encodes via the active
multi-modal embedder, and writes to ``image_embeddings_<name>``.

Image-byte resolution order:

1. ``metadata["image_bytes_b64"]`` — if the extractor stashed bytes
   inline (small icons / generated thumbnails).
2. ``metadata["image_path"]`` — local filesystem path.
3. ``metadata["source_uri"]`` / chunk text — falls back to parsing
   the source URI (filesystem://... → local path).
"""

import base64
import logging
from pathlib import Path

from .backends.postgres import PostgresBackend
from .config import Config
from .embedders.registry import register_from_config, registry
from .embedders.routing import route_for
from .ui.progress import make_progress

logger = logging.getLogger(__name__)

#: PR #81 — backend.chunks_missing_embedding now yields a
#: ``(chunk_id, text, source_uri)`` 3-tuple. Pinned as a named constant so
#: the legacy-2-tuple defensive check (and the matching ingest-side check)
#: don't trip ruff's ``PLR2004 magic-value-in-comparison`` rule.
_CHUNKS_MISSING_TUPLE_WIDTH = 3


def _resolve_image_bytes(metadata: dict) -> bytes | None:
    """Resolve image bytes from a chunk's metadata dict.

    Returns ``None`` when no resolution path matches — the caller logs
    and skips the chunk. This isolates the byte-sourcing policy in one
    place so it's easy to extend (presigned S3 URLs, etc.) without
    touching the embed loop.
    """
    # 1. inline base64 — small icons / generated previews
    b64 = metadata.get("image_bytes_b64")
    if isinstance(b64, str) and b64:
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError):
            logger.debug("image_bytes_b64 failed to decode for chunk; ignoring")

    # 2. local filesystem path written by the ImageExtractor / FilesystemSource
    img_path = metadata.get("image_path") or metadata.get("path")
    if isinstance(img_path, str) and img_path:
        try:
            return Path(img_path).read_bytes()
        except OSError as exc:
            logger.warning("Failed to read image_path %s: %s", img_path, exc)

    # 3. parse a ``filesystem://name/relative/path`` URI in source_uri
    source_uri = metadata.get("source_uri")
    if isinstance(source_uri, str) and source_uri.startswith("filesystem://"):
        # ``filesystem://<root_name>/<rel>``. Without the original root
        # we can't reconstruct the path — best-effort: drop the scheme +
        # name segment and try as an absolute / cwd-relative path.
        rest = source_uri[len("filesystem://") :]
        # Strip the root-name prefix (first segment).
        if "/" in rest:
            rest = rest.split("/", 1)[1]
        candidate = Path(rest)
        if candidate.exists():
            try:
                return candidate.read_bytes()
            except OSError:
                pass

    return None


def backfill_embedder(
    embedder_name: str, dataset_name: str | None = None, limit: int | None = None
) -> None:
    """Backfill embeddings for a specific embedder."""
    # Load config
    config = Config.load()

    # Setup backend
    backend_config = config.backend
    if backend_config.kind == "postgres":
        backend = PostgresBackend(dsn=backend_config.dsn, schema=backend_config.schema)
    elif backend_config.kind == "sqlite":
        # `backend_config.dsn` doubles as the SQLite file path
        # (e.g. "~/Library/Application Support/corpus-forge/corpus.db").
        from .backends.sqlite import SQLiteBackend  # noqa: PLC0415

        backend = SQLiteBackend(path=backend_config.dsn, schema=backend_config.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {backend_config.kind}")

    # Get the embedder config
    embedder_config = None
    for ec in config.embedders:
        if ec.name == embedder_name:
            embedder_config = ec
            break

    if not embedder_config:
        raise ValueError(f"Embedder '{embedder_name}' not found in config")

    # Apply migrations (after validating embedder config exists, before any DB writes)
    backend.migrate()

    # Register/create the embedder via the shared per-provider gating
    # so ``corpus-forge embed`` doesn't drift from ingest / search /
    # admin. The previous inline ``registry.register(...)`` call
    # passed ``device`` unconditionally, which crashed every
    # ``corpus-forge embed -e <openai-provider>`` with
    # ``TypeError: OpenAIEmbedder.__init__() got an unexpected
    # keyword argument 'device'`` — a fourth instance of the bug
    # ``register_from_config`` was created to eliminate.
    embedder = register_from_config(registry, embedder_config)

    # Build the active-embedder list (used by the routing filter below).
    # We register every active embedder so ``route_for`` sees the same
    # set the ingest path would have created.  Calling
    # ``register_from_config`` is idempotent per name (the registry
    # updates attributes in-place when the name already exists), so
    # re-registering the target embedder above is a no-op.
    active_embedders = [register_from_config(registry, ec) for ec in config.embedders if ec.active]

    # Warm up the embedder
    logger.info(f"Warming up embedder: {embedder_name}")
    embedder.warmup()

    # Register embedder with backend to get ID and create table if needed
    embedder_id = backend.register_embedder(embedder)
    logger.info(f"Registered embedder {embedder_name} with ID {embedder_id}")

    # Determine dataset ID if filtering by dataset
    dataset_id = None
    if dataset_name:
        # Get dataset ID from name
        dataset_id = backend.find_dataset_id_by_name(dataset_name)
        if dataset_id is None:
            raise ValueError(f"Dataset '{dataset_name}' not found")
        logger.info(f"Limiting backfill to dataset: {dataset_name} (ID: {dataset_id})")

    # Post-PR #81 bugfix — push the embedder's extension allow-list into
    # SQL so the backend filters at query time. Without this the paging
    # caller would re-fetch the same non-matching first 1000 rows
    # forever for a specialist whose chunks live deeper in the table.
    # ``None`` (not ``[]``) keeps the back-compat SQL fast-path.
    _ext_filter: list[str] | None = list(embedder.extensions) if embedder.extensions else None

    # Backfill embeddings — Phase L Wave 4: pre-count the work so the
    # progress bar carries an ETA, and wrap the loop in the shared
    # ``make_progress`` factory which auto-emits INFO bookends + ~every-
    # 10% milestones to the rotating log. Coerce to int defensively so
    # mock backends returning MagicMock() don't crash the wrapper.
    try:
        total_missing = int(
            backend.count_chunks_missing_embedding(embedder_id, extensions=_ext_filter)
        )
    except (TypeError, AttributeError):
        total_missing = 0
    logger.info(f"Backfilling {embedder_name}: {total_missing} chunks pending")
    processed = 0

    progress_total = total_missing if total_missing > 0 else None
    # Hoisted once per backfill (rather than per batch) — Python caches
    # the import after first use, but the in-loop form needlessly hits
    # ``sys.modules`` on every iteration.
    import time as _time  # noqa: PLC0415

    from corpus_forge.runtime_profile import record as _record  # noqa: PLC0415

    with make_progress(
        f"Embedding chunks ({embedder_name})",
        total=progress_total,
        logger=logger,
    ) as progress:
        task = progress.add_task("Embedding chunks", total=progress_total)
        # Forward-progress guard for the routing-loop. If a backend silently
        # ignores ``extensions=`` AND the in-memory route_for filters every row
        # away, ``continue`` below would re-fetch the same page forever. Abort
        # after N consecutive empty pages so operators see the issue instead of
        # the process spinning idle. Reset to 0 whenever we actually embed.
        _MAX_EMPTY_PAGE_STREAK = 10
        empty_page_streak = 0
        while True:
            # Get chunks missing this embedder's embedding.  PR #81: the
            # backend now yields ``(chunk_id, text, source_uri)``; the
            # extra column is used by the routing filter below.
            #
            # Post-#81 bugfix: pass ``extensions=`` so the backend
            # filters in SQL.  With the SQL push the in-memory
            # ``route_for`` filter below becomes a no-op on every page;
            # it's kept as defense-in-depth in case a custom backend
            # ignores the kwarg.
            raw_rows = list(
                backend.chunks_missing_embedding(embedder_id, limit=1000, extensions=_ext_filter)
            )

            if not raw_rows:
                logger.debug("No more chunks need embedding")
                break

            # Defend against legacy 2-tuple stubs left over from old test
            # fixtures — surface a clear error rather than silently
            # routing every chunk to the catchall.
            if raw_rows and len(raw_rows[0]) != _CHUNKS_MISSING_TUPLE_WIDTH:
                raise ValueError(
                    "backend.chunks_missing_embedding must yield "
                    "(chunk_id, text, source_uri) 3-tuples after PR #81; got "
                    f"{len(raw_rows[0])}-tuple — update the backend (or stub) "
                    "to include source_uri."
                )

            # PR #81 — extension-based routing.  Filter rows down to those
            # that the routing rule assigns to *this* embedder under the
            # set of currently-active embedders.  When no specialist is
            # in play (single-tower configs), every row passes through
            # (the catchall claims everything by definition).
            chunks_needing = [
                (cid, text)
                for (cid, text, src_uri) in raw_rows
                if route_for(src_uri, active_embedders) is embedder
            ]

            if not chunks_needing:
                # Post-PR-#81 bugfix: the original code did ``break`` here,
                # which gave up the entire backfill when the first page
                # happened to contain zero matches — the exact bug this
                # PR fixes.  With the SQL push above the backend should
                # never return a page of all-non-matching rows, but if it
                # does (e.g. a custom backend that ignores ``extensions=``),
                # we ``continue`` to the next page so the loop drains the
                # corpus instead of giving up.  Only ``raw_rows == []``
                # (handled above) is a real end-of-stream signal.
                empty_page_streak += 1
                logger.warning(
                    "Page of %d rows had no matches after in-memory route_for "
                    "for embedder %s (streak %d/%d). Likely cause: backend "
                    "ignores extensions= kwarg and the pending pool's first "
                    "page is dominated by non-matching chunks.",
                    len(raw_rows),
                    embedder.name,
                    empty_page_streak,
                    _MAX_EMPTY_PAGE_STREAK,
                )
                if empty_page_streak >= _MAX_EMPTY_PAGE_STREAK:
                    raise RuntimeError(
                        f"Backfill aborted for embedder {embedder.name!r}: "
                        f"{empty_page_streak} consecutive pages had zero matches "
                        "after in-memory routing. The backend likely ignores the "
                        "`extensions=` filter so the same non-matching page keeps "
                        "coming back. Fix the backend's chunks_missing_embedding "
                        "to honor extensions, or remove the embedder's extensions "
                        "list to make it a catchall."
                    )
                continue

            chunk_ids, texts = zip(*chunks_needing, strict=True) if chunks_needing else ([], [])

            # Apply dataset filter if needed
            if dataset_id is not None:
                # Filter chunks by dataset
                filtered_pairs = []
                for chunk_id, text in zip(chunk_ids, texts, strict=True):
                    # Check which dataset this chunk belongs to
                    # This would require a JOIN query - simplified for now
                    # In a real implementation, we'd modify the chunks_missing_embedding query
                    filtered_pairs.append((chunk_id, text))
                chunk_ids, texts = zip(*filtered_pairs, strict=True) if filtered_pairs else ([], [])

                if not chunk_ids:
                    logger.debug("No more chunks need embedding for this dataset")
                    break

            # Apply limit if specified
            if limit is not None:
                remaining = limit - processed
                if remaining <= 0:
                    break
                if len(chunk_ids) > remaining:
                    chunk_ids = chunk_ids[:remaining]
                    texts = texts[:remaining]

            # Generate embeddings (per-batch chatter demoted to DEBUG —
            # the progress bar + milestone INFO replace it on stdout/log).
            logger.debug(f"Generating embeddings for {len(texts)} chunks")
            _t0 = _time.perf_counter()
            embeddings = embedder.encode(texts)
            _encode_elapsed = _time.perf_counter() - _t0

            # Same bisection-recovery contract as ingest.py: skip
            # chunk_ids whose corresponding text was bisected out by
            # the embedder. The skipped chunks stay pending so the
            # next ``corpus-forge embed`` pass retries them after the
            # model recovers.
            failed_indices: set[int] = set(getattr(embedder, "last_failed_indices", []))
            if failed_indices:
                chunk_ids = [cid for i, cid in enumerate(chunk_ids) if i not in failed_indices]
                logger.warning(
                    "Embedder %s skipped %d/%d chunks in this batch (NaN-shaped "
                    "response or 5xx); they stay pending for retry.",
                    embedder.name,
                    len(failed_indices),
                    len(texts),
                )

            # Write embeddings
            pairs = list(zip(chunk_ids, embeddings, strict=True))

            # Hang-guard: if every chunk in this fetch got bisected
            # out (e.g. the active embedder is wedged across the whole
            # backlog), ``pairs`` is empty AND the same chunk_ids
            # would come back on the next ``chunks_missing_embedding``
            # call — infinite loop. Break and let the operator
            # re-run ``corpus-forge embed`` once the embedder
            # recovers. The chunks stay in
            # ``chunks_missing_embedding`` so no work is lost.
            if not pairs:
                logger.warning(
                    "Embedder %s skipped every chunk in this batch (%d failed); "
                    "exiting the embed loop to avoid an infinite retry cycle. "
                    "Re-run `corpus-forge embed -e %s` after the embedder "
                    "recovers; the skipped chunks stay pending.",
                    embedder.name,
                    len(failed_indices),
                    embedder.name,
                )
                break

            _t1 = _time.perf_counter()
            backend.write_embeddings(embedder_id, pairs)
            _write_elapsed = _time.perf_counter() - _t1
            processed += len(pairs)
            # Forward progress — reset the empty-page guard.
            empty_page_streak = 0
            progress.update(task, completed=processed)

            # Wall-clock calibration — record this batch's embed rate
            # AND the per-chunk DB-write rate so the on-disk profile
            # converges on real hardware. Best-effort; ``record`` swallows
            # any IO failure on the profile file.
            try:
                if pairs:
                    _record(
                        "embed",
                        units=len(pairs),
                        seconds=_encode_elapsed,
                        key=embedder.name,
                    )
                    _record(
                        "db_write",
                        units=len(pairs),
                        seconds=_write_elapsed,
                    )
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("embed: calibration write failed: %s", exc)

            logger.debug(f"Processed {processed} embeddings so far")

            # Break if we've hit the limit
            if limit is not None and processed >= limit:
                break

    logger.info(f"Backfill complete. Processed {processed} embeddings for {embedder_name}")


def backfill_image_embedder(
    embedder_name: str,
    dataset_name: str | None = None,
    limit: int | None = None,
) -> int:
    """Phase G (G-15) — backfill image embeddings for ``image``-labeled chunks.

    Builds a :class:`ClipLocalEmbedder` (or :class:`ClipRemoteEmbedder`
    if a future ``[multimodal-remote]`` config switch lands), iterates
    chunks where ``format=image``, and writes vectors to
    ``image_embeddings_<embedder_name>``.

    Returns the number of chunks embedded (useful for tests + CLI
    progress reporting).
    """
    config = Config.load()

    backend_config = config.backend
    if backend_config.kind == "postgres":
        backend = PostgresBackend(dsn=backend_config.dsn, schema=backend_config.schema)
    elif backend_config.kind == "sqlite":
        from .backends.sqlite import SQLiteBackend  # noqa: PLC0415

        backend = SQLiteBackend(path=backend_config.dsn, schema=backend_config.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {backend_config.kind}")

    backend.migrate()

    # Build the multi-modal embedder. Currently only the local CLIP
    # backend is wired in via this CLI surface; a remote toggle can land
    # by reading from a future ``Config.multimodal`` block.
    from .embedders.clip_local import ClipLocalEmbedder  # noqa: PLC0415

    embedder = ClipLocalEmbedder(name=embedder_name)
    embedder.warmup()

    embedder_id = backend.register_multimodal_embedder(
        name=embedder.name,
        model_id=embedder.model_id,
        dimension=embedder.dimension,
    )
    logger.info("Registered multi-modal embedder %s with ID %d", embedder_name, embedder_id)

    if dataset_name is not None:
        ds_id = backend.find_dataset_id_by_name(dataset_name)
        if ds_id is None:
            raise ValueError(f"Dataset '{dataset_name}' not found")

    # Phase L Wave 4: image-side count helper not yet wired (there's no
    # ``count_image_chunks_missing_embedding`` companion today), so the
    # progress bar runs unbounded for the image lane. Wave 5+ can backfill
    # the count helper to surface an ETA.
    processed = 0
    with make_progress(
        f"Embedding images ({embedder_name})",
        total=None,
        logger=logger,
    ) as progress:
        task = progress.add_task("Embedding images", total=None)
        while True:
            batch = list(backend.image_chunks_missing_embedding(embedder_id, limit=128))
            if not batch:
                break

            if limit is not None:
                remaining = limit - processed
                if remaining <= 0:
                    break
                batch = batch[:remaining]

            # Resolve image bytes for each chunk; skip chunks where the
            # bytes can't be sourced.
            resolved: list[tuple[int, bytes]] = []
            for chunk_id, meta in batch:
                img_bytes = _resolve_image_bytes(meta)
                if img_bytes is None:
                    logger.warning(
                        "Cannot resolve image bytes for chunk %d (metadata=%r); skipping",
                        chunk_id,
                        meta,
                    )
                    continue
                resolved.append((chunk_id, img_bytes))

            if not resolved:
                # Nothing in this batch had resolvable image bytes —
                # advance by skipping (otherwise the loop will spin on the
                # same un-embeddable rows forever). Break out and let the
                # user re-run after fixing the metadata.
                logger.warning(
                    "No image bytes resolved for batch of %d chunks — stopping backfill",
                    len(batch),
                )
                break

            chunk_ids = [cid for cid, _ in resolved]
            embeddings = embedder.encode_image([b for _, b in resolved])
            backend.write_image_embeddings(
                embedder_id, list(zip(chunk_ids, embeddings, strict=True))
            )
            processed += len(resolved)
            progress.update(task, advance=len(resolved))
            logger.debug("Processed %d image embeddings so far", processed)

            if limit is not None and processed >= limit:
                break

    logger.info("Image backfill complete. Processed %d embeddings for %s", processed, embedder_name)
    return processed


def main(
    embedder: str,
    dataset: str | None = None,
    limit: int | None = None,
    *,
    image: bool = False,
) -> None:
    """Main entry point for embed command.

    Phase G (G-15): ``image=True`` routes through
    :func:`backfill_image_embedder` instead of the text path.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        if image:
            backfill_image_embedder(embedder, dataset, limit)
        else:
            backfill_embedder(embedder, dataset, limit)
    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise


if __name__ == "__main__":
    raise SystemExit(
        "Use `corpus-forge embed` (the Typer CLI); "
        "python -m corpus_forge.embed is no longer supported."
    )
