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

from .backends.base import FederationUnsupported
from .backends.postgres import PostgresBackend
from .config import Config
from .embedders.registry import register_from_config, registry
from .embedders.routing import route_for
from .ui.progress import make_progress

logger = logging.getLogger(__name__)

#: RFC fleet-2 — canonical, never-patched reference to the Postgres
#: backend class used by :func:`backfill_embedder` to decide whether the
#: distributed claim/release path applies. Unit tests patch
#: ``corpus_forge.embed.PostgresBackend`` (the constructor) to inject a
#: MagicMock backend; this private alias is a *separate* module attribute
#: those patches don't touch, so ``isinstance(backend, _RealPostgresBackend)``
#: is reliably ``False`` for the mock (→ fallback path) and ``True`` only
#: for a genuine Postgres backend (→ claim path).
_RealPostgresBackend = PostgresBackend

#: PR #81 — backend.chunks_missing_embedding now yields a
#: ``(chunk_id, text, source_uri)`` 3-tuple. Pinned as a named constant so
#: the legacy-2-tuple defensive check (and the matching ingest-side check)
#: don't trip ruff's ``PLR2004 magic-value-in-comparison`` rule.
_CHUNKS_MISSING_TUPLE_WIDTH = 3

#: rfc-fleet-1 item 5 — passive telemetry checkpoint interval.  Every
#: ``_TELEMETRY_CHECKPOINT_EVERY`` processed chunks the backfill writes a
#: ``model_benchmarks`` row (``source="embed-run"``) carrying the
#: aggregate observed rate so a crashed run still reports.  We INSERT a
#: row per checkpoint rather than UPDATE one — the table's PK is a
#: bigserial with no natural per-run key, so append-only keeps the
#: backend helper simple.  At 10k-chunk granularity a full 3.29M-chunk
#: backfill emits ~329 rows (+1 end-of-run), which is bounded and each is
#: a real datapoint for the "latest per host+model" reads.
_TELEMETRY_CHECKPOINT_EVERY = 10_000


def _write_embed_run_telemetry(
    backend,
    config: "Config",
    embedder_config,
    *,
    transport: str,
    device: str,
    processed: int,
    elapsed_s: float,
) -> None:
    """Best-effort ``source="embed-run"`` benchmark row from a live backfill.

    Failure-isolated exactly like the heartbeat: a telemetry write must
    NEVER break or slow the backfill, so every failure path (no
    ``insert_model_benchmark`` hook on the backend, an unreachable DB, a
    zero-length window) is swallowed with a debug log.  Latencies are
    ``None`` — passive telemetry measures the aggregate batched rate, not
    per-request round trips.
    """

    if processed <= 0 or elapsed_s <= 0:
        return
    try:
        chunks_per_s = processed / elapsed_s
        # RFC fleet-6 item 3 — key telemetry on the CANONICAL model identity so
        # the same model served under aliased provider/model_id names accrues
        # under one `corpus.models` row. No aliases → the embedder's own pair.
        from corpus_forge.embedders.identity import canonical_model_key  # noqa: PLC0415

        model_key = canonical_model_key(embedder_config)
        backend.insert_model_benchmark(
            host_id=config.host_id(),
            model_key=model_key,
            source="embed-run",
            transport=transport,
            device=device,
            batch_size=getattr(embedder_config, "batch_size", None),
            sample_chunks=processed,
            chunks_per_s=chunks_per_s,
            tokens_per_s=None,
            latency_p50_ms=None,
            latency_p95_ms=None,
        )
        logger.debug("embed telemetry: recorded embed-run row at %d chunks", processed)
    except Exception as exc:
        logger.debug("embed telemetry: embed-run write skipped (%r)", exc)


#: RFC fleet-2 reviewer finding — sqlstate for a foreign-key violation.
#: A silent ``_telemetry_heartbeat`` failure leaves no ``corpus.hosts`` row,
#: so the first ``claim_chunks_for_embedding`` insert trips the
#: ``embed_claims.host_id`` FK with ``psycopg.errors.ForeignKeyViolation``
#: (sqlstate ``23503``). We detect it WITHOUT importing psycopg at module
#: level — the import must stay lazy/optional so a sqlite-only install never
#: pays for it — by sniffing the exception's ``sqlstate`` attribute (psycopg
#: surfaces the SQLSTATE there) and, defensively, its class name.
_FK_VIOLATION_SQLSTATE = "23503"


def _is_fk_violation(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` is a Postgres foreign-key violation.

    Matched structurally so no ``import psycopg`` is needed at module load:
    psycopg's ``Error`` exposes the SQLSTATE on a ``sqlstate`` attribute, and
    the concrete class is named ``ForeignKeyViolation``. Either signal is
    enough — checking both keeps the probe robust across psycopg minor
    versions and any driver that mimics the DB-API ``sqlstate`` convention.
    """
    if getattr(exc, "sqlstate", None) == _FK_VIOLATION_SQLSTATE:
        return True
    return type(exc).__name__ == "ForeignKeyViolation"


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


def filter_embedders_by_lanes(embedder_names, lanes):
    """RFC fleet-2 item 4 — intersect ``embedder_names`` with the host's lanes.

    This is the lane filter for the *implicit* multi-embedder paths —
    the daemon embed-worker (``get_active_embedders`` →
    ``_flush_all_pending_embeddings``) and agent auto-ingest — where the
    host should
    only work the lanes its local ``[embed] lanes`` config pins it to.

    Semantics:

    - ``lanes`` empty / falsy → return ``embedder_names`` unchanged (the
      hard backcompat bar: absent ``[embed] lanes`` means "all active
      embedders", today's behaviour, byte-identical).
    - ``lanes`` non-empty → keep only names that appear in ``lanes``,
      preserving the input order of ``embedder_names`` (config /
      declaration order) so the worker drains lanes deterministically.

    Lane names are validated against ``[[embedders]]`` at config-load time
    (``Config._check_embed_lanes``), so any name in ``lanes`` is a real
    embedder; the intersection here is purely "which of the active ones
    does THIS host serve".  Returns a fresh ``list``.

    Note: this is NOT the override path — an operator's explicit
    ``corpus-forge embed -e <name>`` bypasses this filter entirely (see
    :func:`embedder_outside_lanes`); only warn-and-proceed applies there.
    """
    if not lanes:
        return list(embedder_names)
    lane_set = set(lanes)
    return [name for name in embedder_names if name in lane_set]


def embedder_outside_lanes(embedder_name: str, lanes) -> bool:
    """Return ``True`` when an explicit ``-e`` target is outside the lanes.

    RFC fleet-2 item 4: an explicit ``corpus-forge embed -e <name>``
    OVERRIDES lane pinning — the operator's direct command wins and the
    backfill proceeds regardless.  The caller uses this predicate only to
    decide whether to emit a one-line WARN ("you pinned lanes X but asked
    for Y; proceeding anyway") before running.  Empty ``lanes`` (the
    default) means "no pinning", so nothing is ever outside it.
    """
    if not lanes:
        return False
    return embedder_name not in set(lanes)


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

    # Fleet telemetry (rfc-fleet-1): record this host + its available
    # models once per embed run.  Failure-isolated inside the helper, so a
    # briefly-unreachable backend never adds a failure mode to embed.
    from corpus_forge.telemetry_registry import heartbeat as _telemetry_heartbeat  # noqa: PLC0415

    _telemetry_heartbeat(backend, config)

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

    # RFC fleet-2 — distributed claim/release backfill.  On a real Postgres
    # backend the loop reserves chunks in ``corpus.embed_claims`` so N hosts
    # drain the same lane with zero duplicated GPU compute; SQLite (and the
    # MagicMock backends in the unit suite) fall back to the single-host
    # ``chunks_missing_embedding`` path, which stays byte-identical.
    #
    # ``_RealPostgresBackend`` (module top) is the never-patched class
    # reference, so the gate is not fooled by tests that patch
    # ``corpus_forge.embed.PostgresBackend`` — a MagicMock backend is
    # correctly NOT an instance and takes the fallback path.
    host_id = config.host_id()
    lease_ttl = config.embed.claim_lease_ttl
    # Heartbeat the host row up-front: the ``embed_claims.host_id`` FK
    # requires a ``corpus.hosts`` row before any claim insert.  The shared
    # telemetry heartbeat above (``_telemetry_heartbeat``) already upserts
    # it (failure-isolated); ``use_claims`` only matters when that succeeded.
    use_claims = isinstance(backend, _RealPostgresBackend)

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

    # RFC fleet-2 — truthful progress total on the claim path: subtract the
    # chunks *other* hosts have already reserved (work this host will never
    # do), floored at 0, so concurrent workers each report their share of a
    # shrinking pool.  The fallback path keeps today's exact total.
    if use_claims:
        try:
            other_claims = int(backend.count_live_claims(embedder_id, exclude_host_id=host_id))
            total_missing = max(total_missing - other_claims, 0)
        except FederationUnsupported:
            # Real Postgres always supports this; only here for symmetry
            # with the claim/fallback decision made lazily in the loop.
            use_claims = False
        except (TypeError, AttributeError):
            pass

    logger.info(f"Backfilling {embedder_name}: {total_missing} chunks pending")
    processed = 0

    # rfc-fleet-1 item 5 — passive telemetry.  Resolve transport/device
    # once (failure-isolated inside the helpers) and start the run clock so
    # checkpoint + end-of-run rows carry the aggregate observed rate.
    from corpus_forge.admin.bench import resolve_device, resolve_transport  # noqa: PLC0415

    _telemetry_transport = resolve_transport(embedder_config)
    _telemetry_device = resolve_device(_telemetry_transport)
    _next_telemetry_at = _TELEMETRY_CHECKPOINT_EVERY

    progress_total = total_missing if total_missing > 0 else None
    # Hoisted once per backfill (rather than per batch) — Python caches
    # the import after first use, but the in-loop form needlessly hits
    # ``sys.modules`` on every iteration.
    import time as _time  # noqa: PLC0415

    from corpus_forge.runtime_profile import record as _record  # noqa: PLC0415

    # Run clock for passive telemetry — wall time over the whole backfill
    # so the recorded rate reflects real throughput (encode + DB write).
    _run_started = _time.perf_counter()

    with make_progress(
        f"Embedding chunks ({embedder_name})",
        total=progress_total,
        logger=logger,
    ) as progress:
        task = progress.add_task("Embedding chunks", total=progress_total)
        # Forward-progress cursor. Advances by ``max(c.id)`` of each page so
        # the next fetch skips chunks we already considered (whether or not
        # they routed to this embedder). This is the structural fix that
        # makes catchall backfills correct when the pending pool is
        # dominated by specialist-owned chunks — without it, ``continue``
        # would re-fetch the same non-matching first page forever.
        # RFC fleet-2: the cursor only moves forward within a run, so a chunk
        # another host releases behind this host's ``after_id`` is skipped
        # now and picked up by the NEXT backfill invocation (the cursor
        # resets to ``None`` each run).
        last_seen_id: int | None = None
        # Defense-in-depth alarm for the *specialist* path only: when a
        # backend honors ``extensions=`` correctly every page is dense with
        # matches, so a run of empty pages signals the SQL filter is
        # broken. Catchall runs legitimately see all-skip pages while the
        # cursor walks past specialist-owned rows, so the guard would false-
        # fire there.
        _MAX_EMPTY_PAGE_STREAK = 10
        empty_page_streak = 0
        # RFC fleet-2 — the FK-violation self-heal only fires on the FIRST
        # claim: a missing ``corpus.hosts`` row (silent heartbeat failure)
        # trips the ``embed_claims.host_id`` FK there. The handler re-
        # heartbeats and retries the claim once (see ``_fetch_page``). After
        # any successful claim the host row demonstrably exists, so a later
        # 23503 is a genuine error and must propagate.
        first_claim_attempt = True

        def _fetch_page(after_id: int | None) -> list[tuple[int, str, str]]:
            """Fetch one page of chunks to embed.

            RFC fleet-2: on the claim path this atomically reserves the
            page in ``corpus.embed_claims`` (so concurrent hosts never
            double-embed); the fallback path is the byte-identical
            ``chunks_missing_embedding`` fetch.  Both yield the same
            ``(chunk_id, text, source_uri)`` 3-tuple shape, so the rest
            of the loop is path-agnostic.

            ``nonlocal use_claims`` lets a ``FederationUnsupported`` (the
            backend can't federate) demote the run to the fallback path
            permanently — after that every page uses
            ``chunks_missing_embedding`` (the single-host SQLite path stays
            exactly as it was). A first-claim foreign-key violation (the
            heartbeat silently failed, so no ``corpus.hosts`` row backs the
            ``embed_claims.host_id`` FK) does NOT demote: it re-heartbeats
            and retries the claim once, so the worker rejoins the claim loop
            once its host row self-heals (RFC fleet-2 live-bug fix).
            """
            nonlocal use_claims, first_claim_attempt
            if use_claims:
                try:
                    page = backend.claim_chunks_for_embedding(
                        embedder_id,
                        host_id,
                        batch=1000,
                        lease_ttl=lease_ttl,
                        extensions=_ext_filter,
                        after_id=after_id,
                    )
                    # A successful claim proves the host row exists; from now
                    # on a 23503 would be a real error, never the missing-row
                    # demotion case.
                    first_claim_attempt = False
                    return page
                except FederationUnsupported:
                    logger.info(
                        "Backend does not support embed claims; falling back to "
                        "the single-host chunks_missing_embedding path."
                    )
                    use_claims = False
                except Exception as exc:
                    # RFC fleet-2 live bug (2026-06-08): a silent heartbeat
                    # failure leaves no ``corpus.hosts`` row, so the FIRST claim
                    # insert trips the ``embed_claims.host_id`` FK (sqlstate
                    # 23503). The previous code latched ``use_claims = False``
                    # PERMANENTLY here — demoting this worker to the un-deduped
                    # ``chunks_missing_embedding`` fallback for its whole
                    # lifetime, so it raced other hosts on the same chunks with
                    # no ``SKIP LOCKED`` coordination. But a missing-row FK is
                    # TRANSIENT: the host row self-heals on the next heartbeat.
                    # So re-heartbeat once (mandatory this time, not the
                    # swallowed best-effort one) and RETRY the claim. If the
                    # retry succeeds the worker rejoins the claim loop; if it
                    # still FK-violates the row is genuinely missing and the
                    # error propagates — we never silently latch. Permanent
                    # demotion is reserved for ``FederationUnsupported`` (the
                    # SQLite-can't-federate case handled above). Caught narrowly
                    # (FK only, first claim only) so no other failure is masked.
                    if first_claim_attempt and _is_fk_violation(exc):
                        logger.warning(
                            "First embed-claim insert hit a foreign-key violation "
                            "(no corpus.hosts row — the telemetry heartbeat likely "
                            "failed silently); re-heartbeating and retrying the "
                            "claim rather than demoting to the single-host path."
                        )
                        try:
                            _telemetry_heartbeat(backend, config)
                        except Exception:
                            # Best-effort: if the re-heartbeat itself fails, the
                            # claim retry below surfaces the real problem (and
                            # propagates) instead of this masking it.
                            logger.warning(
                                "Re-heartbeat before claim retry failed.",
                                exc_info=True,
                            )
                        # Retry the claim once. A success proves the host row now
                        # exists → stay on the claim path (use_claims untouched).
                        # A repeat FK propagates: the row is genuinely missing,
                        # which is a real error, not the transient startup race.
                        page = backend.claim_chunks_for_embedding(
                            embedder_id,
                            host_id,
                            batch=1000,
                            lease_ttl=lease_ttl,
                            extensions=_ext_filter,
                            after_id=after_id,
                        )
                        first_claim_attempt = False
                        return page
                    raise
            return list(
                backend.chunks_missing_embedding(
                    embedder_id,
                    limit=1000,
                    extensions=_ext_filter,
                    after_id=after_id,
                )
            )

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
            raw_rows = _fetch_page(last_seen_id)

            if not raw_rows:
                logger.debug("No more chunks need embedding")
                break

            # RFC fleet-2 — on the claim path every row we just fetched is
            # now reserved for THIS host. Track the reservations so the
            # try/finally below releases any chunk we don't durably embed
            # (routed out, dataset-filtered, limit-truncated, or
            # NaN-skipped) — release-on-error makes a poison chunk
            # immediately retryable elsewhere; lease expiry is only the
            # crash path.
            page_claimed_ids: set[int] = {row[0] for row in raw_rows} if use_claims else set()

            # Advance the cursor before the in-memory router has a chance to
            # drop rows. The cursor must track the LAST chunk_id the backend
            # returned, regardless of whether route_for claimed any of them
            # — otherwise we re-fetch the same page on the next iteration.
            last_seen_id = max(row[0] for row in raw_rows)

            # RFC fleet-2 — try/finally so claims never leak: every chunk
            # reserved for this page that we don't durably embed (routed
            # out, dataset-filtered, limit-truncated, NaN-skipped, or an
            # exception mid-page) gets its claim released, making a poison
            # chunk immediately retryable on another host. Persisted chunks
            # are released too (the embedding row is the durable record).
            try:
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
                    # Cursor (``last_seen_id``) has already advanced above, so
                    # ``continue`` is safe — next iteration will fetch the next
                    # page rather than re-fetching this one.
                    #
                    # Defense-in-depth alarm: only specialists expect every
                    # page to be dense with matches (because the SQL filter is
                    # supposed to do the work). A streak of empty pages in
                    # that path means the backend ignored ``extensions=``.
                    # Catchall runs legitimately walk over all-skip pages
                    # while the cursor advances past specialist-owned rows,
                    # so don't trip the alarm for them.
                    if _ext_filter is not None:
                        empty_page_streak += 1
                        logger.warning(
                            "Page of %d rows had no matches after in-memory "
                            "route_for for specialist embedder %s (streak %d/%d). "
                            "Likely cause: backend ignored extensions= and the "
                            "page is dominated by non-matching chunks.",
                            len(raw_rows),
                            embedder.name,
                            empty_page_streak,
                            _MAX_EMPTY_PAGE_STREAK,
                        )
                        if empty_page_streak >= _MAX_EMPTY_PAGE_STREAK:
                            raise RuntimeError(
                                f"Backfill aborted for specialist embedder "
                                f"{embedder.name!r}: {empty_page_streak} consecutive "
                                "pages had zero matches after in-memory routing. "
                                "The backend likely ignores the `extensions=` "
                                "filter. Fix the backend's chunks_missing_embedding "
                                "to honor extensions, or remove the embedder's "
                                "extensions list to make it a catchall."
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
                    chunk_ids, texts = (
                        zip(*filtered_pairs, strict=True) if filtered_pairs else ([], [])
                    )

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

                # rfc-fleet-1 item 5 — checkpoint passive telemetry every
                # ~10k chunks so a crashed run still reports its rate.
                # Failure-isolated inside the helper.
                if processed >= _next_telemetry_at:
                    _write_embed_run_telemetry(
                        backend,
                        config,
                        embedder_config,
                        transport=_telemetry_transport,
                        device=_telemetry_device,
                        processed=processed,
                        elapsed_s=_time.perf_counter() - _run_started,
                    )
                    _next_telemetry_at += _TELEMETRY_CHECKPOINT_EVERY

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
            finally:
                # RFC fleet-2 — release every claim made for this page.
                # Persisted chunks: the embedding row is durable, so the
                # reservation is no longer needed. Unpersisted chunks
                # (skipped/failed/leaked): release makes them immediately
                # retryable elsewhere — lease expiry is only the crash path.
                # ``release_claims`` is host-scoped, so a slow host whose
                # lease already expired and got reclaimed elsewhere won't
                # clobber the new owner. Guarded so a release failure can't
                # mask the real error propagating out of the try body.
                if use_claims and page_claimed_ids:
                    try:
                        backend.release_claims(embedder_id, host_id, list(page_claimed_ids))
                    except FederationUnsupported:
                        # Path was demoted to fallback mid-run; nothing to release.
                        pass
                    except Exception as exc:  # pragma: no cover — defensive
                        logger.warning(
                            "embed: release_claims failed for %d chunk(s): %r",
                            len(page_claimed_ids),
                            exc,
                        )

    # rfc-fleet-1 item 5 — end-of-run passive telemetry row.  Best-effort;
    # a zero-work run (processed == 0) is skipped inside the helper.
    _write_embed_run_telemetry(
        backend,
        config,
        embedder_config,
        transport=_telemetry_transport,
        device=_telemetry_device,
        processed=processed,
        elapsed_s=_time.perf_counter() - _run_started,
    )

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
