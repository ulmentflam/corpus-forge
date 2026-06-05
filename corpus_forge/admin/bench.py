"""``corpus-forge bench embed`` — active embedder throughput sampling (rfc-fleet-1).

This is item 4 of ``rfc-fleet-1-model-telemetry-and-bench``: a *very
small* calibration run that pushes a handful of chunks through each
configured embedder and records one ``corpus.model_benchmarks`` row per
embedder (``source="bench"``).  The companion passive telemetry
(``source="embed-run"``) lives in :mod:`corpus_forge.embed`; both write
the same table so ``models list`` (a later task) can compare lanes.

Sampling policy — the load-bearing rule the RFC pins by tests:

* **Prefer real pending chunks.**  When the lane has a backlog (chunks
  with no embedding row for this embedder), we sample up to ``--sample``
  of them and **persist** their vectors via ``backend.write_embeddings``.
  The benchmark doubles as real work — the chunks come off the pending
  pile, so the operator pays the round-trip once.
* **Fall back to a deterministic synthetic sample.**  When the lane is
  fully embedded, we generate ``--sample`` seeded synthetic texts of
  varied length and encode them **without persisting** anything — there
  are no chunk ids to attach the vectors to, and writing junk rows would
  corrupt the corpus.  :func:`synthetic_sample` is deterministic so two
  runs on the same host produce comparable numbers.

Metrics:

* ``chunks_per_s`` — sample size over per-batch wall clock.
* ``tokens_per_s`` — only when a cheap tokenizer is *already* attached to
  the embedder (sentence-transformers ships one via ``_model.tokenizer``;
  llama-cpp exposes ``tokenize``).  We never pull a new dependency for
  this, so it is ``None`` for providers without a reachable tokenizer.
* ``latency_p50_ms`` / ``latency_p95_ms`` — per-request round-trip
  latency, recorded **only** for ``transport="api"`` embedders where the
  number is meaningful (each text is encoded individually so we have a
  distribution).  ``None`` for local embedders (batched, single wall
  clock).

Transport / device tagging:

* ``transport`` is ``"api"`` when the embedder config resolves a remote
  ``base_url`` (OpenAI-compatible providers), else ``"local"``.
* ``device`` is ``"remote"`` for API transports, otherwise the
  accelerator lane from :func:`corpus_forge.acceleration.detect_accelerator`
  (``cuda`` / ``mps`` / ``cpu``).

Per-embedder failure isolation: one embedder failing to load or encode
records an error row in the report and the run continues with the
others.  The process exits non-zero only when **every** target failed.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Annotated, Any

import typer
from rich.table import Table

from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error

logger = logging.getLogger(__name__)

bench_app = typer.Typer(
    help="Benchmark embedder throughput and record telemetry (rfc-fleet-1).",
    add_completion=False,
)


# ── Tunables ──────────────────────────────────────────────────────────────

#: Default sample size — kept tiny so ``bench embed --all`` is a quick
#: post-setup calibration, not a load test.  Matches the RFC default.
_DEFAULT_SAMPLE: int = 64
# Width of a `chunks_missing_embedding` row since PR #81 widened it to
# (chunk_id, text, source_uri).  Mirrors `_CHUNKS_MISSING_TUPLE_WIDTH`
# in `corpus_forge/embed.py` — kept local so the verb module doesn't
# import the (heavy) backfill module for one integer.
_CHUNKS_MISSING_TUPLE_WIDTH: int = 3

#: Word pool for the deterministic synthetic fallback.  A fixed list (no
#: randomness beyond the seeded index walk) keeps :func:`synthetic_sample`
#: byte-for-byte reproducible across runs and hosts.
_SYNTHETIC_WORDS: tuple[str, ...] = (
    "corpus",
    "vector",
    "embedding",
    "latency",
    "throughput",
    "telemetry",
    "fleet",
    "benchmark",
    "tailscale",
    "postgres",
    "chunk",
    "document",
    "pipeline",
    "dimension",
    "model",
    "device",
)


# ── Result dataclasses ────────────────────────────────────────────────────


@dataclass(frozen=True)
class BenchResult:
    """One embedder's benchmark outcome.

    ``error`` is ``None`` on success and a short message when the
    embedder failed to load / encode (the row is still reported so the
    operator sees which lane is broken).  ``persisted`` records whether
    the sampled vectors were written back (real-pending path) or
    discarded (synthetic fallback) — surfaced so the persisted-vs-not
    rule is observable by callers and tests.
    """

    embedder_name: str
    model_key: str
    transport: str
    device: str
    sample_chunks: int
    chunks_per_s: float | None
    tokens_per_s: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    source_kind: str  # "real-pending" | "synthetic" | "none"
    persisted: bool
    error: str | None = None


@dataclass(frozen=True)
class BenchReport:
    """Outcome of one :func:`bench_embedders` invocation.

    ``results`` is sorted by ``chunks_per_s`` descending (errored / unrun
    embedders sink to the bottom).  ``all_failed`` is ``True`` when every
    target embedder errored — the CLI maps it to a non-zero exit.
    """

    host_id: str
    results: list[BenchResult] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        return bool(self.results) and all(r.error is not None for r in self.results)


# ── Synthetic fallback corpus ─────────────────────────────────────────────


def synthetic_sample(n: int) -> list[str]:
    """Return ``n`` deterministic synthetic texts of varied length.

    No RNG and no external dependency — the i-th text is built by walking
    the fixed :data:`_SYNTHETIC_WORDS` pool with an index derived from a
    stable hash of ``i``.  Length varies cyclically from ~4 to ~24 words
    so the encoder sees a realistic spread rather than uniform input.
    Used only when an embedder lane has no real pending backlog; the
    resulting vectors are **never** persisted.
    """

    out: list[str] = []
    pool = _SYNTHETIC_WORDS
    pool_len = len(pool)
    for i in range(max(0, n)):
        # Varied length: 4..23 words, cycling deterministically.
        length = 4 + (i % 20)
        # Stable per-text offset so two texts at the same length still
        # differ in content (and across hosts the bytes are identical).
        digest = hashlib.sha256(str(i).encode("utf-8")).digest()
        start = digest[0] % pool_len
        words = [pool[(start + j) % pool_len] for j in range(length)]
        out.append(" ".join(words))
    return out


# ── Transport / device tagging ────────────────────────────────────────────


def resolve_transport(embedder_config: Any) -> str:
    """Return ``"api"`` when the embedder resolves a remote base URL else ``"local"``.

    ``EmbedderConfig.base_url`` is populated only for OpenAI-compatible
    (remote) providers — its presence is the canonical local/API signal
    the RFC calls out.  Probed via ``getattr`` so test doubles need only
    expose the attribute they care about.
    """

    base_url = getattr(embedder_config, "base_url", None)
    return "api" if base_url else "local"


def resolve_device(transport: str) -> str:
    """Return the device tag for a benchmark row.

    API transports run on a remote box, so the local accelerator probe is
    irrelevant — tag them ``"remote"``.  Local transports report the
    detected accelerator lane (``cuda`` / ``mps`` / ``cpu``); a probe
    failure degrades to ``"cpu"`` rather than breaking the bench.
    """

    if transport == "api":
        return "remote"
    try:
        from corpus_forge.acceleration import detect_accelerator

        return str(detect_accelerator().kind)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("bench: accelerator probe failed (%r); tagging device=cpu", exc)
        return "cpu"


# ── Tokenizer probe (best-effort, dep-free) ───────────────────────────────


def count_tokens(embedder: Any, texts: list[str]) -> int | None:
    """Best-effort token count across ``texts`` using an *already-present* tokenizer.

    Returns ``None`` when no cheap tokenizer is reachable — we never pull
    a new dependency just to populate ``tokens_per_s``.  Two known shapes
    are probed:

    * sentence-transformers — ``embedder._model.tokenizer`` is a HF
      ``PreTrainedTokenizer`` whose ``__call__`` / ``encode`` returns
      token ids.
    * llama-cpp — the embedder exposes ``tokenize(bytes) -> list[int]``.

    Any probe failure is swallowed (logged at debug) and yields ``None``;
    a missing tokens metric must never break a benchmark.
    """

    try:
        # sentence-transformers: the wrapped SentenceTransformer carries a
        # ``.tokenizer`` HF object.
        model = getattr(embedder, "_model", None)
        hf_tok = getattr(model, "tokenizer", None) if model is not None else None
        if hf_tok is None:
            hf_tok = getattr(embedder, "tokenizer", None)
        hf_encode: Any = getattr(hf_tok, "encode", None) if hf_tok is not None else None
        if callable(hf_encode):
            total = 0
            for text in texts:
                ids: Any = hf_encode(text)
                total += len(ids)
            return total

        # llama-cpp: tokenize(bytes) on the embedder itself.
        tokenize: Any = getattr(embedder, "tokenize", None)
        if callable(tokenize):
            total = 0
            for text in texts:
                tok_ids: Any = tokenize(text.encode("utf-8"))
                total += len(tok_ids)
            return total
    except Exception as exc:
        logger.debug("bench: token count probe failed (%r); tokens_per_s=None", exc)
        return None

    return None


# ── Percentile helper ─────────────────────────────────────────────────────


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted, non-empty list.

    ``pct`` is in ``[0, 100]``.  Kept dependency-free (no numpy) so the
    bench module imports cheaply even on a minimal install.
    """

    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


# ── Core: bench one embedder ───────────────────────────────────────────────


def _sample_pending(
    backend: Any, embedder: Any, embedder_id: int, sample: int
) -> list[tuple[int, str]]:
    """Return up to ``sample`` real pending ``(chunk_id, text)`` pairs for this lane.

    Reuses the same ``chunks_missing_embedding`` query path
    :func:`corpus_forge.embed.backfill_embedder` walks, including the
    extension allow-list so a specialist embedder samples its own chunks.
    Rows are routed through :func:`corpus_forge.embedders.routing.route_for`
    (defense-in-depth, matching the backfill loop) so a catchall doesn't
    claim specialist-owned chunks.  Returns ``[]`` when the lane has no
    backlog — the caller then falls back to the synthetic sample.
    """

    ext_filter: list[str] | None = list(embedder.extensions) if embedder.extensions else None
    try:
        raw_rows = list(
            backend.chunks_missing_embedding(
                embedder_id,
                limit=sample,
                extensions=ext_filter,
            )
        )
    except Exception as exc:
        logger.debug("bench: chunks_missing_embedding failed (%r); using synthetic sample", exc)
        return []

    if not raw_rows:
        return []

    # Defensive: PR #81 widened the tuple to 3 (chunk_id, text, source_uri).
    # If a stub yields 2-tuples, treat them as catchall-claimed.
    from corpus_forge.embedders.routing import route_for

    pairs: list[tuple[int, str]] = []
    for row in raw_rows:
        if len(row) >= _CHUNKS_MISSING_TUPLE_WIDTH:
            cid, text, src_uri = row[0], row[1], row[2]
        else:
            cid, text, src_uri = row[0], row[1], ""
        # Single-embedder route check: route against just this embedder so
        # a catchall keeps everything and a specialist keeps its own.
        if route_for(src_uri, [embedder]) is embedder:
            pairs.append((cid, text))
        if len(pairs) >= sample:
            break
    return pairs


def bench_one(
    backend: Any,
    embedder_config: Any,
    *,
    host_id: str,
    sample: int,
) -> BenchResult:
    """Benchmark a single embedder and write its ``model_benchmarks`` row.

    Loads the embedder via the shared ``register_from_config`` gate
    (matching ingest/search/embed), samples real-pending-first with a
    synthetic fallback, times the encode, computes the metrics, persists
    real-pending vectors (never synthetic ones), and inserts one
    ``source="bench"`` row.  All failures collapse into a
    :class:`BenchResult` with ``error`` set — the caller continues with
    the next embedder.
    """

    name = getattr(embedder_config, "name", "<unknown>")
    provider = getattr(embedder_config, "provider", "<unknown>")
    model_id = getattr(embedder_config, "model_id", "<unknown>")
    model_key = f"{provider}:{model_id}"
    transport = resolve_transport(embedder_config)
    device = resolve_device(transport)

    def _error(msg: str) -> BenchResult:
        return BenchResult(
            embedder_name=name,
            model_key=model_key,
            transport=transport,
            device=device,
            sample_chunks=0,
            chunks_per_s=None,
            tokens_per_s=None,
            latency_p50_ms=None,
            latency_p95_ms=None,
            source_kind="none",
            persisted=False,
            error=msg,
        )

    try:
        from corpus_forge.embedders.registry import register_from_config, registry

        embedder = register_from_config(registry, embedder_config)
        embedder.warmup()
        embedder_id = backend.register_embedder(embedder)
    except Exception as exc:
        logger.warning("bench: embedder %r failed to load: %r", name, exc)
        return _error(f"load failed: {exc}")

    # Sample: real pending first, synthetic fallback.
    pending = _sample_pending(backend, embedder, embedder_id, sample)
    if pending:
        source_kind = "real-pending"
        chunk_ids = [cid for cid, _ in pending]
        texts = [text for _, text in pending]
    else:
        source_kind = "synthetic"
        chunk_ids = []
        texts = synthetic_sample(sample)

    if not texts:
        return _error("no chunks to sample (empty corpus and sample<=0)")

    # Time the encode.  For API transports we encode per-text so we can
    # build a latency distribution; for local transports a single batched
    # wall clock is the meaningful number.
    latencies_ms: list[float] = []
    try:
        if transport == "api":
            vectors: list[Any] = []
            t0 = time.perf_counter()
            for text in texts:
                r0 = time.perf_counter()
                vec = embedder.encode([text])
                latencies_ms.append((time.perf_counter() - r0) * 1000.0)
                vectors.append(vec[0])
            elapsed = time.perf_counter() - t0
            embeddings: Any = vectors
        else:
            t0 = time.perf_counter()
            embeddings = embedder.encode(texts)
            elapsed = time.perf_counter() - t0
    except Exception as exc:
        logger.warning("bench: embedder %r failed to encode: %r", name, exc)
        return _error(f"encode failed: {exc}")

    n = len(texts)
    chunks_per_s = (n / elapsed) if elapsed > 0 else None

    token_total = count_tokens(embedder, texts)
    tokens_per_s = (token_total / elapsed) if (token_total is not None and elapsed > 0) else None

    p50 = p95 = None
    if latencies_ms:
        ordered = sorted(latencies_ms)
        p50 = _percentile(ordered, 50.0)
        p95 = _percentile(ordered, 95.0)

    # Persist real-pending vectors only — the work counts.  Synthetic
    # vectors have no chunk ids and are NEVER written.
    persisted = False
    if source_kind == "real-pending" and chunk_ids:
        try:
            pairs = list(zip(chunk_ids, embeddings, strict=True))
            backend.write_embeddings(embedder_id, pairs)
            persisted = True
        except Exception as exc:
            # A persist failure must not lose the benchmark numbers we
            # already measured — log and report the row anyway.
            logger.warning("bench: write_embeddings for %r failed (%r); benchmark kept", name, exc)

    # Write the benchmark row (failure-isolated — a telemetry write never
    # breaks the bench's reported numbers).
    try:
        backend.insert_model_benchmark(
            host_id=host_id,
            model_key=model_key,
            source="bench",
            transport=transport,
            device=device,
            batch_size=getattr(embedder_config, "batch_size", None),
            sample_chunks=n,
            chunks_per_s=chunks_per_s,
            tokens_per_s=tokens_per_s,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
        )
    except Exception as exc:
        logger.warning("bench: insert_model_benchmark for %r failed (continuing): %r", name, exc)

    return BenchResult(
        embedder_name=name,
        model_key=model_key,
        transport=transport,
        device=device,
        sample_chunks=n,
        chunks_per_s=chunks_per_s,
        tokens_per_s=tokens_per_s,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        source_kind=source_kind,
        persisted=persisted,
        error=None,
    )


# ── Orchestration ─────────────────────────────────────────────────────────


def _select_targets(config: Any, embedders: list[str] | None, all_: bool) -> list[Any]:
    """Resolve the list of embedder configs to benchmark.

    ``--all`` benchmarks every configured embedder; otherwise the named
    ``-e`` embedders are looked up (unknown names raise ``ValueError`` so
    the CLI surfaces a clear usage error).  With neither flag we default
    to the active embedders — the common "calibrate what I run" case.
    """

    configured = list(getattr(config, "embedders", []) or [])
    by_name = {ec.name: ec for ec in configured}

    if all_:
        return configured
    if embedders:
        targets: list[Any] = []
        for name in embedders:
            ec = by_name.get(name)
            if ec is None:
                raise ValueError(f"embedder {name!r} not found in config")
            targets.append(ec)
        return targets
    # Default: active embedders (fall back to all if none flagged active).
    active = [ec for ec in configured if getattr(ec, "active", True)]
    return active or configured


def bench_embedders(
    backend: Any,
    config: Any,
    *,
    embedders: list[str] | None = None,
    all_: bool = False,
    sample: int = _DEFAULT_SAMPLE,
) -> BenchReport:
    """Benchmark the selected embedders and return a sorted :class:`BenchReport`.

    Heartbeats the host + model registry first (so the benchmark rows'
    foreign keys resolve), benches each target with per-embedder failure
    isolation, then sorts results by ``chunks_per_s`` descending.
    """

    from corpus_forge.telemetry_registry import heartbeat

    # FK targets must exist before we insert benchmark rows.
    heartbeat(backend, config)
    host_id = config.host_id()

    targets = _select_targets(config, embedders, all_)
    results: list[BenchResult] = []
    for ec in targets:
        results.append(bench_one(backend, ec, host_id=host_id, sample=sample))

    # Sort by chunks/s desc; errored / unmeasured rows (None) sink to the
    # bottom via a -inf sort key.
    def _key(r: BenchResult) -> float:
        return r.chunks_per_s if r.chunks_per_s is not None else float("-inf")

    results.sort(key=_key, reverse=True)
    return BenchReport(host_id=host_id, results=results)


# ── Rendering ─────────────────────────────────────────────────────────────


def _fmt(value: float | None, *, digits: int = 1) -> str:
    """Render an optional float for the Rich table; ``None`` → ``"—"``."""

    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def render_table(report: BenchReport) -> Table:
    """Build the Rich comparison table (sorted by chunks/s desc)."""

    table = Table(title="Embedder benchmark (source=bench)", show_header=True)
    table.add_column("Embedder", style="accent.path")
    table.add_column("Model", style="muted")
    table.add_column("Transport")
    table.add_column("Device")
    table.add_column("chunks/s", justify="right", style="accent.number")
    table.add_column("tokens/s", justify="right", style="accent.number")
    table.add_column("p50 ms", justify="right")
    table.add_column("p95 ms", justify="right")
    table.add_column("n", justify="right")
    for r in report.results:
        if r.error is not None:
            table.add_row(
                r.embedder_name,
                r.model_key,
                r.transport,
                r.device,
                f"[error]{r.error}[/error]",
                "—",
                "—",
                "—",
                "0",
            )
            continue
        table.add_row(
            r.embedder_name,
            r.model_key,
            r.transport,
            r.device,
            _fmt(r.chunks_per_s),
            _fmt(r.tokens_per_s),
            _fmt(r.latency_p50_ms),
            _fmt(r.latency_p95_ms),
            str(r.sample_chunks),
        )
    return table


def report_to_dict(report: BenchReport) -> dict[str, Any]:
    """Serialise a :class:`BenchReport` to a single JSON-able object."""

    return {
        "host_id": report.host_id,
        "all_failed": report.all_failed,
        "results": [
            {
                "embedder": r.embedder_name,
                "model_key": r.model_key,
                "transport": r.transport,
                "device": r.device,
                "sample_chunks": r.sample_chunks,
                "chunks_per_s": r.chunks_per_s,
                "tokens_per_s": r.tokens_per_s,
                "latency_p50_ms": r.latency_p50_ms,
                "latency_p95_ms": r.latency_p95_ms,
                "source_kind": r.source_kind,
                "persisted": r.persisted,
                "error": r.error,
            }
            for r in report.results
        ],
    }


# ── CLI verb ───────────────────────────────────────────────────────────────


def _build_backend(config: Any) -> Any:
    """Return a backend instance for the configured kind (postgres / sqlite)."""

    kind = getattr(config.backend, "kind", "postgres")
    if kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    elif kind == "postgres":
        from corpus_forge.backends.postgres import PostgresBackend

        backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {kind}")
    backend.migrate()
    return backend


@bench_app.command("embed")
def cmd_embed(
    sample: Annotated[
        int,
        typer.Option(
            "--sample", help="Number of chunks to push through each embedder (default 64)."
        ),
    ] = _DEFAULT_SAMPLE,
    embedder: Annotated[
        list[str] | None,
        typer.Option(
            "-e",
            "--embedder",
            help="Embedder name to benchmark (repeatable). Default: active embedders.",
        ),
    ] = None,
    all_: Annotated[
        bool,
        typer.Option("--all", help="Benchmark every configured embedder."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of the Rich table (agent mode)."),
    ] = False,
) -> None:
    """Sample-benchmark embedders and record ``model_benchmarks`` rows.

    Prefers real pending chunks (their vectors are persisted — the work
    counts) and falls back to a deterministic synthetic sample (never
    persisted) when a lane is fully embedded.  Writes one
    ``source="bench"`` row per embedder and prints a comparison table
    sorted by chunks/s.  Exit code is non-zero only when *every* target
    embedder failed.
    """

    import json as _json

    from corpus_forge.config import Config
    from corpus_forge.ui import agent as ui_agent

    # Agent mode: ``--json`` forces it; otherwise honour the ambient
    # detection the other verbs use.
    agent_mode = json_out or ui_agent.is_agent_mode()

    try:
        config = Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run `corpus-forge setup` to create one.")
        raise typer.Exit(code=2) from None

    try:
        backend = _build_backend(config)
    except Exception as exc:
        ui_error(f"Could not reach backend: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        report = bench_embedders(
            backend,
            config,
            embedders=list(embedder) if embedder else None,
            all_=all_,
            sample=sample,
        )
    except ValueError as exc:
        # Unknown embedder name → usage error (exit 2).
        ui_error(str(exc))
        raise typer.Exit(code=2) from None
    finally:
        closer = getattr(backend, "close", None)
        if callable(closer):
            import contextlib

            with contextlib.suppress(Exception):  # pragma: no cover — defensive
                closer()

    if agent_mode:
        print(_json.dumps(report_to_dict(report), indent=2, default=str))
    else:
        ui_console.print(render_table(report))

    if report.all_failed:
        raise typer.Exit(code=1)


__all__ = [
    "BenchReport",
    "BenchResult",
    "bench_app",
    "bench_embedders",
    "bench_one",
    "count_tokens",
    "render_table",
    "report_to_dict",
    "resolve_device",
    "resolve_transport",
    "synthetic_sample",
]
