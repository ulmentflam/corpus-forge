"""Storage backend protocol for corpus-forge."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    import numpy as np

    from corpus_forge.embedders.base import Embedder
    from corpus_forge.retrieval.types import Hit
    from corpus_forge.sources.base import RawConversation, RawDocument


class IngestRunInProgressError(RuntimeError):
    """Raised when a concurrent ingest run is already in progress on this host.

    Exit code convention: callers that catch this at the CLI boundary MUST
    exit with code 75 (POSIX EX_TEMPFAIL — "temporary failure, retry later").
    """


class FederationUnsupported(RuntimeError):
    """Raised when a fleet-federation operation is attempted on a backend
    that cannot coordinate multiple hosts.

    Distributed claim-based embedding backfill (RFC ``fleet-2``) relies on
    a single shared Postgres for cross-host coordination
    (``FOR UPDATE SKIP LOCKED`` + ``ON CONFLICT DO NOTHING`` on
    ``corpus.embed_claims``). The SQLite backend is single-machine by
    construction, so :meth:`StorageBackend.claim_chunks_for_embedding`,
    :meth:`StorageBackend.release_claims`, and
    :meth:`StorageBackend.expire_stale_claims` raise this on SQLite rather
    than silently letting two hosts duplicate compute.

    Federated config sharing (RFC ``fleet-3``) relies on the same shared
    Postgres, so :meth:`StorageBackend.get_shared_config` and
    :meth:`StorageBackend.put_shared_config` raise this on SQLite too.
    """


class SharedConfigVersionConflict(RuntimeError):
    """Raised when a :meth:`StorageBackend.put_shared_config` write loses the
    optimistic-concurrency race.

    Federated config publish (RFC ``fleet-3``) is optimistic: the
    publisher passes the ``version`` it last pulled, and the write only
    lands if the DB is still at that version. When a peer published in the
    meantime — or another host did the very first publish concurrently —
    the conditional write affects zero rows and this is raised instead of
    silently clobbering the newer config. The operator's fix is always the
    same: pull the current shared config first, then re-publish on top.
    """


def normalize_extensions_filter(extensions: "list[str] | None") -> list[str]:
    """Normalise an ``extensions=`` allow-list for SQL pushdown.

    Post-PR #81 bugfix: shared helper used by both Postgres and SQLite
    backends so the ``chunks_missing_embedding`` /
    ``count_chunks_missing_embedding`` ``extensions=`` filter behaves
    identically across backends.

    - ``None`` or ``[]`` → ``[]`` (no filter; back-compat behaviour preserved).
    - Each entry is lowercased and given a leading ``.`` if missing
      (``"py"`` and ``".PY"`` both → ``".py"``).
    - Empty strings or non-string entries raise ``ValueError`` /
      ``TypeError`` (defence in depth — empty string would otherwise
      yield ``LIKE '%'`` and silently match every row, defeating the
      filter).
    """
    if not extensions:
        return []
    out: list[str] = []
    for ext in extensions:
        if not isinstance(ext, str):
            raise TypeError(f"extension entries must be strings; got {type(ext).__name__}: {ext!r}")
        if not ext:
            raise ValueError("extension must be a non-empty string")
        lower = ext.lower()
        if not lower.startswith("."):
            lower = "." + lower
        out.append(lower)
    return out


class StorageBackend(Protocol):
    """Pluggable storage backend. Implementations live behind this Protocol."""

    def migrate(self) -> None: ...

    def register_embedder(self, embedder: "Embedder") -> int: ...

    def upsert_document(
        self,
        dataset_id: int,
        doc: "RawDocument",
        chunks: list[Any],
        embedder_ids: "list[int] | None" = None,
    ) -> int:
        """Insert or update a document and its chunks.

        Phase D housekeeping (HK-2): ``chunks`` accepts either
        :class:`~corpus_forge.chunkers.base.TextChunk` instances
        (preferred — carries ``metadata``, ``role``, ``token_count``) or
        the legacy ``(heading, text)`` 2-tuple shape used by older tests
        and ``tests/smoke``. Implementations coerce on the way in.

        Typed as ``list[Any]`` so the invariant-``list`` mismatch between
        the Protocol and the per-backend implementations stays out of
        the type checker's way; both backends document the accepted
        shapes in their own docstrings.
        """
        ...

    def find_document(self, dataset_id: int, source_uri: str) -> "dict | None": ...

    def upsert_conversation(
        self,
        dataset_id: int,
        conv: "RawConversation",
        chunked_messages: list[list[Any]],
    ) -> int:
        """Insert or update a conversation and its messages/chunks.

        ``chunked_messages`` accepts either :class:`TextChunk` instances
        or the legacy ``(heading, text)`` 2-tuple shape, same as
        :meth:`upsert_document`.
        """
        ...

    def write_embeddings(self, embedder_id: int, pairs: list[tuple[int, "np.ndarray"]]) -> None: ...

    def chunks_missing_embedding(
        self,
        embedder_id: int,
        limit: int = 1024,
        *,
        extensions: "list[str] | None" = None,
        after_id: int | None = None,
    ) -> Iterator[tuple[int, str, str]]:
        """Yield ``(chunk_id, text, source_uri)`` for chunks missing an
        embedding under ``embedder_id``.

        PR #81 widened the tuple to include ``source_uri`` (taken from the
        parent ``documents`` row) so the caller can route each chunk to
        exactly one of the active embedders via
        :func:`corpus_forge.embedders.routing.claims`.  Chunks whose
        document has no ``source_uri`` (defensively: shouldn't happen,
        the column is ``NOT NULL`` in the schema) get ``""`` so the
        catchall claims them.

        Post-#81 bugfix: ``extensions`` is an optional case-insensitive
        suffix allow-list. When provided, the backend filters in SQL so the
        Python caller doesn't have to page through millions of unrelated
        chunks looking for matches.

        - ``None`` or ``[]`` → unfiltered (back-compat with PR #81 baseline).
        - Non-empty list → only rows whose source_uri (from documents OR
          conversations, via COALESCE) ends with one of the lowercased,
          dot-prefixed extensions are returned. Entries are normalised:
          ``"py"`` and ``".PY"`` both become ``".py"``.
        - Empty-string or non-string entries → ``ValueError``.

        ``after_id`` is a forward-progress cursor: when set, only chunks with
        ``c.id > after_id`` are returned. Combined with ``ORDER BY c.id``,
        this lets callers iterate the entire pending pool deterministically
        without re-fetching pages where every row got skipped in-memory
        (the bug catchall backfills hit when the first page is dominated by
        specialist-owned chunks the catchall doesn't claim).
        """
        ...

    def count_chunks_missing_embedding(
        self,
        embedder_id: int,
        *,
        extensions: "list[str] | None" = None,
    ) -> int:
        """Total number of chunks still missing an embedding under
        ``embedder_id`` — companion to :meth:`chunks_missing_embedding`.

        ``extensions`` follows the same normalise-and-filter contract as
        :meth:`chunks_missing_embedding`. With the post-PR #81 fix, the
        count is filtered by the same SQL allow-list as the paging query
        so the embed progress bar's ETA reflects the *real* work for a
        specialist embedder rather than the unfiltered chunks total.
        """
        ...

    # --- Fleet 2 — distributed claim-based embedding backfill -------------

    def claim_chunks_for_embedding(
        self,
        embedder_id: int,
        host_id: str,
        batch: int = 1024,
        lease_ttl: int = 600,
        *,
        extensions: "list[str] | None" = None,
        after_id: int | None = None,
    ) -> "list[tuple[int, str, str]]":
        """Atomically reserve up to ``batch`` not-yet-embedded chunks for ``host_id``.

        Fleet-2 (RFC ``rfc-fleet-2-distributed-embedding``) coordination
        primitive. Lets N hosts drain the *same* embedder lane concurrently
        with zero duplicated GPU compute, by recording per-chunk
        reservations in ``corpus.embed_claims``.

        Contract (Postgres):

        1. **Self-heal first.** Opportunistically delete this embedder's
           stale claims (rows past ``lease_until``) so abandoned work from
           a dead worker becomes claimable again — no operator action.
        2. **Select from the missing-embeddings set.** Uses the *same*
           "missing embedding" definition as
           :meth:`chunks_missing_embedding` (shared SQL fragment so the
           two paths cannot drift), additionally excluding chunks with a
           live claim, and applying ``FOR UPDATE SKIP LOCKED`` so
           concurrent claimers skip each other's in-flight rows instead of
           blocking.
        3. **Insert claim rows** with ``lease_until = now + lease_ttl`` and
           ``ON CONFLICT (embedder_id, chunk_id) DO NOTHING`` — only the
           host whose insert actually lands works the chunk, making
           uniqueness races benign.
        4. **Return** the claimed chunks in the same
           ``(chunk_id, text, source_uri)`` shape
           :meth:`chunks_missing_embedding` yields, so the backfill loop
           can switch over transparently.

        ``extensions`` / ``after_id`` mirror
        :meth:`chunks_missing_embedding` for lane filtering and
        forward-progress cursoring.

        SQLite raises :class:`FederationUnsupported`.
        """
        ...

    def release_claims(
        self,
        embedder_id: int,
        host_id: str,
        chunk_ids: "list[int]",
    ) -> int:
        """Release this host's claims on ``chunk_ids`` for ``embedder_id``.

        Called after a batch is embedded (the embedding row is the durable
        record; the claim was only a reservation). Returns the number of
        claim rows deleted. SQLite raises :class:`FederationUnsupported`.
        """
        ...

    def expire_stale_claims(self, embedder_id: int | None = None) -> int:
        """Delete claims whose ``lease_until`` is in the past; return the count.

        Called opportunistically at the top of
        :meth:`claim_chunks_for_embedding` so a host that dies mid-batch
        has its reservations reclaimed automatically once the lease
        elapses. ``embedder_id=None`` sweeps every lane. SQLite raises
        :class:`FederationUnsupported`.
        """
        ...

    def count_stale_claims(self, embedder_id: int | None = None) -> int:
        """Count claims past ``lease_until`` *without* deleting them.

        The read-only counterpart to :meth:`expire_stale_claims` (which
        DELETEs). Backs the informational ``embed_claims`` doctor check:
        stale claims are self-healing — the next claim call sweeps them —
        so the doctor wants the *count* but must not mutate state during a
        diagnostic. ``embedder_id=None`` counts every lane. SQLite raises
        :class:`FederationUnsupported`.
        """
        ...

    def count_live_claims(
        self,
        embedder_id: int,
        exclude_host_id: str | None = None,
    ) -> int:
        """Count unexpired claims on ``embedder_id`` (RFC fleet-2).

        A claim is "live" when its ``lease_until`` is still in the future.
        ``exclude_host_id`` drops this host's own claims from the count so
        the embed backfill can compute a truthful progress total — the
        chunks *other* hosts have reserved are work this host will never
        do, so they're subtracted from the missing-embeddings total
        (floored at 0). SQLite raises :class:`FederationUnsupported`.
        """
        ...

    # --- Fleet 3 — federated config publish / pull ------------------------

    def get_shared_config(self) -> "tuple[int, dict] | None":
        """Return the corpus's shared config as ``(version, body)``, or None.

        Fleet-3 (RFC ``rfc-fleet-3-federated-config-and-setup``) read side.
        Returns ``None`` when no host has ever published (the table is
        empty), so a fresh fleet's first ``config publish`` passes
        ``expected_version=0``. Otherwise returns the current ``version``
        and the decoded ``body`` dict.

        SQLite raises :class:`FederationUnsupported`.
        """
        ...

    def put_shared_config(
        self,
        body: dict,
        expected_version: int,
        published_by: str,
    ) -> int:
        """Atomically publish ``body`` as the next shared-config version.

        Fleet-3 write side, optimistic-concurrency guarded. The caller
        passes ``expected_version`` — the version it last pulled (``0`` for
        the very first publish) — and the new version
        (``expected_version + 1``) is returned on success.

        The write is a single conditional statement:

        * ``expected_version == 0`` (first publish) →
          ``INSERT ... ON CONFLICT (corpus_id) DO NOTHING RETURNING version``;
          a row already present means another host published first, so no
          row comes back.
        * otherwise →
          ``UPDATE ... SET version = version + 1, ... WHERE corpus_id = 1
          AND version = %s RETURNING version``; the ``version = %s`` guard
          fails (zero rows) when the DB has moved past ``expected_version``.

        Either way, no returned row means the optimistic-concurrency check
        lost the race, and :class:`SharedConfigVersionConflict` is raised
        telling the operator to pull first. SQLite raises
        :class:`FederationUnsupported`.
        """
        ...

    # --- Phase L Wave 6 — embedder-fingerprint helpers --------------------

    def find_embedder_row_by_name(self, name: str) -> "dict | None":
        """Look up an ``embedders`` row by ``name`` for the fingerprint drift path."""
        ...

    def count_existing_embeddings(self, embedder: int | str) -> int:
        """Count embedding rows already written for the given embedder (by id or name)."""
        ...

    def update_embedder_config_blob(self, embedder: int | str, config_blob: dict) -> None:
        """Write a fresh ``embedders.config`` JSON blob (by id or name)."""
        ...

    # --- Multi-modal embedding surface (Phase G P1) ----------------------------

    def register_multimodal_embedder(
        self,
        *,
        name: str,
        model_id: str,
        dimension: int,
    ) -> int:
        """Register a multi-modal embedder and create its ``image_embeddings_<name>`` table.

        Mirrors :meth:`register_embedder` but flags the row as
        ``image=TRUE`` (Phase G migration 0011 added the column) and
        provisions a parallel ``image_embeddings_<name>`` table rather
        than ``embeddings_<name>``.
        """
        ...

    def write_image_embeddings(
        self,
        embedder_id: int,
        pairs: "list[tuple[int, list[float]]]",
    ) -> None:
        """Write image embeddings for chunks (Phase G P1).

        Same shape as :meth:`write_embeddings` but operates on the
        ``image_embeddings_<name>`` table and accepts plain
        ``list[float]`` vectors (no numpy dependency required at this
        boundary — the image-embed pipeline produces lists directly).
        """
        ...

    def image_chunks_missing_embedding(
        self, embedder_id: int, *, limit: int = 1024
    ) -> "Iterator[tuple[int, dict]]":
        """Return image-labeled chunks missing an embedding for ``embedder_id``.

        Yields ``(chunk_id, metadata_dict)`` so the caller can read
        ``metadata["image_uri"]`` (or however image bytes are sourced)
        from the dict rather than re-querying the chunks table.

        Filter: only chunks whose document carries ``format=image`` are
        considered (image-extractor output).
        """
        ...

    def lock_source(
        self, key: str
    ) -> "AbstractContextManager[None]": ...  # Context manager for advisory lock

    def delete_document(self, dataset_id: int, source_uri: str) -> None: ...

    def delete_conversation(self, dataset_id: int, source_uri: str) -> None: ...

    def resolve_document(self, dataset_id: int, source_uri: str) -> "dict | None": ...

    def resolve_self_source(self, dataset_id: int, host: str) -> int: ...

    def insert_revision(
        self,
        *,
        document_id: int,
        source_uri: str,
        content_hash: str,
        text: str,
        parent_revision_id: "int | None",
        author_host: str,
        is_tombstone: bool,
        metadata: "dict | None" = None,
    ) -> dict: ...

    def latest_revision(self, document_id: int) -> "dict | None": ...

    def pending_remote_revisions(
        self,
        dataset_id: int,
        last_pulled_revision_id: "int | None",
        self_host: str,
        *,
        limit: int = 1024,
    ) -> "list[dict]": ...

    def mark_revision_pulled(self, source_id: int, revision_id: int) -> None: ...

    def set_tombstone(self, document_id: int) -> None: ...

    def clear_tombstone(self, document_id: int) -> None: ...

    def get_or_create_dataset(self, name: str, kind: str, description: str) -> int: ...

    def find_dataset_id_by_name(self, name: str) -> "int | None": ...

    def register_source(self, dataset_id: int, plugin: str, identity: str, host: str) -> int: ...

    def append_enhancement_chunk(
        self,
        dataset_id: int,
        source_uri: str,
        text: str,
        *,
        title: str | None = None,
        heading: str | None = None,
        role: str | None = None,
        token_count: int | None = None,
        metadata: "dict | None" = None,
    ) -> "tuple[int, int]":
        """Append one synthetic chunk to a lazily-created host document.

        Used by the curation loop to *mint* new chunks (a captured
        conversation + recommended enhancement) rather than only editing
        existing ones.  The host document identified by
        ``(dataset_id, source_uri)`` is created on first use and reused
        thereafter; the new chunk is appended at the next ``chunk_index``.
        Returns ``(document_id, chunk_id)``.
        """
        ...

    # --- Retrieval surface (Phase R1) ----------------------------------------

    def search_dense(
        self,
        embedder_id: int,
        query_vector: "np.ndarray",
        *,
        k: int,
        dataset_id: int | None = None,
        chunk_ids: "frozenset[int] | None" = None,
    ) -> "list[Hit]":
        """Dense ANN search restricted to per-embedder + per-dataset.

        Phase N Wave 3 added the ``chunk_ids`` keyword for the static
        fast-tier shortcut mode — a non-None ``frozenset`` narrows the
        result to the candidate pool surfaced by the fast tier; empty
        means "filter to nothing"; ``None`` (default) preserves the
        pre-Wave-3 unfiltered behaviour.
        """
        ...

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
        chunk_ids: "frozenset[int] | None" = None,
    ) -> "list[Hit]":
        """Lexical (FTS) search.  Same ``chunk_ids`` semantics as
        :meth:`search_dense` (Phase N Wave 3)."""
        ...

    def get_chunk(self, chunk_id: int) -> "dict | None":
        """Return chunk row joined to documents + conversations (LEFT JOIN).

        Additive (agent-chunk-explorer): the returned dict ALSO includes
        ``prev_chunk_id`` and ``next_chunk_id`` (``int | None``) so the
        caller can chain follow-up lookups without an extra query.  For
        document chunks these are computed against ``(document_id,
        chunk_index)``; for conversation chunks against
        ``(conversation_id, message_id, chunk_index)``.  Existing keys
        are preserved.
        """
        ...

    def get_chunk_neighbors(
        self,
        chunk_id: int,
        *,
        before: int = 1,
        after: int = 1,
    ) -> "list[dict]":
        """Return up to ``before`` preceding + ``after`` following neighbor chunks.

        Same row shape as :meth:`get_chunk`. Ordered by ``chunk_index``
        ascending (for document chunks) or by ``(message_id,
        chunk_index)`` (for conversation chunks).  Does NOT include the
        anchor chunk itself.  Returns ``[]`` if the anchor doesn't
        exist.  ``before=0`` or ``after=0`` is valid.
        """
        ...

    def get_document_chunks(self, document_id: int) -> "list[dict]":
        """Return every chunk of a document ordered by ``chunk_index``.

        Same row shape as :meth:`get_chunk`. Returns ``[]`` if the
        document has no chunks or doesn't exist.  Distinct from
        :meth:`get_document_chunk_texts` (which returns only the text
        strings) — this surfaces every column the CLI/MCP layers need.
        """
        ...

    def get_chunk_by_content_hash(self, content_hash: str) -> "dict | None": ...

    def get_document_chunk_texts(self, document_id: int) -> "list[str]":
        """Return the texts of all chunks attached to ``document_id`` in order.

        Phase F (F-04): used by ``corpus-forge rechunk`` to compare the
        prospective new chunk-text list against the stored chunk-text
        list and skip the upsert when they match (idempotency check).

        Returns an empty list when the document has no chunks.
        """
        ...

    def get_document_chunk_metadatas(self, document_id: int) -> "list[dict]":
        """Return the metadata dicts of all chunks attached to ``document_id``.

        Phase F (F-04): used by the ``rechunk`` CLI idempotency check to
        detect when stored chunks lack the expected chunker signature
        (e.g. ``cdc_fingerprint`` for prose classes), in which case the
        rechunk pass runs even if the chunk text happens to match.
        """
        ...

    def replace_document_chunks(
        self,
        document_id: int,
        chunks: list[Any],
        embedder_ids: "list[int] | None" = None,
    ) -> int:
        """Replace the chunks of a document with ``chunks``, content-hash-aware.

        Phase F (F-04): used by the ``rechunk`` CLI. Mirrors the
        ``content_hash`` chunk-reuse path inside :meth:`upsert_document`
        WITHOUT touching the document row. Embedding rows for chunks
        whose ``content_hash`` survives the rechunk are preserved
        in-place (Phase C BUG-3).
        """
        ...

    def list_datasets(self) -> "list[dict]": ...

    def backfill_lexical_index(self) -> int: ...

    # --- Classification surface (Phase E) ----------------------------------

    # --- Code-enrichment surface (Phase H) ---------------------------------

    def iter_code_chunks_for_enrichment(
        self,
        model_tag: str,
        dataset_id: "int | None" = None,
    ) -> "Iterator[tuple[int, Any, str]]":
        """Yield ``(chunk_id, TextChunk, language)`` for code chunks to enrich.

        Iterates chunks whose parent document carries ``class=code``
        (the Phase E classifier output) AND whose
        ``metadata.enrichment.model`` does NOT equal ``model_tag``.
        That second filter is the idempotency guard — chunks already
        enriched with the current model tag are skipped so re-running
        the CLI is cheap.

        ``language`` resolution order (first non-empty wins):
        1. ``chunks.metadata.language`` (CodeChunker stamps this).
        2. ``documents.language`` document-label
           (``namespace='language'``).
        3. The string ``"unknown"`` as a final fallback.
        """
        ...

    def update_chunk_enrichment(
        self,
        chunk_id: int,
        enrichment: Any,
    ) -> None:
        """Merge ``enrichment.to_metadata()`` into ``chunks.metadata.enrichment``.

        Preserves existing chunk-metadata fields (``kind``, ``name``,
        ``byte_range``, ``cdc_fingerprint``) by writing only the
        ``enrichment`` sub-key — other keys are left untouched.
        """
        ...

    def iter_documents_for_classification(
        self,
        dataset_id: "int | None" = None,
        *,
        include_classified: bool = False,
    ) -> "Iterator[Any]":
        """Yield :class:`ClassifiableDocument` rows for the classifier chain.

        Read-only iterator joining ``documents`` to
        ``document_labels`` / ``labels`` so the caller sees the
        already-attached structural labels (``format``, ``language``,
        ``extractor``).

        Args:
            dataset_id: Restrict to a single dataset. ``None`` iterates
                every dataset.
            include_classified: When ``False`` (default), skip documents
                that already carry a ``namespace='class'`` label whose
                ``source LIKE 'classifier:%'``. User-attached class
                labels (``source='user'``) do NOT block iteration —
                the classifier writes its own source-distinct row.
        """
        ...

    # --- Ingest-run state (SR-G2) -------------------------------------------

    def start_ingest_run(
        self,
        *,
        run_id: str,
        host: str,
        pid: int,
        config_digest: str,
    ) -> None:
        """Insert a new ingest-run row with status='running'.

        On conflict (same run_id already exists — resume path), update
        status back to 'running' and bump last_progress_at instead of
        raising a unique-violation error.
        """
        ...

    def update_ingest_run(
        self,
        run_id: str,
        *,
        last_op: str | None = None,
        last_done: int | None = None,
        last_total: int | None = None,
    ) -> None:
        """Best-effort heartbeat. Implementations MUST swallow OperationalError and log at DEBUG."""
        ...

    def finish_ingest_run(
        self,
        run_id: str,
        *,
        status: "Literal['completed', 'interrupted', 'failed']",
        error: str | None = None,
    ) -> None:
        """Set ended_at, status, and optional error on the ingest-run row."""
        ...

    def latest_ingest_run(self) -> "dict | None":
        """Returns the row with the most-recent started_at (any status)."""
        ...

    def latest_unfinished_ingest_run(self, host: "str | None" = None) -> "dict | None":
        """Returns the most-recent row with status IN ('running','interrupted'); None otherwise.

        host=None (default) returns any unfinished row regardless of host (back-compat).
        host='X' adds AND host = 'X' to the WHERE clause.
        """
        ...

    def upsert_ingest_run_source(
        self,
        *,
        run_id: str,
        source_uri_prefix: str,
        dataset_id: int,
        last_scanned_at: "datetime | None" = None,
        docs_seen_delta: int = 0,
        docs_skipped_delta: int = 0,
        docs_failed_delta: int = 0,
        finished: bool = False,
    ) -> None:
        """UPSERT on (run_id, source_uri_prefix). Deltas ADD to existing counters.
        When finished=True, set finished_at = now().
        """
        ...

    def find_source_last_scanned_at(self, source_uri_prefix: str) -> "datetime | None":
        """Latest finished_at across any completed/interrupted run for this source_uri_prefix.
        Used by --resume + max-scan-age skip logic. Returns None if never scanned.
        """
        ...

    def mark_stale_runs(
        self,
        threshold_seconds: float,
        *,
        host: "str | None" = None,
    ) -> int:
        """Transition stale 'running' rows to 'failed'.

        A row is stale when ``now() - last_progress_at > threshold_seconds``.
        Only rows with ``status='running'`` are eligible; 'interrupted' rows are
        never touched (they are sticky for --resume).

        Args:
            threshold_seconds: Age in seconds beyond which a running row is
                considered dead. Values <= 0 are a no-op short-circuit (returns 0
                without hitting the DB).
            host: When not None, further restrict to rows whose ``host`` column
                matches this value.

        Returns:
            Number of rows transitioned to 'failed'.
        """
        ...

    # --- Fleet telemetry registry (rfc-fleet-1) -----------------------------

    def upsert_host(
        self,
        *,
        host_id: str,
        hostname: str,
        os: str,
        accelerator: "dict | None",
        tailscale_name: str | None = None,
    ) -> None:
        """UPSERT this host's row in the ``hosts`` registry (rfc-fleet-1).

        Keyed on ``host_id``.  ``accelerator`` is serialised to JSON by
        the backend (JSONB on Postgres, TEXT on SQLite).  ``last_seen``
        is set to ``now()`` on every call — the row doubles as a
        heartbeat.  Called once per process start (daemon startup, embed
        entry), never on a hot path.
        """
        ...

    def upsert_models(self, rows: "list[dict]") -> None:
        """Insert ``models`` registry rows, preserving ``first_seen`` (rfc-fleet-1).

        Each dict carries ``model_key`` (``"<provider>:<model_id>"``),
        ``kind``, ``provider``, ``model_id``, and an optional
        ``dimension`` (``None`` when unknown — e.g. ``ollama list`` LLM
        rows).  Insert uses ``ON CONFLICT (model_key) DO NOTHING`` so a
        model's original ``first_seen`` survives re-registration.  An
        empty ``rows`` list is a no-op.
        """
        ...

    def insert_model_benchmark(
        self,
        *,
        host_id: str,
        model_key: str,
        source: str,
        transport: str,
        device: str,
        batch_size: int | None,
        sample_chunks: int | None,
        chunks_per_s: float | None,
        tokens_per_s: float | None = None,
        latency_p50_ms: float | None = None,
        latency_p95_ms: float | None = None,
        cold_start_s: float | None = None,
    ) -> None:
        """Insert one ``model_benchmarks`` throughput sample (rfc-fleet-1).

        Append-only (the table's PK is a ``bigserial``); ``measured_at``
        is stamped to ``now()`` by the backend.  ``source`` is
        ``"bench"`` (active ``bench embed`` sample) or ``"embed-run"``
        (passive telemetry from a real backfill); ``transport`` is
        ``"local"`` / ``"api"`` and ``device`` the accelerator lane
        (``cuda`` / ``mps`` / ``cpu`` / ``remote``).  The optional
        latency / token columns stay ``None`` when not measurable for
        the lane.  ``cold_start_s`` is the model load + warmup wall clock
        (``bench`` measures it; the passive ``embed-run`` path leaves it
        ``None``).  Foreign keys point at :meth:`upsert_host` /
        :meth:`upsert_models` rows, so callers heartbeat first.
        """
        ...

    def list_models_with_latest_benchmark(self) -> "list[dict]":
        """Return ``models`` rows joined to the LATEST benchmark per host (rfc-fleet-1).

        Powers ``corpus-forge models list``.  The base of the join is the
        ``models`` registry — every registered model appears at least once
        even when it has no benchmark yet (those rows carry ``host_id =
        None`` and ``None`` metrics).  When a model *has* benchmark rows,
        one row is emitted per ``(host_id, model_key)`` carrying only the
        *most recent* sample for that pair (older samples are dropped via a
        ``ROW_NUMBER() OVER (PARTITION BY host_id, model_key ORDER BY
        measured_at DESC)`` window — portable across Postgres and SQLite
        and served by the 0018 ``(host_id, model_key, measured_at DESC)``
        index).

        Each dict carries the model registry columns (``model_key``,
        ``kind``, ``provider``, ``model_id``, ``dimension``) plus the
        latest-benchmark columns (``host_id``, ``chunks_per_s``,
        ``transport``, ``device``, ``source``, ``measured_at``) — the
        latter all ``None`` for a model with no benchmark.  Rows are
        ordered by ``model_key`` then ``host_id`` for a stable render.
        """
        ...

    def list_hosts_with_latest_rate(self) -> "list[dict]":
        """Return ``hosts`` rows + each host's freshest aggregate rate (rfc-fleet-1).

        Powers ``corpus-forge hosts list``.  The base is the ``hosts``
        registry so a host with zero benchmarks still appears (``models``
        / ``latest_chunks_per_s`` / ``latest_measured_at`` are ``None``).
        ``latest_chunks_per_s`` is the ``chunks_per_s`` of that host's
        single most-recent benchmark row across all of its models (the
        "what's the freshest number I measured on this box" headline);
        ``models`` is the count of distinct ``model_key`` values that host
        has ever benchmarked.

        Each dict carries the host columns (``host_id``, ``hostname``,
        ``os``, ``accelerator``, ``last_seen``) plus the aggregate columns
        (``models``, ``latest_chunks_per_s``, ``latest_measured_at``).
        ``accelerator`` is returned as the backend stored it — a ``dict``
        on Postgres (JSONB), a JSON ``str`` on SQLite — and the view layer
        normalises it.  Rows are ordered by ``last_seen`` descending so
        the most-recently-seen host renders first.
        """
        ...

    def model_benchmark_stats(self) -> "dict":
        """Return ``{"count": int, "freshest": <measured_at | None>}`` (rfc-fleet-1).

        Powers the informational ``model_telemetry`` doctor check: the
        total number of ``model_benchmarks`` rows and the single most
        recent ``measured_at`` across all of them (``None`` when the table
        is empty).  ``freshest`` is the backend's native timestamp type
        (a ``datetime`` on Postgres, an ISO ``str`` on SQLite) — doctor
        renders the age from it.  An empty table returns
        ``{"count": 0, "freshest": None}``.
        """
        ...
