# Changelog

All notable changes to **corpus-forge** are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/)
version numbers (so `0.1.0b1` is the first beta of the `0.1.0` line).

## [Unreleased]

### Added

- **`setup --join` seeds `[service] embed_drain` for the joined host
  (rfc-fleet-5 item 3)** — the joined host's rendered `config.toml` gets a
  `[service]` block with `embed_drain = true` (the managed service drains
  the shared backlog) and a GPU-aware `ingest_watch` default — off on a
  capable GPU box (pure-drain), on otherwise. New `setup` flags
  `--embed-drain/--no-embed-drain` and `--ingest-watch/--no-ingest-watch`
  (env `CF_EMBED_DRAIN` / `CF_INGEST_WATCH`) override the defaults. A plain
  local (non-`--join`) setup is unchanged — no `[service]` block, drain
  stays off (no surprise background GPU loop on a laptop). Builds on the
  item-2 schema/wiring above.
- **Managed embed-drain daemon (rfc-fleet-5 item 2)** — the background
  daemon can now continuously drain the embedding backlog, and its ingest
  watcher is optional.
  - New `[service]` config block (`ServiceConfig`): `embed_drain`
    (default `False`) and `ingest_watch` (default `True`) toggles, plus
    `[embed] drain_idle_min` / `drain_idle_max` (default 5s / 300s,
    validated `max >= min`, both > 0) for the `EmbedDrainLoop` idle-backoff
    window.
  - **Daemon wiring (item 2b):** when `[service] embed_drain` is on and the
    backend is Postgres, `run_daemon` starts the merged `EmbedDrainLoop`
    (#123) on a daemon thread so this host drains the backlog continuously
    (fleet-2 claims dedupe the work across the fleet). The filesystem ingest
    watcher is now gated on `[service] ingest_watch`, so a pure-drain GPU
    box (`embed_drain=true, ingest_watch=false`) only embeds and never walks
    source roots. Drain on a non-Postgres backend is intentionally skipped
    (no `corpus.embed_claims` coordination); a broken drain wiring is logged
    and swallowed so it can never take down the ingest daemon.
  - **Backcompat:** the defaults (`embed_drain=False` / `ingest_watch=True`)
    reproduce today's ingest-only daemon byte-for-byte; a config with no
    `[service]` block validates and behaves unchanged.

**Hotfix on top of the distributed-fleet release.** Fixes a real-world
fleet-2 deadlock observed on the operator's two-host Mac + Windows
5090 setup pointed at a remote Postgres LXC over Tailscale. The
underlying bug was a write-amplification regression (older than fleet-2)
that fleet-2's `embed_claims` paper trail made *visible*: on real
distributed setups the embed-worker would wedge after the first
"Generating embeddings for 1000 chunks" log line with claims held but
zero rows written. Also collects two follow-up items filed as issues.

### Fixed

- **`PostgresBackend.write_embeddings` now batches into one
  `executemany` under one connection checkout + COMMIT** (PR #124).
  The previous implementation did one `_execute` per
  `(chunk_id, embedding)` pair → ~1001 pool checkouts per 1000-pair
  batch. Over Tailscale to a remote Postgres LXC (~14 ms/checkout),
  each batch became a 14-second pool-contention storm. Combined with
  the fleet-2 claim loop's per-batch claim/release cycle, this
  produced the wedge symptom the operator observed: 0.2% CPU, all
  threads in `__psynch_cvwait`, `corpus.embed_claims` accumulating
  unembedded claims, `psycopg "another command is already in
  progress"` warnings on shutdown. The fix preserves the per-row
  `ON CONFLICT (chunk_id) DO NOTHING` semantics — `executemany`
  runs the same INSERT N times under one txn so the guard still
  fires per row. SQLite backend untouched (no Tailscale latency, no
  pool, cheap single-machine path).

  Per-host write pressure on Postgres drops ~50×. This bug existed
  in the legacy `chunks_missing_embedding` fallback path too; fleet-2
  didn't introduce the regression but surfaced it. Single-host
  setups also benefit — any operator with non-trivial Postgres RTT
  was paying this cost silently.

  See `.planning/tdd/fleet2_claim_deadlock_investigation.md` for the
  full diagnostic writeup, hypothesis reconciliation, and the
  RED-anchor integration test
  (`tests/integration/test_postgres_write_embeddings_batched.py`)
  that wraps the connection pool with a checkout counter and asserts
  ≤ 4 checkouts for a 500-pair write. Pre-fix: 201 checkouts; post-fix:
  2.

### Docs

- **RFC: `bench embed` progress + cold-start handling** (PR #123) —
  operator-requested refinement of the bench surface based on the
  multi-minute model-load + first-batch latency we observed in the
  field. Lays out incremental progress reporting and warm-state vs
  cold-load attribution.

### Known follow-ups (filed)

- **#125** — _Guards / throttling to prevent embed-workers from
  saturating the Postgres host (fleet-2)._ The operator's Postgres LXC
  (4 vCPU / 16 GiB RAM / 512 MiB swap) hit 97% CPU, 98% RAM, 100% swap
  during the two-host fleet drain — Tailscale connections began timing
  out, the daemon's `pg_stat_activity` queries queued behind autovacuum,
  and the LXC needed a reboot. The deadlock fix in #124 collapses
  ~3000 pool round-trips per batch to ~3, so per-worker peak write
  pressure drops ~50× — but the *steady-state* CPU + memory cost of N
  concurrent claim workers all hitting the same Postgres still has no
  client-side guard. Issue #125 captures the missing pieces:
  `pg_stat`-based backpressure, per-host concurrency cap,
  pool-size-vs-`max_connections` doctor check, configurable
  `claim_batch_size`, and a "size your fleet" docs callout in
  CLAUDE.md / AGENTS.md.

- **PR #120** — _`config pull --apply` writes malformed TOML when
  rewriting an existing inline `datasets = []` array._ Observed during
  Windows-host onboarding: `setup --join` renders a skeleton config
  with `datasets = []`; the next `config pull --apply` substitutes
  table-array content into the inline-array slot without inserting
  `{ }` braces or `,` between key-value pairs. Operator workaround:
  hand-edit the embedder blocks out of the dry-run diff instead of
  `--apply`. Fix is in `merge_shared_scope` (tomlkit array-of-tables
  detection).

### CI / Tests

- **Test version pins refreshed to `0.1.0b18`** —
  `tests/unit/test_phase_ci3_pyproject.py::test_version_is_beta`,
  `test_phase_ci3_wheel_metadata.py::test_wheel_filename`, and
  `test_metadata_version` hard-code the expected version string. Each
  release that bumps `pyproject.toml` must bump these too or the
  release-CI gate fails (and the publish-to-PyPI step never runs —
  this is what happened to b17's first tag).

## [0.1.0b17] - 2026-06-08

**The distributed-fleet release.** corpus-forge is no longer a
single-machine tool. The four "fleet" RFCs land together — model and
host telemetry (fleet-1), distributed embedding via claim-based
backfill (fleet-2), federated config + one-command host onboarding
(fleet-3), and Tailscale-native addressing (fleet-4) — so a second
box (a Linux GPU rig, a Windows machine with a 5090, a spare Mac mini)
can join an existing corpus, drain a specific embedder lane, and never
duplicate work with the primary.

### Fleet-1 — Model & host telemetry, `bench embed`

- **New schema** (alembic revision `0018`): `corpus.hosts`,
  `corpus.models`, `corpus.model_benchmarks`. Every host that has ever
  touched the corpus has a row; every embedder, LLM, VLM, and Whisper
  model the fleet has seen has a row; every benchmark and every real
  embed-run drops a row tagged with host, transport (`local` / `api`),
  device (`cuda` / `mps` / `cpu`), batch size, chunks/s, and p50/p95
  latency. The data accumulates passively — `embed.py::backfill_embedder`
  writes a `model_benchmarks` row at end-of-run and per-checkpoint, so
  even a crashed run reports what it managed (PRs #96, #97).
- **`corpus-forge bench embed [--all | -e name…] [--sample N] [--json]`** —
  active sampling pass. Real pending chunks first (vectors persisted),
  deterministic synthetic fallback when the lane has no backlog
  (vectors never persisted). Rich table sorted by chunks/s; `--json`
  for agents (PR #97).
- **`corpus-forge models list`** and **`corpus-forge hosts list`** —
  read verbs over the new tables. Latest benchmark per (host, model),
  staleness hint, accelerator summary. Both `--json` (PR #98).
- **Doctor `model_telemetry` informational check** — `OK` with
  "n benchmarks, freshest X ago" or "no benchmarks yet — run
  `corpus-forge bench embed`" (PR #98).
- **Setup wizard hint** — both interactive and `CF_NON_INTERACTIVE`
  paths now end by suggesting `corpus-forge bench embed --all` as the
  post-setup calibration step (PR #98).

### Fleet-2 — Distributed embedding via claim-based backfill

- **New schema** (alembic revision `0019`): `corpus.embed_claims`
  with `(embedder_id, chunk_id)` unique constraint and a lease-expiry
  index. Lease TTL configurable via `[embed] claim_lease_ttl` (default
  600 s) (PR #100).
- **Backend primitives** on Postgres: `claim_chunks_for_embedding`
  uses `FOR UPDATE SKIP LOCKED` so N hosts can drain the **same**
  embedder concurrently with zero duplicated GPU compute;
  `release_claims` deletes on completion (the embedding row is the
  durable record); `expire_stale_claims` self-heals abandoned work
  opportunistically at the top of each claim call. SQLite raises
  `FederationUnsupported` on all three (PR #100).
- **Backfill loop on claims, unconditionally** —
  `embed.py::backfill_embedder` switches to claim/release for every
  Postgres run. Single-host throughput is unchanged in effect (one
  extra round-trip per batch). Progress totals subtract other hosts'
  live claims so two concurrent workers each report their share of a
  shrinking pool, not duplicate progress bars (PR #107).
- **`[embed] lanes = ["name", …]`** — host-local lane pinning. A
  CUDA box can be told to only work `qwen3-4096`; a weaker box takes
  `nomic`. Absent block → all active embedders (today's behavior).
  Explicit `corpus-forge embed -e <name>` overrides the pin
  ("operator said so"). The setup wizard offers the prompt when more
  than one host has heartbeated, seeded from the accelerator probe
  ("CUDA ≥ 8 GB → suggest qwen3-4096"). `CF_NON_INTERACTIVE` accepts
  `--embed-lanes a,b` (PR #108).
- **Doctor `embed_claims` check** — stale-claim count, lease-TTL vs
  observed-rate sanity (WARN when host's rate × batch approaches the
  TTL), `FederationUnsupported` WARN when multiple hosts have
  heartbeated against a SQLite corpus (PR #109).

### Fleet-3 — Federated config + multi-host setup/join

- **Scope annotations** on pydantic config models. `shared_scope_dict`
  extracts the shared subset (dataset names/kinds, embedder
  definitions + fingerprints + dimensions, retrieval settings,
  classifier/enricher model choices); `merge_shared_scope` rewrites
  only shared-scope keys via tomlkit, preserving comments and
  ordering. Deny-list blocks path-shaped (`*_root`, `dsn`,
  `gguf_path`) and secret-shaped (`api_key`, `*_password`) fields
  from ever being extracted (PR #101).
- **New schema** (alembic revision `0020`): `corpus.shared_config`
  — versioned `jsonb` body, `published_by` host reference,
  `published_at` timestamp. One row per corpus (PR #110).
- **`corpus-forge config publish` / `config pull [--apply]` / `config
  diff`** — extract → validate (deny-list re-scan) → bump version →
  write. `publish` refuses to write when the DB version is newer than
  the last version this host pulled (`SharedConfigVersionConflict` —
  "pull first"). `pull` is dry-run by default (mirroring `prune`);
  `--apply` rewrites shared keys in `config.toml`. `diff` is the
  dry-run alias (PR #110).
- **`[federation]` config block** (default `enabled = false`). When
  absent or disabled, the federation tables are never read at runtime
  — local-only setups are byte-for-byte unchanged. When enabled,
  daemon startup compares hashes and logs **one** WARN per
  throttled-interval on shared-config drift; no background mutation,
  ever (PR #111).
- **`corpus-forge setup --join <dsn>`** — one-command host
  onboarding. Connects to the DSN, verifies schema, reads
  `corpus.shared_config`, writes a minimal local `config.toml`
  (backend block, pulled shared scope, generated `host_id`, empty
  local blocks with commented examples for source roots and `[embed]
  lanes`), and registers the host in `corpus.hosts`. Works
  interactively and via `CF_JOIN_DSN`. A joined host explicitly does
  **not** run `migrate` — the primary owns the schema lifecycle
  (PR #113).
- **`install.sh --join <dsn>` / `install.ps1 -Join <dsn>`** —
  installer pass-through. `CF_JOIN_DSN` is the env-var equivalent for
  streamed `curl | bash` / `iwr | iex` forms. In join mode the
  installer skips its question tree (shared scope comes from the
  primary), hands off to `setup --join`, runs `doctor` as a tolerant
  smoke check, and skips `migrate`. The non-join path is
  byte-equivalent to before (PR #117).

### Fleet-4 — Tailscale-native configuration and discovery

- **`corpus_forge/net/tailscale.py`** — read-only Tailscale
  integration: `resolve(name)` (MagicDNS-first, `tailscale status
  --json` fallback, 5 s timeout, process-lifetime cache), `peers()`
  for wizard pickers + `hosts list` annotations. Failure shapes
  mirror `ProcessDiscoveryUnavailable`: `TailscaleUnavailable`
  distinguishes "no binary / not running" from "name not found in
  tailnet" so the operator gets the right remediation (PR #102).
- **`ts://<magicdns-name>[:port][/path]` scheme** accepted anywhere
  a URL or DSN appears: `backend.dsn`, `EmbedderConfig.base_url`,
  `ollama.base_url`, `classifier.llm_url`, enricher URLs, VLM /
  Whisper endpoints. Resolution is lazy and applied at the point each
  URL is consumed (config stays inert; errors surface with full
  connection context). `[tailscale]` config block (default
  `enabled = false`) controls behavior. `ts://` in config with
  Tailscale disabled fails at config-load with a fix-it message
  (PR #112).
- **Doctor `tailscale` check** — binary present, backend state
  `Running`, every `ts://` name resolves, TCP-probes the configured
  port. `OK "not configured"` when the block is absent and no `ts://`
  appears in config (PR #114).
- **Wizard live-peer picker** — when `tailscale status` succeeds,
  `setup` and `setup --join` present a peer picker for the Postgres
  host and remote embedder URLs, rendering `ts://` names directly
  into config. `hosts list` cross-references
  `corpus.hosts.tailscale_name` against live peers and prints an
  online/offline marker; heartbeat fills `tailscale_name` properly
  (PR #115).

### Docs

- **"Add a second machine"** sections in `README.md`, `CLAUDE.md`,
  and `AGENTS.md`. The README gains both an operator quick-start
  (the install one-liner with `--join`, post-join steps:
  `bench embed --all` then `service install`) near the install
  commands, and a deeper "Fleet" chapter covering distributed embed,
  config publish/pull, and `ts://` addressing (PRs #116, #117).
- **Troubleshooting tables** in CLAUDE.md / AGENTS.md gain
  `TailscaleUnavailable` and "second machine: no shared config after
  `setup --join`" rows (PR #116).
- **Fleet RFC series accepted** (PR e1b4e5a, then per-PR checkbox
  syncs in #105, #106).

### MCP

- **`mcp__corpus-forge__check_update` tool** + cache-only
  instructions advisory. Lets MCP clients (Claude Desktop, Claude
  Code, mcp-cli) discover when a newer corpus-forge release is
  available without invoking the CLI directly (PR #103).

### Fixed

- **`fix(ui): latch agent-mode emit() off after stdout broken pipe`**
  — when a downstream agent process closed its stdin (the agent quit
  early), corpus-forge's structured-event emitter would crash on the
  next `print`. Now it latches off after the first `BrokenPipeError`
  and continues writing to the rotating log (PR #95).

### CI / Tests

- **HF_TOKEN now passed through to the cold-path warm step** of the
  HF cache job — secret was previously only available to the test
  step, so cold-misses on a freshly-rotated cache hit the public
  rate limit (PR #94).
- **`test_logs follow` test no longer crashes xdist workers** —
  deterministic `KeyboardInterrupt` injection replaces the previous
  signal-race that lost shards under `-n auto` (PR #99).
- **Config resolution isolated from the dev machine's environment**
  — `tests/unit/test_config.py` no longer false-positives on a stray
  `CORPUS_FORGE_*` in the dev shell (PR #104).

## [0.1.0b16] - 2026-06-04

Supersedes 0.1.0b15 — same code (PR #91 + #92 + flake-stabilization
fixes) but with the full release notes covering everything that
landed on this main commit.

### Stabilized

- **Cross-test flake from ``sys.modules`` pop with parent-attr leak**.
  ``test_package_import_does_not_load_server`` (in
  ``test_mcp_module_scaffold.py``) and
  ``TestImportSurface.test_server_module_import_does_not_construct_retriever``
  (in ``test_mcp_server.py``) both popped ``corpus_forge.mcp.*`` from
  ``sys.modules`` to force a fresh module-load. A simple snapshot/
  restore was insufficient because the import statement also writes
  the submodule as an attribute on the parent package
  (``corpus_forge.mcp.server = <module>``), and that attribute
  survives ``sys.modules.pop``. When a sibling test then did
  ``import corpus_forge.mcp.server as server_mod``, Python's
  ``IMPORT_FROM`` bytecode resolved ``server`` via attribute lookup on
  ``corpus_forge.mcp`` — picking up the *stale* freshly-imported
  module instead of the one in ``sys.modules``. Under ``-n auto`` the
  resulting patch / monkeypatch mismatches surfaced as 9-test
  cascades on macos-py3.12 and windows-py3.13 (lifecycle tests
  reporting empty results, ``pytest.raises`` missing the raised
  exception class) plus 2 ``test_cli_mcp_serve`` failures
  (``CliRunner`` stdout closed by an unpatched ``serve_stdio``). Both
  tests now run their import-side-effect assertion inside a
  ``subprocess.run([sys.executable, "-c", code])`` — a truly fresh
  interpreter with no parent-attr pointers to leak.

- **Integration ``embedder`` fixture's ``return`` aborted the
  ``patch(...)`` context manager mid-test**, restoring the real
  ``SentenceTransformer`` before any ``embedder.encode(...)`` call
  ran. With CI's ``HF_HUB_OFFLINE=1`` and shared-runner-pool 429
  storms this surfaced as recurring "HTTP Error 429 … Timeout"
  failures on ``Integration (ubuntu-22.04 / py3.{11,12})``. Two-part
  fix in ``TestSentenceTransformersEmbedderContract.embedder``:
  ``yield embedder`` (not ``return``) so the patch survives the test;
  and an ``encode.side_effect`` lambda that returns an array shaped
  to match input length, so ``test_encode_single_text`` /
  ``test_encode_empty_list`` don't fail on the now-active mock.

### CI

- **HF cache keyed on the model list** (PR #92). The HuggingFace
  cache used to share a key with ``uv.lock`` / ``pyproject.toml``,
  so every unrelated dependency bump rotated the multi-GB model
  cache and forced every cell to re-download. New
  ``.github/ci-models.txt`` pins the warmed model set; the cache
  key hashes that file only, so the model cache stays valid until
  the model list itself changes. On a miss, the new
  ``.github/scripts/warm_hf_cache.py`` script pre-downloads each
  repo with ``snapshot_download``, retry/backoff on transient
  ``429 / 5xx``, and exit-non-zero on permanent failures (``404`` /
  auth / malformed repo id). Test steps then run under
  ``HF_HUB_OFFLINE=1`` so a Hub outage during the test phase can't
  flake them. Warm-step soft-fails when *all* failures are
  transient HTTP errors (Hub CDN rate-limiting the runner pool's
  egress IP) so an environment-level flake doesn't block CI —
  unit / fuzz / smoke tests mock model loads, and integration
  tests skip via their own ``model_loads_ok`` conftest fixtures
  when needed weights aren't in cache.

## [0.1.0b15] - 2026-06-04

### Fixed

- **MCP server: `writes_enabled` now defaults to `True`** (PR #91).
  Hotfix — the 16 write tools were unreachable in 0.1.0b14 and
  earlier because nothing flipped the default. Opt-out remains via
  `--no-writes` / `writes_enabled=False`.

### Added

- **New `corpus-forge mcp restart` verb** (PR #91) — SIGTERMs every
  running `corpus-forge mcp serve` process so the client respawns it
  under the new wheel.
- **New doctor `mcp_servers` check** (PR #91) — detects running
  servers with `--no-writes` in argv (WARN) and surfaces
  `ProcessDiscoveryUnavailable` when `ps` can't enumerate the process
  table (OK with "detection unavailable" detail).

## [0.1.0b14] - 2026-06-03

### Added

- **Auto-detect accelerator and pick a matching embedder lane** (PR
  #87). New ``corpus_forge.acceleration`` module probes the host
  hardware via a lightweight ``subprocess.run(["nvidia-smi", ...])``
  shellout (no torch dependency — keeps doctor shippable on minimal
  installs) plus ``torch.backends.mps.is_available()`` on Apple
  Silicon, then maps the result to one of three llama-cpp lanes:
  CUDA ≥ 8 GB VRAM → ``qwen3-embedding:8b`` (4096d) with
  ``n_gpu_layers = -1``; CUDA < 8 GB → ``nomic-embed-text`` (768d)
  with full GPU offload; MPS → same as CUDA-≥8GB; CPU →
  ``nomic-embed-text`` (768d) with ``n_gpu_layers = 0``. All lanes
  use ``provider = "llama-cpp"`` so cross-host configs diverge by a
  single field. Two integration points:
  - ``corpus-forge setup`` wizard gets a new ``embedder = "auto"``
    choice (the default) that calls the detector and emits the
    matching ``[[embedders]]`` block.  ``st`` / ``openai`` / ``both``
    stay as opt-out manual lanes.
  - ``corpus-forge doctor`` gets a new informational
    ``embedder_acceleration`` check that surfaces the detected
    hardware and the recommended ``model_id`` so operators can spot
    a config leaving a freshly-attached GPU on the table.  Always
    ``OK`` status — CPU is a slower lane, not a failure.  Wrapped in
    ``except Exception`` so a future detection-helper change can
    never crash ``doctor``.
  - Detection times out at 2 s on ``nvidia-smi`` so a hung driver
    never blocks the wizard / doctor; ``FileNotFoundError`` /
    ``TimeoutExpired`` / non-zero exit / ``PermissionError`` all
    fall through cleanly.

### Fixed

- **llama-cpp embedder silently truncated every chunk to 64 tokens**
  (issue #88). llama-cpp-python's ``embedding=True`` initialiser
  overrides ``n_seq_max`` to ``min(n_batch,
  llama_max_parallel_sequences())`` (256 on a stock 0.3.25 install),
  and ``encode()``'s runtime introspection fed that through the
  split-KV-cache math unconditionally: ``8192 // 256 - 4 = 28``,
  floored to 64 — so every chunk longer than 64 tokens was sliced
  before embedding, with only a DEBUG line as evidence. The same
  binding versions also set ``kv_unified = True`` on embedding
  contexts and clear the KV cache before every decode, which means a
  single sequence may use the whole window. The new
  ``effective_n_ctx_seq`` helper branches on the handle's
  ``kv_unified`` flag: unified caches get
  ``min(n_ctx, n_batch, n_ubatch) - headroom`` (full window on stock
  configs); split caches keep the conservative
  ``n_ctx // n_seq_max`` division that PR #80 shipped for the
  ``decode: failed to find a memory slot`` crash. ``encode()`` now
  also WARNs once per instance whenever the effective budget lands
  below half the configured ``n_ctx``, so a future collapse can't
  hide at DEBUG level again. Vectors embedded while the collapse was
  active were built from 64-token prefixes and should be re-embedded.

## [0.1.0b13] - 2026-06-03

### Fixed

- **Daemon is now actually useful** (PR #86). Six stacked fixes that
  take ``corpus-forge service start`` from "respawn loop that does
  nothing" to "drop a file in a watched folder → it lands in the
  corpus, chunked + embedded + searchable, while ``service stop``
  returns in <1 s." Each fix reuses an existing repo pattern rather
  than inventing a new mechanism.
  1. **``daemon.main()`` wired through ``run_daemon``.** The published
     entry point was calling the unimplemented ``ingest_main(once=False)``
     stub, so under launchd's ``KeepAlive=true`` the process respawned
     every ~10 s doing zero work. ``main()`` now loads ``Config``,
     dispatches to ``run_daemon(config)``, initialises
     ``init_logging("daemon", ...)`` (so daemon records survive the
     LaunchAgent / systemd-unit stderr-to-``/dev/null`` redirection),
     and blocks in ``time.sleep`` until the signal handler raises
     ``SystemExit``.
  2. **Dataset id + plugin-aware source root.** Three latent
     ``AttributeError`` traps: Pydantic ``DatasetConfig`` carries
     neither ``id`` nor ``exclude_globs`` (the id lives on the
     backend's ``corpus.datasets`` row; ``exclude_globs`` is per-
     source), and ``source.root`` is plugin-specific
     (``filesystem``→``source.root``, ``markdown_vault``→
     ``source.vault_root``, chat/Zotero→none). ``run_daemon`` now
     resolves the id via ``backend.find_dataset_id_by_name(name)``
     and the on-disk path via
     ``ignore_lifecycle._source_root(source)``, and passes them as
     explicit ``SyncEngine(dataset_id=..., source_root=...)`` kwargs.
  3. **Watchdog ``on_created`` → per-file ingest (new-file discovery).**
     ``PushPipeline`` was a replication-only pipeline — it silently
     dropped brand-new files. ``PushPipeline`` now accepts an
     optional ``discovery_callback`` and uses ``find_document``
     (lookup-only, not the create-stub ``resolve_document``) to
     detect genuinely-new paths. ``SyncEngine`` forwards the
     callback. ``run_daemon`` builds a per-source callback via
     ``_build_discovery_callback`` that lazy-instantiates the Source
     plugin, the dispatched Chunker, and the active embedders on
     first use (qwen3-4096 is ~4 GB resident; an idle daemon stays
     light). ``handle_change`` is wrapped in a try/except so silent
     worker-thread crashes surface in ``daemon.log``.
  4. **Edge-case survival.** ``LlamaCppEmbedder.encode`` now follows
     ``OpenAIEmbedder``'s ``last_failed_indices`` contract (PR #49):
     non-finite rows are dropped from the returned array and their
     input indices recorded — avoids ``psycopg.errors.DataException:
     NaN not allowed in vector`` from llama-cpp's occasional NaN
     emissions. ``PushPipeline.start`` composes the same three-layer
     ``IgnoreStack`` the scanner uses
     (``load_global_ignore + load_local_ignore + _ignore_from_globs``)
     so binary files the managed ``.corpusignore`` excludes at scan
     time are also skipped by the watchdog — and ``UnicodeDecodeError``
     from ``read_text`` is caught at DEBUG as a belt-and-suspenders.
     ``IngestRunInProgressError`` from per-source lock contention
     (Obsidian autosave bursts) now logs at DEBUG instead of ERROR.
     ``run_daemon._shutdown`` stops engines in parallel via
     ``concurrent.futures.ThreadPoolExecutor`` then ``os._exit(0)``
     (aliased as ``_exit_hard`` for testability) so the daemon
     terminates promptly instead of waiting on Python's
     interpreter finalisation. ``stop_daemon`` treats a pid
     replacement (current pid ≠ original pid) as a clean exit so
     launchd's ``KeepAlive=true`` respawn isn't mistaken for "still
     alive" — ``service stop`` dropped from 30 s + ``SIGKILL`` to
     ~0.3 s.
  5. **File modifications re-chunk + re-embed.** Aligned
     ``PushPipeline._compute_source_uri`` with ``Source.parse``'s
     output (via a per-plugin ``source_uri_prefix`` derived in
     ``run_daemon``) so ``find_document`` actually matches existing
     rows. ``_handle_change_inner`` routes any content change
     (new file OR existing file whose ``content_hash`` differs)
     through the discovery callback — so modifications re-run
     ``ingest_one`` → ``upsert_document`` (BUG-3 chunk-preserving
     update path) → chunks and embeddings actually reflect the new
     content. ``content_hash``-unchanged events (mtime touch, IDE
     format-on-save) now no-op cheaply without acquiring a lock.
  6. **CI portability.** ``_should_ignore`` uses
     ``getattr(path.stat(), "st_blocks", None)`` instead of bare
     attribute access — Windows's ``os.stat_result`` lacks the
     POSIX-only field. ``test_daemon.py`` autouse fixture
     snapshots + restores SIGINT/SIGTERM handlers per test so the
     real handlers ``run_daemon`` installs don't survive into the
     ``test_logs_subcommand.py`` SIGINT test on shared pytest-xdist
     workers (the previous SystemExit-based shutdown masked this
     leak; the new ``os._exit`` shutdown surfaced it as a worker
     crash).

## [0.1.0b12] - 2026-06-02

### Added

- **`corpus-forge agents init`** (PR #85) — corpus-grounded `/init` that
  synthesizes project conventions by combining (a) local project
  inspection, (b) cross-corpus retrieval over the user's indexed
  corpus, and (c) a two-pass LLM synthesis. **Two-output split** so
  private corpus signal never leaks into shared projects' git history:
  - `<project-root>/.corpus-agents/AGENTS.md` — private, corpus-grounded,
    chunk_id citations. Gitignored automatically.
  - `<project-root>/.corpus-agents/shareable.md` — sanitized subset,
    citation-free, intended for manual review + selective lift into a
    team `AGENTS.md`.
  - `<project-root>/AGENTS.md` — created from shareable content **only
    when absent**. `--force` NEVER overwrites it (sacred-file
    contract; pinned by three independent tests).
  - `<project-root>/.gitignore` — `.corpus-agents/` appended
    idempotently.
  New flags: `--project-root`, `--output-dir`, `--no-root-write`,
  `--gitignore/--no-gitignore`, `--no-ingest`, `--force`, `--diff`,
  `--json`. Auto-ingests the project root if not covered by any active
  source. New `/corpus-agents` skill wraps the CLI for Claude Code.
  Defensive regex sanitization on the shareable output rejects any
  `chunk_id=N`, `filesystem://`, or `vault://` markers the LLM might
  leak past the sanitization prompt.

## [0.1.0b11] - 2026-06-02

### Added

- **New `llama-cpp` embedder backend** (PRs #78, #79, #80). In-process
  via `llama-cpp-python`, native Metal on Apple Silicon. Bypasses
  Ollama's `/v1/embeddings` JSON encoder so the qwen3-embedding-style
  NaN-in-vector bug can't drop the wire. New `provider = "llama-cpp"`
  on `[[embedders]]` plus knobs `gguf_path`, `n_ctx`, `n_seq_max`,
  `n_batch`, `n_ubatch`, `n_gpu_layers`. GGUF resolution rule:
  explicit `gguf_path` wins; else auto-discovers from
  `~/.ollama/models/manifests/registry.ollama.ai/library/<name>/<tag>`;
  else raises a clear error naming both knobs. Runtime introspection
  of `llama_n_ctx(ctx)` / `llama_n_seq_max(ctx)` (PR #80) so
  truncation honors what llama.cpp actually allocated rather than the
  configured value (v0.3.x bindings silently override `n_seq_max`).
  Python-side token-aware truncation to `n_ctx_seq` keeps the
  decoder within budget. New optional `[llama-cpp]` extra. Apple
  Silicon install: `CMAKE_ARGS="-DGGML_METAL=on" uv tool install
  'corpus-forge[llama-cpp]'`.
- **Extension-based chunk routing for dual-tower setups** (PR #81).
  New `extensions: list[str]` on `[[embedders]]` — each chunk routes
  to exactly one active embedder: first specialist whose
  case-insensitive `endswith` matches the chunk's source URI wins;
  otherwise the first catchall (empty `extensions`) claims it.
  Config-load gate (`EmbedderRoutingError`) rejects specialist-only
  setups missing a catchall. `corpus-forge embed -e <name>` filters
  its pending pool through the rule. Pairs with the llama-cpp
  backend to make a `nomic-embed-text-v1.5` (catchall) +
  `nomic-embed-code` (code specialist) dual-tower work out of the
  box. Backwards-compat: when no embedder declares `extensions`,
  every active embedder still embeds every chunk.
- **`corpus-forge chunk` CLI subgroup** (PR #83) — agent-friendly
  chunk explorer. `chunk show <id>` (full text + metadata, prev/next
  hints, absolute disk path), `chunk neighbors <id> --before N
  --after N` (ordered siblings in same document/conversation),
  `chunk doc <doc_id> [--reassemble]` (every chunk of a document,
  optionally concatenated for full-file view). All three support
  `--json` for a single-line machine-readable object.
- **`corpus-forge search --json -`** (PR #83) — single JSON object on
  stdout with `{query, k, took_ms, hits}` and zero log chatter;
  alembic / plugin / agent-event lines suppressed. The existing
  `--json <PATH>` file-output contract is preserved.
- **New MCP tools** (PR #83): `chunk_neighbors(chunk_id, before,
  after)` and `get_document(document_id, reassemble=False)`. The
  existing `get_chunk` tool gained `prev_chunk_id`, `next_chunk_id`,
  and `abs_path` fields (additive — no field renames).
- **Absolute-path resolver** (PR #83) — new
  `corpus_forge/sources/path_resolve.py::resolve_abs_path` maps
  `filesystem://<root>/<rel>` URIs to absolute on-disk paths by
  walking `config.datasets[*].sources[*]`. Returns `None` for
  non-filesystem URIs (conversation, http, etc.). Used by the chunk
  CLI + MCP responses so agents don't have to remap URIs in their
  heads.
- `Pods/` (CocoaPods vendor dir) added to the managed
  `.corpusignore` template (PR #76). Surfaced by `doctor` drift
  check on iOS / React Native projects.

### Changed

- `StorageBackend.chunks_missing_embedding` widened to yield
  `(chunk_id, text, source_uri)` 3-tuples and gained two optional
  kwargs (PR #81, PR #82): `extensions: list[str] | None` pushes the
  routing allow-list into the SQL `WHERE` clause; `after_id: int |
  None` is a forward-progress cursor so paged backfills walk the
  table deterministically. Both Postgres + SQLite implementations
  updated; `count_chunks_missing_embedding` honors the same
  filter so progress totals are honest. Backwards-compat: omitting
  the kwargs (or passing `extensions=None`) yields current
  behaviour.
- `StorageBackend` gained `get_chunk_neighbors(chunk_id, before,
  after)` and `get_document_chunks(doc_id)` on both Postgres + SQLite
  implementations (PR #83), plus a defensive
  `_chunk_prev_next_ids` helper. The existing `get_chunk` response
  now includes `prev_chunk_id`, `next_chunk_id`, `abs_path` —
  additive, no field renames.
- `backfill_embedder`'s pending-pool loop (PR #82) no longer
  `break`s when an in-memory route_for filter empties a page — it
  `continue`s to the next page via the SQL cursor. A
  consecutive-empty-page abort guard (`_MAX_EMPTY_PAGE_STREAK = 10`)
  fires `RuntimeError` only on specialists (where the SQL filter
  should keep every page dense) — catchall runs legitimately walk
  past specialist-owned chunks and don't trip the alarm.

### Fixed

- **Subagent fabrication carve-out** (PR #77) — the
  `corpus-forge-researcher` subagent was emitting confident
  citations with `tool_uses: 0` (i.e. fabricating MCP responses).
  Hardened the agent prompt with an anti-fabrication section that
  cites the harness's tool-use counter as the verification
  mechanism, forbids pasted fake `<function_calls>` blocks, requires
  integer chunk_ids (real chunk_ids are integers; UUID-shaped IDs
  are an explicit hallucination tell), and requires verbatim quotes
  from tool results. Added "Not found in corpus" and "MCP
  unavailable" output templates so refusing retrieval is
  structurally easier than inventing it.
- **Routing backfill stall** (PR #82) — closes the loop hole
  introduced in PR #81 where the in-memory `route_for` filter ran
  AFTER fetching a 1000-row page from the non-cursored
  `chunks_missing_embedding` query. When the first page happened to
  be entirely non-matching for the specialist, the loop hit
  `break` and abandoned the rest of the corpus. Pushed the filter
  into SQL on both backends + cursor-based paging. Symptom in prod:
  1.88M chunks "pending" for a new code-specialist embedder, only
  13 ever embedded over multiple restarts.

### Added (earlier in the cycle)

- `OpenAIEmbedder._embed_oversized_chunk` rescues chunks too long
  for the embedder's context window via recursive split-in-half +
  per-piece embed + mean-pool, returning ONE representative vector
  instead of skipping the chunk. Surfaced 2026-05-27 when restarting
  ingest after switching to `nomic-embed-text` (8k context): some
  code chunks in the maintainer's vault exceeded the window and were
  being skipped by the bisection's base-case path. The rescue
  recursion is bounded by `_MAX_OVERSIZED_SPLIT_DEPTH = 8` (256
  pieces upper-bound) so pathological inputs eventually fall through
  to skip rather than loop forever. The outer `encode` re-normalises
  the pooled vector when `self.normalized`, so the returned
  embedding is still unit-length. New helper
  `_is_context_length_error(exc)` lifted out of
  `_is_recoverable_exception` so both the bisection classifier and
  the base-case rescue path share one source of truth. Regression
  coverage in
  `tests/unit/test_openai_embedder_bisection.py::Test400ContextLengthCarveOut`:
  rescue-then-skip on pathological-input (every sub-piece still
  400s); successful rescue via single split; recursive split when
  the first half is still too long; depth-limit fallback to skip;
  mixed batch with one oversized + one normal chunk (both land,
  zero skipped).

### Changed

- `PostgresBackend.upsert_document` now batches all per-document
  chunk INSERTs into one multi-row `INSERT ... VALUES ...
  RETURNING id, content_hash` statement, and the
  embedding-reuse copy (`_copy_reusable_embeddings_batch`) does
  exactly 2 round-trips per embedder regardless of chunk count
  (one `SELECT DISTINCT ON (content_hash)` for prior chunks +
  one `INSERT ... SELECT ... FROM (VALUES ...) JOIN ...` for the
  bulk copy). Replaces the per-chunk loop that did 2+ round-trips
  per chunk. For an N-chunk file at ~4ms per round-trip over
  Tailscale that's `2 + 4N → 1 + 2` round-trips per embedder —
  roughly 5-10x faster on the 86%-of-per-file-time slice that
  the 2026-05-27 profile identified as the ingest bottleneck.
- `PostgresBackend.register_embedder` is now process-lifetime
  cached on `embedder.name → id`. First call does the original
  3 round-trips (SELECT existing + INSERT/UPDATE + CREATE TABLE
  IF NOT EXISTS); subsequent calls return the cached id without
  any DB traffic. Saves ~46ms per call × N files in
  `ingest_one`'s pre-upsert phase (~40 min over the maintainer's
  51k-file ingest). The associated `_embedder_info_cache` (id →
  `{name, table_name}`) eliminates the per-chunk `SELECT name,
  table_name FROM embedders` that the old
  `_copy_reusable_embeddings` did inside its inner loop. A new
  `_ensure_embedder_caches` helper lazy-initialises the dicts so
  unit tests that bypass `__init__` keep working without
  modification.
- `PostgresBackend` now registers an `atexit` callback to close
  its connection pool at interpreter shutdown — replaces the
  `couldn't stop thread 'pool-1-worker-N' within 5.0 seconds`
  warnings that psycopg-pool emits at the end of every CLI
  invocation. Held by **weakref** so the callback doesn't pin the
  pool alive for the process lifetime (if the backend is GC'd
  mid-process, the pool can be reclaimed normally and the atexit
  call becomes a no-op via the dead weakref). Uses `atexit`
  rather than `weakref.finalize` (which fires on GC and at
  shutdown) because the earlier attempt at the same fix (commit
  ec9632e, reverted in 50913a3) broke py3.12 Integration CI: the
  finalizer fired mid-test as backends went out of scope, racing
  pytest's own teardown and exhausting Postgres's
  `max_connections` budget with "FATAL: sorry, too many clients
  already" on ~6 alembic-migration tests. `atexit` fires only at
  interpreter shutdown so it can't disrupt test-time connection
  state. Regression coverage in the new
  `tests/integration/test_postgres_pool_lifecycle.py` (8 tests)
  pins: `atexit` is registered (not `weakref.finalize`), 20
  concurrent backends don't exhaust `max_connections`, 50
  serial-then-close backends never exhaust either, `close()` is
  idempotent, `close()` actually closes the pool, dead-weakref in
  the atexit callback is a no-op, and pool-close exceptions
  during interpreter shutdown are suppressed cleanly.
- `PostgresBackend` now uses a `psycopg_pool.ConnectionPool` instead
  of opening a fresh TCP+TLS+auth handshake on every backend call
  (the previous `# For now, we'll create a new connection each time`
  TODO). Profiled 2026-05-27 against the maintainer's vault over
  Tailscale: cold connect was ~45ms per call; pooled drops to
  ~14ms. With 5-7 backend calls per file × 51k files, this saves
  ~2 hours of pure connection overhead on a full ingest. New
  `pool_min_size` / `pool_max_size` kwargs (default 0 / 8 — lazy
  pool, max 8 concurrent connections); the pool's `close()` is
  reachable via `backend.close()` so tests + the daemon can dispose
  of a backend cleanly. Schema `search_path` is set once per pooled
  connection via the pool's `configure` callback, so the existing
  unqualified-table-name DDL semantics survive.
- `ingest_once`'s per-source iteration now lives inside an outer
  `try/finally`; the `finally` clause runs the end-of-source
  `_flush_all_pending_embeddings` call. Previously the flush sat
  after the `for raw in raw_items:` loop, so an iterator failure
  (filesystem read crash mid-walk, `EmbedderWedged` propagating
  out of the inner per-file `except`, etc.) skipped the flush and
  left the trailing files' chunks un-embedded until the next
  ingest pass. The finally also wraps the flush call in its own
  try/except so a flush failure during error-unwind doesn't mask
  the original exception.
- `ingest_once` now batches the embedding flush across files instead
  of flushing after every file. The per-file path was paying ~209ms
  for the `chunks_missing_embedding` LEFT-JOIN-anti-join (called
  once per file). Now the flush runs every
  `_FLUSH_EMBEDDINGS_EVERY_N_FILES = 32` files plus once at the
  end of each source — a **97% reduction** in query cost (32 calls
  → 1 call per window) and ~56 minutes saved on the 51k-file
  baseline. `ingest_one` gains a `flush_embeddings: bool = True`
  kwarg; external callers (the live `ingest_one` API) keep the
  per-file flush by default, only `ingest_once` opts in to batched.
  `_write_embeddings_for_chunks` now returns the count of
  embeddings written so the new `_flush_all_pending_embeddings`
  helper can loop until it returns 0 (drain-until-empty) without
  the extra `count_chunks_missing_embedding` round-trip.
- The raw DB-API 2.0 connection factory that `corpus-forge analyze` and
  `corpus-forge feedback` each carried as a byte-identical
  `_get_backend_conn` is now defined once in
  `corpus_forge/backends/conn.py` as `open_conn`. Both CLI modules keep a
  thin `_get_backend_conn` wrapper that delegates to it, preserving the
  module-level monkeypatch seam tests rely on. Behavior is unchanged.

### Fixed

- `OpenAIEmbedder.encode` now has a **circuit breaker** that raises
  the new `EmbedderWedged` exception once
  `_WEDGE_THRESHOLD_CONSECUTIVE_FAILURES` (50) chunks have accumulated
  across one or more consecutive *all-failed* mini-batches with no
  intervening successful mini-batch. Surfaced 2026-05-26 on the
  maintainer's 357k-chunk vault: with Ollama-served
  `qwen3-embedding:8b` returning NaN for code-shaped inputs, the
  bisection-with-skip recovery (b10 hardening) turned into a silent
  ~1.3 chunks/sec no-op — 800 sequential WARNINGs over 11 minutes
  with zero embeddings written (counter stuck at `0/357186`). The
  breaker now trips after the first all-failed mini-batch crosses
  the threshold (timing is host-dependent: roughly the time for one
  full bisection of a mini-batch ≥ threshold size — observed ~30s to
  a few minutes depending on response latency) so operators see a
  clear failure with a recovery hint instead of grinding indefinitely.
  Behavior:
  - Counter updates **per mini-batch**, AFTER bisection completes
    and the final `(rows, failures)` is known. `rows` non-empty
    resets the counter (any successful embedding proves the
    upstream is alive); `rows` empty (every chunk in the mini-batch
    isolated and skipped) adds the mini-batch size to the streak
    and checks the threshold. Per-chunk accounting was tried and
    discarded — DFS preorder would spuriously trip on mixed batches
    where the failures happened to cluster in the left subtree
    (e.g. a 100-chunk batch with the first 50 NaN and last 50 clean
    would fire the breaker before the right subtree was ever
    explored).
  - Persists across `encode()` calls so multi-file ingest
    accumulates the streak (`_write_embeddings_for_chunks` calls
    `encode()` once per file). A successful mini-batch in any later
    file resets the streak.
  - On trip, `last_failed_indices` carries every chunk attempted
    across the all-failed mini-batches that fed the streak — the
    accumulator already covers everything because bisection runs
    side-effect-free and the breaker fires *after* it returns.
  - `ingest.ingest_one`'s per-file `except Exception` re-raises
    `EmbedderWedged` instead of catching it (systemic, not per-file)
    so the breaker actually breaks out of the file loop.
  - `ingest.main()` catches `EmbedderWedged` at the CLI boundary,
    logs a clean ERROR line, and re-raises so the exit code
    reflects the failure.
  Regression coverage in
  `tests/unit/test_openai_embedder_bisection.py::TestWedgeCircuitBreaker`
  (11 new tests): threshold constant ≥ 30 (absorbs realistic
  bad-chunk bursts), below-threshold doesn't trip, at-threshold
  raises with embedder name + model_id in the message,
  per-chunk-granularity trip during bisection of an oversized
  single all-failed mini-batch, success resets the counter,
  counter persists across `encode()` calls, 50% failure rate
  sustained over 4x threshold doesn't trip, recovery hint present
  in the message, `last_failed_indices` covers every chunk
  attempted at trip (single-mini-batch + cross-mini-batch merge,
  no duplicates), and the load-bearing regression for the
  spurious-trip bug — a mixed batch with all failures clustered
  in the left subtree must NOT trip the breaker.
- `ZoteroLocalReader._validate_schema_compatibility` now accepts ANY
  `setting='client'` row in the `settings` table rather than requiring
  the specific `key='lastclient'` value. Modern Zotero (5.x / 6.x /
  7.x) writes `client.lastVersion` and `client.lastCompatibleVersion`
  on every startup — but never `client.lastclient` — so the previous
  check false-negatived against every real Zotero 7 library, raising
  `ZoteroSchemaUnsupported` even when the DB was perfectly valid.
  Surfaced 2026-05-22 on the maintainer's vault: the eager planner
  walk in `_plan_ingest` propagated the exception and aborted the
  entire ingest before any filesystem source ran. The synthetic test
  fixture under `tests/fixtures/zotero/build_fixture.py` has also been
  updated to write what real Zotero writes (`lastVersion` /
  `lastCompatibleVersion`) and the fixture sqlite regenerated. New
  regression tests in `tests/unit/test_zotero_local.py::TestSchemaProbe`
  pin: empty `settings` table raises, `settings` rows without a
  `client` row raise (with a helpful message), and all three known
  client-row key names (`lastVersion`, `lastCompatibleVersion`,
  `lastclient`) pass the probe.

## [0.1.0b10] - 2026-05-26

### Added

- `corpus-forge ingest` now enforces `DatasetSourceConfig.max_rows` /
  `max_bytes` per source by evicting the lowest-scoring chunks via
  `score_for_pruning(...)` after each source completes a scan.
  Attribution uses URI-scheme prefix matching
  (`corpus_forge/admin/source_caps.py::derive_source_uri_prefix`) —
  plugins whose scheme isn't uniquely owned (e.g. `zotero` without a
  `library_id`, unknown plugins) are silently skipped with a one-line
  WARNING. Fourth RFC item of `rfc-corpus-growth-controls`.
- New `corpus-forge prune --dataset NAME [--percentile N] [--apply]
  [--dry-run-json PATH]` verb — dry-run-default deletion of the
  bottom-percentile chunks under
  `corpus_forge.admin.prune.prune_dataset()`. Third RFC item of
  `rfc-corpus-growth-controls`.
- New `prune_dataset()` module + scoring rubric
  (`corpus_forge/admin/prune.py`) — first step of
  `rfc-corpus-growth-controls`. Postgres / SQLite dispatch goes
  through a small `_is_postgres_like` capability probe
  (`_paramstyle == "pyformat"` first, class-name `"postgres"`
  substring as fallback) so we don't lean on a single brittle name
  check; SQLite branch chunks the IN-list at `_SQLITE_BATCH_SIZE =
  500` ids. `PruneReport.duplicate_density_available` exposes
  whether the MinHash quality signal ran (promoted off the head
  candidate's `sub_scores` so every element of `selected` is now
  shape-uniform). Named-but-unknown datasets raise `ValueError`
  before any candidate walk — critical safety guard under
  `apply=True` so a typo'd name can never delete from the wrong
  scope. 22 unit tests in `tests/unit/test_prune_scorer.py` lock
  the rubric, the dispatch heuristics, both delete paths, and the
  unknown-dataset refusal.
- Public `score_for_pruning(candidate, *, sub_scores, weights=None)`
  exported from `corpus_forge.curation.selector` — extracted from
  `corpus_forge.admin.prune` so the rubric lives next to the
  curation selector. Default weights exposed as `PRUNE_WEIGHTS`.
- `[growth]` config block — first foundation task of RFC
  `rfc-corpus-growth-controls` (P1). `corpus_forge.config.GrowthConfig`
  exposes three fields: `prune_percentile_default` (int 0-100,
  default 10), `sync_cap_bytes` (int | None — accepts human-readable
  strings like `"10G"`, `"500M"`, `"1.5T"` via the new private
  `_parse_bytes` helper; IEC 1024-based, case-insensitive,
  optional `B` suffix), and `per_source_cap_default_rows`
  (int ≥ 0, default 0 = disabled). All fields default to
  no-enforcement values so existing configs without a `[growth]`
  block continue to validate and behave identically.
- `DatasetSourceConfig.max_rows` / `DatasetSourceConfig.max_bytes`
  — per-source growth caps (RFC `rfc-corpus-growth-controls`). Both
  `int | None`, default `None` (uncapped), validated `> 0` when set.
  Storage-only — the runtime eviction loop landed alongside in this
  release.
- `corpus_forge.eval._schema.EvalOutput` — shared output envelope
  for every `corpus-forge eval *` subcommand. Pydantic v2 model with
  six top-level keys (`eval_kind` ∈ {classifier, quality, retrieval,
  regression}, `dataset`, `git_commit`, `ts`, `metrics`, `config`).
  `extra='forbid'` so future evaluators can't accidentally widen the
  envelope. Foundation task of RFC `rfc-eval-framework-expansion`
  (P1); subsequent PRs add `classifier_accuracy.py`,
  `chunk_quality.py`, and `regression.py` which marshal their
  results through this envelope so downstream dashboards see one
  consistent shape.
- `[eval_regression]` config block —
  `corpus_forge.config.EvalRegressionConfig` drives the future
  `corpus-forge eval regression --baseline` verb's tolerance gating.
  Three fields: `enabled` (default `True`), `default_tolerance`
  (float `[0, 1]`, default `0.02`), `per_metric` (dict of
  metric-name → tolerance, each bounded `[0, 1]`). Convenience
  `tolerance_for(name)` helper. Foundation task of RFC
  `rfc-eval-framework-expansion` (P1).
- `corpus_forge.quality.HeuristicQualityEnricher` — pure-Python,
  dependency-free composite quality scorer (token-rate,
  punctuation-balance, repetition-ratio, shouting-ratio → weighted
  geometric mean on `[0, 1]`). First foundation task of RFC
  `rfc-nlp-data-quality-signals` (P1); subsequent PRs add language
  detection, MinHash dedup, and boilerplate pattern-matching, plus
  the curation-selector hookup. Distinct from the Phase H
  `corpus_forge.enrichers` (code-enricher) pipeline — code lives in
  the new `corpus_forge.quality` package.
- `corpus-forge logs tail --level <name>` — minimum-severity filter
  for the rotating-log viewer. Accepts `debug` / `info` / `warn` /
  `warning` / `error` / `critical` (case-insensitive). Lines below
  the named severity are dropped in both single-shot and `--follow`
  modes; unparseable lines (tracebacks, `print()` output) are also
  dropped when a level filter is active. Closes the `logs tail` /
  `--level` checkbox of RFC `rfc-developer-ux-verbs` (P3).
- New `embedder_drift` doctor check + `corpus-forge embedder gc`
  CLI command. Catches the silent embedder-rename bug surfaced on
  2026-05-22: renaming an embedder in `config.toml` (e.g.
  `qwen3-2000` → `qwen3-4096`) left the original `corpus.embedders`
  row plus its per-embedder `embeddings_<name>` table behind in the
  database, and `corpus-forge embedder list` (which reads
  config-side state only) was blind to it. The maintainer's instance
  had accumulated a 209 MB orphan table on top of a 342 MB
  real-data DB before this code landed. The new check walks
  `corpus.embedders`, compares against `cfg.embedders[*].name`, and
  WARNs when DB rows have no matching config entry — with a
  reclaimable-bytes total and the `corpus-forge embedder gc --apply`
  recovery hint. The new CLI lists orphans (`--dry-run` default)
  and drops both the table and the catalog row on `--apply`.
  Regression tests:
  `tests/unit/test_doctor_embedder_drift.py` (10 tests pinning the
  SKIP / OK / WARN branches + `run_doctor` registration) and
  `tests/unit/test_embedder_gc.py` (19 tests on the underlying
  `audit_embedder_drift` / `reconcile_embedder_drift` helpers
  including SQLite short-circuit, error tolerance on size/count
  probes, partially-cleaned orphans, and the `_human_bytes`
  rendering helper). 60/60 doctor + embedder tests pass after the
  change.
- New module `corpus_forge.macos_tcc` — iCloud Drive + TCC integration
  for macOS hosts. Public surface:
  - `is_icloud_path` / `is_iclouddrive_managed` — classify a path as
    iCloud-rooted (CloudDocs strict / Mobile Documents broad).
  - `probe_tcc_access` — non-destructive 1-byte read that
    distinguishes `GRANTED` / `DENIED` / `MISSING` / `ERROR` /
    `NOT_APPLICABLE`. Catches the canonical
    `PermissionError(errno=1, "Operation not permitted")` that macOS
    surfaces when the running terminal hasn't been granted Full Disk
    Access for `~/Library/Mobile Documents/`.
  - `open_privacy_settings(pane)` — launches System Settings to the
    Full Disk Access or Files and Folders pane via the
    `x-apple.systempreferences:` URL scheme.
  - `request_full_disk_access(paths)` — the install-time handshake.
    Probes the supplied iCloud paths, opens the Privacy pane on
    denial, and returns a structured outcome plus human-readable
    instruction text naming the binary the user should add.
  - `download_if_evicted(path)` — best-effort `brctl download`
    wrapper for cloud-only placeholders. Proactive companion to the
    `FilesystemSource` eviction-tolerance fix from PR #19.
  Every public function degrades to a safe no-op on non-macOS hosts
  (Linux / Windows installs are zero-cost). Module is exercised by
  `tests/unit/test_macos_tcc.py` (29 tests pinning both macOS and
  non-macOS branches via `sys.platform` monkeypatch).
- `corpus-forge doctor` gains an `icloud_access` check that walks
  every configured filesystem source, classifies iCloud-rooted
  roots, and probes each one for TCC access. `OK` when all probes
  succeed, `WARN` when at least one is `DENIED` (with the
  `Run corpus-forge setup` recovery hint), `SKIP` on non-macOS hosts
  or when no iCloud-rooted source is configured.
- `corpus-forge setup` now runs a macOS TCC handshake after writing
  `config.toml`. When any answer in the wizard resolves to an
  iCloud-managed path and the probe fails, System Settings →
  Privacy & Security → Full Disk Access opens automatically and the
  wizard prints the exact terminal binary the user should add. In
  non-interactive mode the same handshake runs without opening the
  pane (CI / unattended installs would never see a GUI dialog
  anyway) so a denial still surfaces in the install log.
- `FilesystemSource.parse` now proactively materialises iCloud
  placeholders before extraction. When the path is iCloud-managed
  (any `Mobile Documents` provider) and the file is currently
  evicted, `brctl download` is invoked before the extractor reads.
  macOS already auto-downloads on `open()` when TCC is granted, but
  the explicit hand-off turns a network hiccup on a metered link
  into a clean `Could not materialise iCloud placeholder…` WARNING
  instead of a wedged-extractor timeout. No-op on non-macOS hosts
  and best-effort on macOS (a missing `brctl` falls back to the
  existing eviction-tolerance shim from PR #19).
- `corpus-forge service install --apply --launchd` now runs a TCC
  handshake right after `launchctl load` succeeds. A freshly-loaded
  LaunchAgent inherits TCC from launchd itself rather than the
  terminal that installed it; if the agent's grant is missing, the
  daemon would die on the first iCloud read. Surface that
  requirement up-front by probing the configured iCloud roots and,
  on denial, opening System Settings → Full Disk Access with the
  recovery instruction printed to stderr.

### Fixed

- `OpenAIEmbedder.encode` now **bisects on failure** instead of
  giving up on the whole batch. On any 5xx or NaN-laced response,
  the batch is recursively halved until the offending chunk is
  isolated, logged at WARNING with `orig_idx` / `chars` / `sha256`
  (no chunk text — see PII note below), and skipped. The remaining
  good rows still flow through. `OpenAI(max_retries=0)` disables
  the SDK's exponential backoff so failures fast-fail.
  Skipped `chunk_ids` stay in `chunks_missing_embedding` for the
  next pass; callers (`ingest_one`, `embed.backfill_embedder`)
  read `embedder.last_failed_indices` after each `encode` and
  filter their `write_embeddings` pair list accordingly.
  - Non-recoverable 4xx errors (auth, missing model, 400/422) are
    re-raised immediately instead of being bisected — bisection
    can't fix a wrong config, so we surface the real cause.
  - Provider-side row-count mismatches (response returns fewer / more
    rows than the input batch) are also treated as failure for the
    bisection.
  - `embed.backfill_embedder` exits its inner loop on an all-skipped
    batch instead of refetching the same `chunks_missing_embedding`
    rows forever.
  - WARNING log intentionally omits the chunk text preview to avoid
    leaking PII from personal vault content; `sha256` is enough to
    look up the chunk in `corpus.chunks` and reproduce out-of-band.
  Regression coverage: `tests/unit/test_openai_embedder_bisection.py`
  (happy path, single-chunk NaN/5xx, multi-chunk bisection,
  recoverable-vs-non-recoverable triage, row-count mismatch,
  all-skipped loop exit).

## [0.1.0b9] - 2026-05-22

### Fixed

- `corpus-forge ingest --once` no longer crashes with
  `AttributeError: 'ProgressEmitter' object has no attribute
  'remove_task'` when running under Claude Code (or any agent-mode
  invocation that sets the `CLAUDE_CODE` env var). The PR #46
  per-source progress bar teardown path calls
  `progress.remove_task(source_task)`; Rich's `Progress` class
  implements that, but the agent-mode shim in
  `corpus_forge/ui/agent.py` did not. Added the missing no-op
  method for Rich-API parity. Regression test
  `tests/unit/test_progress_emitter_remove_task.py` pins the
  contract: method exists, returns `None`, and is a no-op (the
  emitter's completed counter is preserved across calls).

## [0.1.0b8] - 2026-05-22

### Fixed

- Every "No configuration found" error in `corpus_forge/cli.py` now
  points users at `corpus-forge setup` (the verb that *creates* a
  config), not `corpus-forge migrate` (which needs a config to load
  and itself fails when one is missing). 11 message sites updated,
  spanning `embedder list`, `embedder get`, `dataset list`, `ingest`,
  `embed`, `analyze`, and several admin verbs. Property-#2 of the
  human-friendly CLI spec (`.planning/tdd/e2e_ux_flows.md`): error
  messages name the broken thing AND the fix.
- `corpus-forge mcp serve` no longer surfaces a Rich-formatted
  traceback when the optional `mcp` package isn't installed. A
  pre-flight `import mcp` in the CLI catches `ImportError` and prints a
  single-line install hint
  (`uv tool install 'corpus-forge\[mcp]'` or
  `pip install 'corpus-forge\[mcp]'`) before exiting 1. The Rich
  markup escape on `\[mcp]` keeps the extras specifier from being
  silently eaten as an unknown style tag. Regression test
  `test_missing_mcp_extra_shows_install_hint`.
- MCP `tools/call` responses no longer return `isError: true` with an
  empty content block when a dispatcher throws an uncaught exception.
  The catch-all wrapper in `corpus_forge.mcp.server._call_tool` now
  packs the exception's class name and message into a `TextContent`
  block so real MCP clients (Claude Desktop / Code) surface a useful
  diagnostic instead of a blank error. Regression test
  `TestDispatcherExceptionSurface::test_retriever_builder_failure_surfaces_in_content`.
- Phase M Wave 4 source-nesting bug: doctor's Zotero check no longer
  silently SKIPs sources declared as `plugin = "zotero"` without an
  explicit `[datasets.sources.zotero]` block. `DatasetSourceConfig`
  now default-instantiates `ZoteroSourceConfig()` (local mode, platform-
  default library path) when `plugin == "zotero"` and the nested block
  is absent. Three regression tests in
  `TestZoteroSourceDefault` lock the contract.
- `install.sh` and `install.ps1` now invoke `corpus-forge migrate`
  after the setup wizard so a first-run `ingest`/`embed` doesn't fail
  on an empty DB. The migrate call is failure-tolerant: a non-zero
  exit (Postgres unreachable at install time, etc.) is logged to a
  temp file, warned about, and the installer still exits 0 so the
  user isn't left with a half-installed CLI. The PowerShell path also
  resets `$LASTEXITCODE` on the warn branch and ends with an explicit
  `exit 0` so `iwr | iex` callers don't propagate a stale 1.
- `install.ps1` now always passes `--non-interactive` to
  `corpus-forge setup` (mirrors the bug-#1 fix in #18 for `install.sh`):
  the wizard's stdin was already consumed by the PowerShell prompts,
  so prior re-prompts silently took defaults and discarded user
  answers.
- New smoke suite at `tests/scripts/test_install_sh.py` exercises the
  handoff via a sentinel-extracted `__cf_post_install_handoff` and a
  stubbed `corpus-forge` on PATH: happy path, migrate-failure path,
  corpus-forge-missing path, and `CF_CONFIG` propagation. PowerShell
  mirror test is skipped when `pwsh` isn't on PATH.
- `FilesystemSource.parse` no longer crashes the ingest pass when an
  iCloud Drive (or other network mount) evicts a file between text
  extraction and the SHA-256 hash step. The hash call is wrapped to
  match the extractor's contract — a `FileNotFoundError`/`OSError`
  emits a WARNING (`Could not hash %s — skipping`) and returns
  `None`, so the scan loop continues. Observed against a real
  Obsidian vault on iCloud. Regression test
  `test_parse_returns_none_when_file_evicted_between_extract_and_hash`.
- Per-document failures that originate in the embedder rather than
  the extractor are no longer misattributed as "Extractor failed"
  (PR #46). `_classify_and_log_ingest_error` recognises the
  Ollama-style `"failed to encode response: json: unsupported value:
  NaN"` 5xx and logs it as a WARNING with an actionable hint
  (swap embedders or filter empty-chunk input), and generic
  embedder 5xx as a WARNING. Everything else keeps the historic
  `extract_logger.info("Extractor failed on X: …")` taxonomy so
  existing grep patterns / dashboards still match.
- Per-file progress bars now advance on failure as well as
  success (PR #46). Both `progress.update(..., advance=1)` calls
  moved into a `try/finally` block — previously the `except`
  branch ended with `continue`, so per-file failures silently
  dropped advances. The global bar now reaches 100% even when
  some files fail.

### Changed

- `openai>=1.30` is now a base dependency rather than an opt-in
  `[openai]` extra. `provider = "openai"` in `[[embedders]]` (and
  elsewhere) refers to the OpenAI REST *protocol* — every local
  OpenAI-compatible endpoint (Ollama at `:11434/v1`, vLLM, llama.cpp
  server, LM Studio, …) routes through the same client, so users
  who deliberately opted out of the extra were hitting `openai
  package is required` mid-ingest. The `[openai]` extra is kept as
  an empty alias so existing install commands keep resolving.
- OpenAI-compatible embedder now performs client-side Matryoshka
  truncation when the configured `dimension` is smaller than the
  model's native dim. The `dimensions=` request field is still
  forwarded so servers that honour it (real OpenAI, vLLM) short-
  circuit the work, but local servers that ignore the field
  (Ollama, some llama.cpp builds) get the right shape back via the
  client-side truncate + L2 renormalise path.

### Added

- Richer, realistic code-sample fixtures for the code-embedder lane:
  `tests/fixtures/multi_format_corpus/code/realistic/<lang>/inventory.*`
  adds idiomatic, multi-construct modules (python / typescript / go /
  rust) that all model the same theme — a small in-memory inventory of
  items with quantities — so they're parallel and useful for
  cross-language retrieval comparison. Emitted deterministically by a
  new `build_realistic_code()` in `scripts/build_fixture_corpus.py` and
  guarded by `tests/unit/test_realistic_code_fixtures.py` (pure
  filesystem: each file exists, is > 400 bytes, decodes UTF-8, and
  carries its language's signature construct). Self-authored, synthetic,
  CC0.
- `corpus_forge/eval/embedder_ranking.py` — embedder-ranking eval
  harness. Sweeps candidate embedders from a TOML manifest, scores each
  on the same retrieval gold set, records embed throughput / device /
  peak GPU memory, and emits a ranked leaderboard envelope
  (`eval_kind: "embedder_ranking"`, `primary_metric` default `ndcg@10`).
  The ranking core (`rank_embedders`) takes an injectable `evaluate_fn`
  so it is unit-testable with no model download or DB; the real-wiring
  evaluator (`make_default_evaluator`) and the on-machine run + CLI verb
  are separate RFC boxes.
- `corpus-forge eval embedders --candidates <manifest.toml>` — CLI verb
  wiring the embedder-ranking harness. Reuses the same backend + gold-set
  plumbing as `eval retrieval` (`--gold` resolves a bundled name or
  `.jsonl` path; `--k` sets the retrieval cutoffs), assembles the
  already-ingested `(chunk_id, text)` corpus from the configured backend,
  ranks every candidate in the manifest, and writes the leaderboard
  envelope to `--out`/`--json` (or stdout) with a short ranked table on
  stderr. Requires real models + a populated backend, so the unit suite
  only pins the help surface.
- `docs/embedding-models.md` — per-lane embedding-model
  recommendations: a grounded (HF MCP + live MTEB/MMTEB/CoIR/ViDoRe
  leaderboard) survey of best-in-class embedders across four lanes
  (English text retrieval, code, multilingual, multimodal), each with
  a default / fast-local / API pick mapped onto corpus-forge's
  `sentence_transformers` / `openai` / `model2vec` / CLIP providers.
- `README.md` — brief `## Embedding model recommendations` section: a
  four-lane default / fast-local / API pick table condensed from
  `docs/embedding-models.md`, a non-commercial-license caveat for the
  jina models, and three copy-paste `[[embedders]]` blocks (Qwen3-8B,
  potion-code-16M static fast tier, OpenAI text-embedding-3-large)
  matching `config.example.toml`'s field format.
- `tests/unit/test_cli_human_friendly.py` — first two tests against
  the human-friendly CLI testable properties: (1) doctor's
  `_check_config_present` pins the `corpus-forge setup` recovery
  command in its WARN detail; (2) a static scan of `cli.py` asserts
  every "No configuration found" message names `setup` and never
  `migrate`.
- `tests/integration/test_claude_code_self_ingest_e2e.py` —
  full-pipeline E2E coverage for `ClaudeCodeSource`. Drives the
  parser → conversation chunker → in-memory SQLite backend → fake
  embedder → `HybridRetriever` round-trip against a real (anonymised)
  Claude Code session file checked in under
  `tests/fixtures/claude_code_self_ingest/`. Pins: every parser event
  type produces the right rows or metadata fold (regression for the
  PR #29 permission-mode leak bug), session-link wiring fires during
  full `ingest_one`, retrieval returns chunks for the ingested
  conversation. Closes the first task of RFC
  `rfc-claude-code-self-ingest-e2e` (P0).
- `corpus_forge/sources/_git.py::git_context(path)` — best-effort
  helper that resolves `(commit_sha, branch)` for a given path,
  returning `(None, None)` and never raising when `git` is absent
  from PATH, the path is not inside a git work tree, or any
  subprocess call times out or fails. Detached HEAD surfaces as
  `(sha, None)` rather than `(sha, "HEAD")`. First sub-task of
  RFC `rfc-source-provenance-git-and-lines` (P0); the helper will
  be wired into `FilesystemSource` + `ClaudeCodeSource` and through
  to per-chunk metadata in subsequent PRs.
- `ClaudeCodeSource` now propagates the session-level `git_branch`
  (captured from the JSONL session file's `gitBranch` field) onto
  every `RawMessage.metadata` entry under the key `git_branch`. The
  fan-out is a post-process step after the parse loop, so even
  messages whose JSONL line predates the line that carries
  `gitBranch` still receive the value. Uses `setdefault` so any
  future per-turn branch override is preserved. Foundation for the
  per-chunk `git_branch` provenance column landing in subsequent
  RFC `rfc-source-provenance-git-and-lines` PRs.
- Alembic revision `0016_chunk_provenance` adds five nullable
  provenance columns to `corpus.chunks` / `chunks`: `file_path`,
  `line_start`, `line_end`, `git_commit`, `git_branch`. Existing
  rows survive untouched — the columns stay NULL until the
  source/chunker re-emits them on the next ingest pass. Postgres
  path uses `ADD COLUMN IF NOT EXISTS`; SQLite uses a
  `PRAGMA table_info` probe — both fully idempotent. Foundation
  for the chunker write paths, backend upsert paths, and MCP
  `get_source_file_context` tool landing in subsequent RFC
  `rfc-source-provenance-git-and-lines` PRs.
- pgvector backend now supports native **4096-dim halfvec** indexes
  alongside the existing `vector_cosine_ops` (≤2000d) and projected-
  halfvec (>2000d, ≤4096d) strategies. `embedder repair-indexes`
  gains a third detection axis so it can audit + rebuild any
  drifted HNSW index. `corpus-forge doctor` reports the per-embedder
  index strategy in its `embedder_indexes` check.
- **Full chat-history coverage** for every agent CLI corpus-forge
  can reach (PR #29). `gemini_cli`, `codex_cli`, `chatgpt_export`,
  and `jsonl_chat` are now reachable from config (`_instantiate_source`
  previously raised "Unknown source plugin" even though the code
  existed). New `DatasetSourceConfig` fields — `chats_root`,
  `sessions_root`, `export_root`, `path`, plus `history_path` for
  Claude Code — each name the canonical default in their
  missing-path `ValueError`. Parser-level upgrades:
  - **Claude Code:** filters JSONL lines by `type` so
    `permission-mode` / `file-history-snapshot` / `ai-title` /
    `last-prompt` / `pr-link` events stop being ingested as empty
    assistant turns. Metadata-only events fold into
    `RawConversation.metadata` (titles, permission transitions,
    PR links, file-history snapshots). `tool_use` / `tool_result`
    blocks extracted into structured `tool_calls` / `tool_results`.
    Optional `history_path` ingests `~/.claude/history.jsonl` as a
    separate `claude-code-history://<sid>` conversation per
    session, distinct enough not to false-link to feedback rows.
  - **OpenCode:** new `scan()` reconstructs full conversations from
    the modern `session/info` + `session/message` + `session/part`
    triple-store. Legacy flat `message.json` still parses.
  - **Codex CLI:** `discover()` switches to `rglob` for the modern
    `sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` shard layout
    and handles the typed event stream (`session_meta`,
    `event_msg`, `reasoning_summary`, `function_call` /
    `function_call_output`). Legacy `{role, content, ts}` still
    parses.
  - All source constructors coerce `str | Path` to `Path` and
    `.expanduser()` once, so str-typed `ExpandedPath` values from
    pydantic config don't crash `.iterdir()` mid-ingest.
  - `_SOURCE_URI_TO_CLIENT` learns `codex-cli://`,
    `chatgpt-export://`, `jsonl-chat://`, and
    `claude-code-history://` so feedback-session linking works for
    every chat client.
  - Four new test files (20 tests) pin: typed-event filtering,
    tool-call extraction, history.jsonl ingestion, codex modern
    shard layout, opencode triple-store reconstruction, and
    `_instantiate_source` wiring for every plugin.
- **Wall-clock time estimator** for `corpus-forge estimate` (PR #44)
  — per-phase prediction (`scan` / `extract` / `chunk` / `embed` /
  `db_write`) alongside the existing storage estimator. Heuristic
  constants ship out of the box (~±50%); a per-host runtime
  profile self-calibrates on real `ingest`/`embed` runs via an
  EWMA so subsequent estimates converge on observed throughput
  (~±20% after a handful of samples). Surfaces:
  - CLI: new "Estimated wall-clock" table + `time:` key in
    `--json` / agent JSONL output, with a footer line that
    explains whether the number is heuristic-only, hybrid, or
    calibrated from N past samples.
  - MCP `estimate_sync_size`: sibling `time:` block alongside
    `estimate:`, same `schema_version=1` contract.
  - `corpus-forge ingest --once`: one-shot ETA INFO log at
    startup, summed across filesystem-rooted sources.

  Calibration is best-effort and never blocks ingest — profile
  reads/writes degrade silently to heuristic-only mode on any
  I/O failure or corrupt schema_version.
- `scripts/check-pyrefly.sh` wrapper that exits 1 on any reported
  error so `make typecheck` and the `pyrefly` pre-commit hook stop
  silently passing (PR #45). `pre-commit` config gains two stages:
  - `pre-commit` (fast, auto-fixing): `ruff format`,
    `ruff check --fix`, per-file `pyrefly` scoped to
    `^corpus_forge/`.
  - `pre-push` (strict, no auto-fix): `ruff format --check`,
    `ruff check`, project-wide `pyrefly`. Mirrors the CI
    `quality` job step-for-step.

  The previous `unit-tests` pre-push hook is dropped — it
  depended on every optional extra being installed and blocked
  legitimate pushes from partial dev envs; the CI matrix already
  exercises tests. `make dev` installs both hook stages.
- **Live ETA progress bar for `corpus-forge ingest`** (PR #46).
  Replaces the unbounded `⠼ Ingest 0:00:01` spinner with a
  bounded Rich progress bar that renders `X/Y` complete +
  remaining time, computed from the planner walk that already
  produced the startup ETA. Layout while ingest runs:

  ```
  Ingest (all sources) ━━━━━━━━ 4,321/250,003 0:01:30 1d 11h 30m
    filesystem         ━━━━━━━━     4,321/51,001 0:01:30 0:08:13
  ```

  The top bar persists across every source; the indented
  per-source bar appears when each source starts and is removed
  when it finishes, so exactly one source task is visible at
  any time under the persistent global view. Sources without a
  resolvable filesystem root (API-only plugins) keep the
  previous unbounded spinner. The previous `_log_ingest_eta`
  helper was renamed to `_plan_ingest` and now returns a
  per-source file-count map alongside emitting the startup ETA.
- Multilingual prose fixture family under
  `tests/fixtures/multi_format_corpus/prose/multilingual/` — one
  markdown doc per language (`es`/`fr`/`de`/`ru`/`ja`/`ar`) spanning
  the Latin, Cyrillic, CJK, and Arabic (RTL) scripts, so the synthetic
  fixture corpus has non-English coverage for multilingual ingest and
  (later) multilingual embedder ranking. All six docs are original,
  project-authored CC0-1.0 text emitted by the new
  `build_multilingual_prose()` builder in
  `scripts/build_fixture_corpus.py` (deterministic — no timestamps,
  numbers, or randomness). New `tests/fixtures/multi_format_corpus/ATTRIBUTION.md`
  records the public-domain provenance; `tests/unit/test_multilingual_fixtures.py`
  is a pure-filesystem regression test asserting the six files exist,
  decode as UTF-8, and carry real Cyrillic / CJK / Arabic codepoints.
- Two deterministic, **text-free** synthetic images for the CLIP /
  multimodal lane — `images/scene-landscape.png` (gradient landscape
  with sun + hills) and `images/abstract-blocks.png` (4×4 colour-block
  grid) — generated by `build_clip_images()` in
  `scripts/build_fixture_corpus.py` (drawn via `PIL.ImageDraw`, no
  network fetch, byte-stable across regens). Covered by a new
  pure-filesystem unit test (`tests/unit/test_clip_image_fixtures.py`)
  and a CLIP-gated e2e assertion that the two embed distinctly
  (`tests/integration/test_multimodal_embed_e2e.py`). The P0
  multi-format ingest test's `_UNINGESTABLE` set excludes both, since
  the default `ExtractionConfig` registers no image extractor.
- `examples/sample-corpus/` — a small, self-authored (CC0) mini
  knowledge base for the fictional "Skycast" weather CLI (markdown
  notes, FAQ, CSV metrics, a TOML config, and a ruff-clean Python
  module). Gives new users a ready-made corpus to point corpus-forge
  at while following the README Quickstart. Guarded by
  `tests/smoke/test_sample_corpus_present.py`.

### Docs

- README `## Install` now has an **Install with Claude (copy-paste
  prompt)** subsection — a short provider-neutral prompt you can paste
  into Claude Code / Desktop / an Agent-SDK client to have the
  assistant do the whole install + MCP wiring + skill registration +
  first-run sanity by following `CLAUDE.md` (and `AGENTS.md` /
  `GEMINI.md` for non-Claude clients).

### Changed

- Refactored `register_default_extractors` in
  `corpus_forge/extractors/registry.py` to drop its cyclomatic
  complexity below the lint gate (C901 36 → ≤10, PLR0912 35 → ≤12,
  PLR0915 78 → ≤50). The eight identical no-arg branches (markdown,
  plaintext, structured, subtitle, html, epub, office, notebook)
  collapse into a `_SIMPLE_EXTRACTORS_PRE_PDF` / `_SIMPLE_EXTRACTORS_POST_PDF`
  data table driven by a single loop, and each special case (pdf, csv,
  code, image, audio/video) moves verbatim into a dedicated
  `_register_*` helper. Behavior-preserving: same extractors registered
  under the same flags, in the same order, with the same skip-on-
  `ImportError` semantics — proven by identical before/after test counts.

## [0.1.0b7] - 2026-05-20

### Added

#### Phase O — EDA + corpus cleaning (alembic `0012_analyze_signals`)

- `[analyze]` optional extra: `scikit-learn`, `hdbscan`, `umap-learn`,
  `bertopic`, `datasketch`, `fasttext-langdetect` (POSIX) +
  `langdetect` (cross-platform fallback). All heavy modules are
  lazy-imported inside function bodies — `corpus-forge --help` cold
  start stays at ~34 ms.
- `corpus_forge/analyze/` modules:
  - `stats` — p50/p95/mean/min/max token counts, length histograms.
  - `dedup` — exact (content-hash) + near-duplicate (MinHash LSH).
  - `language` — ISO-code + confidence via fasttext/langdetect dispatch.
  - `drift` — KS over token length, JS over embedding centroids.
  - `topics` — BERTopic with raw-HDBSCAN fallback + c-TF-IDF top terms.
  - `quality` — heuristic by default, joblib model when present at
    `~/.cache/corpus-forge/models/quality.joblib`.
- Curation selector extension: per-chunk dual-weight scheme. 5-weight
  (`learned_quality` added) when `chunk_quality_signals` has a row;
  4-weight (unchanged) otherwise. **The 47 pre-existing
  `test_curation_selector` tests stay byte-identical**, so MCP callers
  depending on `next_curation_target` ordering see no flip until
  `analyze quality` has run.
- New `corpus-forge analyze` CLI subgroup —
  `stats|duplicates|topics|distribution|drift|quality`. Reports land
  at `~/.cache/corpus-forge/reports/<ts>/` and respect
  `CORPUS_FORGE_REPORT_DIR`.
- Four new **read-only** MCP tools above the `writes_enabled` gate:
  `analyze_corpus`, `find_duplicates`, `cluster_topics`,
  `score_quality`.

#### Phase P — RAG/CAG retrieval feedback (alembic `0013_search_sessions`)

- New schema: `search_sessions(id, query, dataset_id, started_at)` and
  `search_result_events(id, session_id FK, chunk_id, signal, value,
  source, created_at)`.
- `HybridRetriever.search()` returns a `SearchResponse` that
  **subclasses `list`** — existing callers iterating/indexing the
  return value still work; new callers can read `.query_id` and
  `.results`. MCP `search` surfaces `query_id` in the response.
- `rate_search_result` MCP write tool — auto-creates a session for
  unknown `query_id`, persists `replacement_chunk_id` as a preference
  signal for the learned reranker.
- `LearnedReranker` — sklearn LogisticRegression trained on rated
  events; conforms to the existing `Reranker` protocol. Train via
  `corpus-forge analyze quality --train-reranker`.
- `corpus_forge/cag/` — precomputed cache builder + hybrid CAG/RAG
  selector. Cache key =
  `sha256((dataset_id, sorted(content_hash_set), template_name))`,
  so `commit_curation` triggers deterministic targeted invalidation
  (best-effort; failure does not fail the commit). corpus-forge ships
  CAG as a corpus-side cache builder, **not** an inference server.
- `corpus-forge eval rag` + `eval cag` CLI subcommands with
  configurable LLM-judge endpoint (local Ollama default; OpenAI-compat
  remote supported). `corpus_forge/eval/judge_mock.py` ships a
  SHA-256-keyed deterministic mock judge for CI byte-stability.

#### Phase Q — Explicit feedback capture + SDFT-format preprocessing (alembic `0014_sdft_demonstrations`)

Grounded in [Shenfeld et al., arXiv:2601.19897](https://arxiv.org/abs/2601.19897).
**corpus-forge does NOT train, fine-tune, or sample models** — it
captures feedback and emits training-ready data; downstream consumers
train. A static-analysis test enforces the boundary
(`tests/unit/test_sdft_no_inference.py`).

- New schema: `sdft_demonstrations(id, dataset_id FK, query,
  student_messages, teacher_messages, target, source, trace_id,
  content_hash UNIQUE, created_at)` with indexes on
  `(dataset_id, source)` and `(trace_id)`.
- `record_demonstration` MCP write tool with content-hash dedup
  (sha256 over a canonical-JSON payload). Idempotent — re-issued
  identical writes return the existing id with `deduped=True`.
- Capture hooks: `commit_curation` description corrections write a
  demo with `source=curation_commit`; `rate_search_result` negative
  signal + replacement_chunk_id writes with `source=rate_search_result`.
  Pure metadata fixes do NOT fire the hook (low-signal filter).
- Per-chat-client skill packs — all four reference the same MCP tool
  set; `test_skill_pack_consistency.py` rot-detects drift:
  - `.claude/skills/corpus-curate/SKILL.md` — extended
  - `.gemini/extensions/corpus-curate.toml` + `PROMPT.md` (Gemini CLI)
  - `opencode/commands/corpus-curate.md` (OpenCode)
  - `codex/agents/corpus-curate.md` (Codex)
- `corpus-forge feedback` CLI subgroup — `start|resume|list-sessions
  |export-session`. `prompt_toolkit`-based TUI for offline curators
  plus a scripted `--no-tui` mode for headless / CI usage. Session
  state persists to `~/.cache/corpus-forge/feedback/session-<id>.json`
  with idempotent resume.
- `corpus-forge export sdft` — chat-templated JSONL/Parquet artifact
  loadable via `datasets.load_dataset(...)`. Deterministic train /
  held-out split via `sha256(content_hash) % 100` bucketing.
  `--include-sources` filters by `SDFTSource` enum (covers
  `curation_commit`, `rate_search_result`, `record_demonstration`,
  `cli_feedback`, `claude_code`, `gemini`, `opencode`, `codex`).
  Golden-file regression locks `export_chat` and
  `export_feedback_pairs` schemas — no row-shape drift.
- `corpus-forge eval distill` — preprocessing-health metrics only:
  coverage, source mix histogram, template fidelity, p50/p95 token
  stats. **Not** a training-quality metric (no judge calls, no model
  sampling).

### Fixed

- `corpus_forge/sdft/capture.py` Postgres branch: the
  `INSERT ... ON CONFLICT DO NOTHING RETURNING id` path never called
  `conn.commit()`. The row was rolled back when
  `backend._get_connection`'s context manager closed the connection,
  so callers received a phantom `demonstration_id` that didn't exist
  on disk. Added the commit; pinned with four regression tests at
  `tests/integration/test_sdft_capture_pg_commit_regression.py`
  (durability across connection close, dedup-branch round trip,
  white-box commit-counter spy, full PostgresBackend round trip).
- Bumped `astral-sh/setup-uv@v5 → v6` and `actions/cache@v4 → v5` in
  `.github/actions/setup-uv/action.yml` to clear the Node.js 20
  deprecation warning ahead of GitHub's 2026-09-16 forced removal.

### Migration order

`0012_analyze_signals` → `0013_search_sessions` →
`0014_sdft_demonstrations`. Downgrade functions are `pass` (matches
the project's forward-only convention from `0008` / `0010` / `0011`).

## [0.1.0b6] - 2026-05-19

### Added

#### Phase N — Retrieval Quality (semble technique extraction)

Carries out Phase M Wave 5's recommendation to extract three
techniques from MinishLab/semble rather than swap retrievers. All
three features default OFF — deployments opt in explicitly via
`RetrievalConfig` flags.

- **Wave 0 — broadened bench.** Vendored a `pallets/flask` snapshot
  (`tests/fixtures/external/flask-snapshot/`, BSD-3-Clause, pinned
  to commit `954f5684`) as a second bench corpus alongside this repo.
  Grew `tests/perf/data/semble_queries.jsonl` from 25 → 61 hand-
  authored queries with byte-offset ground truth. Captured the
  Phase N baseline at `tests/perf/out/phase_n_baseline.json`. New
  gated bench at `tests/perf/test_phase_n_bench.py`
  (`CF_PHASE_N_BENCH=1`).
- **Wave 1 — adaptive lexical-weight bump on symbol queries.** New
  `corpus_forge.retrieval.query_shape.is_symbol_shaped(query)`
  heuristic (catches `Foo.bar`, `Foo::bar`, `_private`,
  `setUp`, `MyClass`, `snake_case_name`; rejects natural language).
  `HybridRetriever.search` lowers the effective alpha to
  `RetrievalConfig.symbol_query_alpha` (default 0.3) when
  `adaptive_lexical_weight=True` and the query is symbol-shaped on
  alpha fusion. Reranker downstream washes out the fusion-stage
  signal in practice — Wave 1 ships the lever, the lift materialises
  via composition with Wave 2.
- **Wave 2 — definition boosts on retrieval.** Code chunker now tags
  every AST-walk chunk with `metadata.is_definition = True` and
  `metadata.definition_kind` (`Function` / `Class` / `Method` /
  `Block`). HybridRetriever applies a score multiplier to definition
  chunks whose `metadata.name` matches a query token, BOTH
  pre-rerank (`definition_boost_factor_pre_rerank`, default 1.5) AND
  post-rerank (`definition_boost_factor_post_rerank`, default 1.2).
  Boost is gated on `is_symbol_shaped(query)` to avoid collateral
  damage on natural-language queries that happen to contain
  identifier-like words. Composite result with Wave 1: **identifier
  MRR@10 +0.1225** (0.466 → 0.588), zero per-category regression vs
  control on the broadened bench.
- **Wave 3 — static-tier fast path.** New `model2vec` embedder
  provider (`corpus_forge/embedders/model2vec.py`) for
  `minishlab/potion-code-16M` (256-dim, MIT, ~16 MB, CPU-fast). New
  `SearchOptions.fast_tier_mode ∈ {skip, shortcut, only}`. Shortcut
  mode uses the fast embedder as a candidate generator
  (`fast_tier_top_n`, default 200) for the main embedder's dense +
  lexical + rerank pipeline; only mode bypasses lexical + rerank
  entirely for latency-sensitive paths. Backend `search_dense` and
  `search_lexical` gained a `chunk_ids: frozenset[int] | None`
  keyword arg to support the candidate-pool restriction (on both
  Postgres and SQLite). Bench result: **shortcut +0.07 concept
  MRR@10**, **only mode 24.6 ms p50** (50× drop from 1.2 s) with
  quality within the looser Pareto floor. The cross-encoder reranker
  dominates p50, so shortcut-mode's value is quality preservation
  under candidate restriction, not latency — documented in the
  retriever's docstring.

### Changed

- **Embedder fingerprint drift detection** unchanged in code but
  re-verified during Wave 3 pre-flight: silently skips embedders the
  backend has not yet seen, so adding the new `model2vec` provider
  does NOT false-positive on a user's main embedder.

### Deps

- `model2vec>=0.5` under the NEW optional extra `[fast-tier]`. Core
  install size unchanged.

## [0.1.0b5] - 2026-05-19

### Added

#### Phase M — Corpusignore lifecycle, scan perf, Zotero, semble spike

- **Managed `.corpusignore` lifecycle** — `corpus-forge setup` now
  offers to create a feature-aware `.corpusignore` at each data root.
  The file carries a sentinel-delimited managed block whose patterns
  derive from active features: always-on lockfiles / build artifacts /
  sourcemaps / Apple metadata / archives; audio + video patterns
  added when `whisper.backend == "none"`; raw-image patterns added
  when no image extractor is configured. Conservative — PDFs,
  notebooks, and source code are never auto-ignored. User edits
  outside the sentinels survive resync.
  (`corpus_forge/ignore_defaults.py`, `corpus_forge/ignore_lifecycle.py`)
- **`corpus-forge doctor`** — new `corpusignore` check validates
  syntax, detects managed-block drift vs current features, warns on
  missing files at configured FS roots.
- **`corpus-forge ignore` subcommand** — list, add, remove, edit,
  validate, sync, and init `.corpusignore` files at local or global
  scope. Refuses to mutate the managed region (instructs the user to
  flip the underlying feature). Atomic writes; backup-and-rollback
  on `$EDITOR` syntax errors.
  (`corpus_forge/admin/ignore.py`)
- **Five new MCP tools** wrapping the same surface: `list_ignore`,
  `validate_ignore` (always available, read-only); `add_ignore_pattern`,
  `remove_ignore_pattern`, `sync_ignore` (`writes_enabled`-gated).
- **Zotero library source plugin** — local `zotero.sqlite` (read-only
  via `mode=ro&immutable=1`, safe with Zotero running), Zotero Web API
  at `api.zotero.org`, or both with local-wins reconciliation. PDF
  attachments flow through `PdfDigitalExtractor`; Zotero metadata
  (authors, year, DOI, collection, tags, abstract) propagates into
  chunk metadata. Doctor and MCP gain Zotero-aware tools.
  (`corpus_forge/zotero/`, `corpus_forge/sources/zotero.py`,
  `mcp__corpus-forge__zotero_sync`)
- **`[scan]` config block** — `extra_skip_dirs`, `follow_symlinks`,
  `workers` (concurrency reserved for a follow-up wave).

### Changed

- **Unified file walker** (`corpus_forge/scanner/walker.py`) replaces
  the two divergent slow walkers (`estimate._walk` and
  `FilesystemSource.discover`). `os.scandir`-based with descent-time
  directory pruning and extension short-circuit *before* statting.
  Synthetic-tree bench measured **3.29× speedup** with 99% of
  baseline-skip subtrees never entered (144 of ~2,200 dirs scanned on
  a 10k-file fixture).
- `IgnoreStack` gains `directory_pruned(rel_path)` — conservative
  algorithm: any negation anywhere in the stack disables directory
  pruning, otherwise prune iff a non-negated pattern matches. Strict
  gitignore parent-exclusion semantics preserved (a `!parent/child`
  negation cannot re-include children when `parent/` is ignored).
- `corpus-forge estimate` and ingest of the `filesystem` source plugin
  both delegate to the new walker; size/count behavior is unchanged
  (parity-tested across five fixture trees including negation-heavy
  ignore stacks).

### Research

- **semble investigation spike** — time-boxed measurement of
  MinishLab/semble against corpus-forge's `HybridRetriever` on this
  repo with 25 hand-authored queries. semble crushes identifier
  searches (MRR@10 0.85 vs 0.40) at ~880× lower p50 latency, but
  loses on concept, error, and call-site queries. Decision:
  **extract techniques** (adaptive lexical-weight bump on symbol
  queries, definition boosts, optional model2vec static-embedding
  fast tier) in a follow-up phase. semble is not added as a
  dependency. (`.planning/tdd/phase_m_wave5_semble.md`,
  `experiments/semble_adapter.py`)

### Deps

- `httpx>=0.27` (core, for Zotero Web API)
- `respx>=0.21` (dev, for Zotero web-client tests)

## [0.1.0b4] - 2026-05-18

(Reissue of `0.1.0b3` — that tag's release pipeline failed on a missed
test version-string pin. No code differences vs the failed b3 tag other
than the version bump and the corrected wheel-metadata test.)

### Added

#### Phase L — CLI beautification & diagnostics

- **`corpus_forge/ui/` package** (theme, console, banner, progress,
  prompts, agent). Brand palette pinned to the logo's ember (`#ff8a3d`)
  / deep ember (`#b83205`) with ANSI named state colors. Rounded-box
  banner on `setup` and `doctor`. `--no-color`, `--light` flags.
- **Centralized rotating logging** (`corpus_forge/logging_config.py`)
  — file at `~/.cache/corpus-forge/logs/<component>.log` (10 MB × 5) +
  themed stderr `RichHandler` + 200-entry in-memory ring buffer for
  bug-reports. New global flags: `--verbose/-v`, `--quiet/-q`,
  `--agent`, `--background/-b`.
- **`corpus-forge setup --quick`** — minimal-prompt wizard (backend,
  Ollama URL probe, embedder, first dataset).
- **`corpus-forge doctor --json`** — structured doctor output for
  agents / scripts. Adds a new `daemon_activity` check.
- **`corpus-forge estimate`** now reports wall-clock scan time + scan
  rate + pending-files breakdown (documents-not-chunked,
  chunks-missing-embedding).
- **Progress bars on every long op** (`ingest --once`, `embed`,
  `sync pull --once`, `sync push`, `estimate`) via a shared
  `ui.progress.make_progress` factory with bookending logger lines.
- **Embedder fingerprint drift detection**
  (`corpus_forge/embedders/fingerprint.py`) with a 3-way prompt
  (now/later/skip) on setup/ingest/embed; daemon emits a warning only.
  Drift state persisted to `~/.cache/corpus-forge/state/`.
- **`corpus-forge bug-report`** — zipped diagnostics bundle
  (manifest.json, doctor.json, redacted config.toml, log tails,
  recent-events ring buffer flush, env, deps, db summary,
  service status). Pre-fills a GitHub issue URL. Redactor module
  (`corpus_forge/diagnostics/redact.py`) covers DSN, API keys, Bearer
  tokens, password fields.
- **`corpus-forge logs path|tail|clear`** — sibling diagnostics
  surface; `tail --follow` polls at 250 ms, themed by log level.
- **Admin CRUD command groups**: `config`, `embedder`, `ollama`,
  `dataset`, `source`. Dotted-path config get/set/show/unset/edit via
  `tomlkit`. Ollama `list/get/pull/set-url/test` (streamed pull progress).
  Embedder `list/get/add/remove/set-active/test`. Dataset / source CRUD.
- **`corpus-forge service` lifecycle group**:
  `status/start/stop/restart/logs/install/uninstall`. Generates user-scope
  systemd unit (Linux) / launchd plist (macOS) / `schtasks` argv
  (Windows). The bare `daemon` command is now a deprecated alias for
  `service start`.
- **Project-wide "stay attached, unless `-b`" convention**: every
  long-running side-effect command (rerun-embed, daemon start,
  ollama pull, source ingest) defaults foreground with live progress
  and SIGINT forwarding; `-b` / `--background` detaches via
  `subprocess.Popen` and writes a pid file.
- **Agent-mode detection + JSONL emission** (`corpus_forge/ui/agent.py`)
  mirrors `cli/cli`'s `internal/agents/detect.go`. Recognised signals:
  `AI_AGENT`, `AGENT=amp`, `CODEX_*`, `GEMINI_CLI`, `COPILOT_CLI`,
  `OPENCODE`, `CLAUDECODE`, plus MCP stdio carve-out and explicit
  `--agent <type>` / `CF_AGENT`. When active: every command emits one
  `command.start` and one terminal `result|error` JSONL event on
  stdout; banners/progress/prompts suppress or emit structured events;
  logs route through an `AgentLogHandler`. `corpus-forge capabilities`
  introspects the registered Typer commands for agent discovery.

#### Phase K — .corpusignore

- `corpus-forge estimate` now honors a gitignore-subset `.corpusignore`
  file at the scan root (auto-detect) or at a path passed via the new
  `--ignore-file PATH`. New CLI flags: `--ignore-file`, `--no-ignore-file`
  (disable local), `--no-global-ignore` (disable global). `--ignore-file`
  and `--no-ignore-file` are mutually exclusive.
- The MCP `estimate_sync_size` tool gains `ignore_file` (string; empty
  string disables local; absent → auto-detect) and `disable_global_ignore`
  (boolean) args. Same semantics as the CLI flags.
- Global ignore file at `~/.config/corpus-forge/ignore` (mirrors
  git's `~/.config/git/ignore` convention). Overridable via the
  `CF_GLOBAL_IGNORE_FILE` env var; empty-string value disables the
  global lookup.
- Hard-coded `_SKIP_DIR_NAMES` (`.git`, `node_modules`, `__pycache__`,
  `.venv`, …) remain absolute — `.corpusignore` negations cannot un-skip
  a baseline entry.
- `.corpusignore.example` ships at the repo root with sensible
  defaults (Apple metadata, Photos libraries, large media, common
  backup dirs).
- New module `corpus_forge/ignore.py` exposes `CorpusIgnore`,
  `IgnoreStack`, `load_global_ignore`, `load_local_ignore` for callers
  that want the same matcher (K2 will wire this into `FilesystemSource`
  and `MarkdownVaultSource` so estimate and ingest agree).

### Changed

#### Phase L — CLI beautification & diagnostics

- Every `typer.echo` / `typer.secho` call site in `corpus_forge/cli.py`
  routes through `corpus_forge.ui.*` helpers. Static test
  (`tests/cli/test_no_typer_echo.py`) locks the refactor against drift.
- `corpus-forge sync status` shows the embed-worker pid status.
- Backends gain `find_embedder_row_by_name`, `count_existing_embeddings`,
  `update_embedder_config_blob` for the fingerprint flow, and
  `count_chunks_missing_embedding`, `pending_documents` for the
  estimate-pending breakdown.
- `corpus-forge daemon` deprecation alias forwards to `service start`.

#### Phase K — .corpusignore

- README "Install" section moved above "Quickstart" so users land on
  install before being asked to run shell commands. No content edits to
  either section — just an order swap and a "drop a `.corpusignore`"
  one-liner inserted into the Quickstart numbered list.

### Fixed

- Windows portability: `signal.SIGKILL` fallback to `SIGTERM` +
  `TerminateProcess` for the service-stop escalation path; atomic
  marker writes use linear backoff against Windows' file-replace deny;
  redactor reads/writes use explicit `encoding="utf-8"` so the
  `«redacted»` guillemets round-trip on cp1252 hosts.

## [0.1.0b2] - 2026-05-17

### Added

#### Phase J — Living Corpus
- `corpus-forge estimate <path>` CLI (new `corpus_forge/estimate.py`
  module + Typer command) — predicts the Postgres storage footprint of
  syncing a folder without touching the database. Per-extractor file
  counts and per-embedder embedding-row sizing including pgvector HNSW
  overhead (35 %) and btree-index overhead (~80 B / row). Human output
  by default; `--json` emits the `SyncEstimate` dataclass under stable
  `schema_version = 1`. `--compression-ratio` models LZ4-toasted text
  columns. New `[estimate]` config block with `compression_ratio`
  (default `1.0`; lower it to model TOAST compression on
  `documents.text` / `chunks.text`).
- `estimate_sync_size` MCP tool — same surface as the CLI, available to
  any MCP-connected assistant. Read-only; no `writes_enabled` flag
  required. Args: `{path, dataset?, embedders?, compression_ratio?}`.
  Returns the same `SyncEstimate` JSON shape with `schema_version = 1`.
- `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` at the repo root — vendor-
  specific (Claude Code / Desktop / API; Gemini CLI / Code Assist) and
  vendor-neutral (OpenCode, Cursor, Zed, Continue, Cline, any MCP-
  speaking client) setup guides covering install → configure →
  migrate → wire MCP → register skills → first-run sanity →
  curation-loop playbook → troubleshooting. README cross-links via a
  new "For AI assistants" section (J3).
- Data-curation chat skill (Claude / Gemini / OpenCode / AGENTS.md
  generic recipe) — pulls low-confidence or metadata-poor entries,
  facilitates a chat to improve them, and commits changes via MCP. New
  module `corpus_forge/curation/` (selector + shared chat-loop prompt
  template). New MCP tools: `next_curation_target` /
  `next_curation_batch` (read-only; both available regardless of the
  `writes_enabled` gate) and `commit_curation` (gated by
  `writes_enabled`; composes the existing
  `add_label`/`remove_label`/`set_metadata`/`set_description`/`add_feedback`
  write surface in one call). Skill assets land under
  `.claude/skills/corpus-curate/`, `.opencode/command/corpus-curate.md`,
  and the greenfield `.gemini/agents/corpus-curate.md`. Selector score
  formula: classifier_confidence_deficit × 0.35 + missing_metadata ×
  0.30 + ranker_elevation × 0.25 + freshness × 0.10, normalised to
  [0, 1]; the reranker leg reuses the existing `Reranker` protocol
  (cross_encoder or ollama) so the local-or-remote URL invariant
  carries through unchanged.

### Changed

#### Phase J — Living Corpus
- README reframed around "Chat with your data. Forge a living, trainable
  corpus." Training-data export stays the headline deliverable, framed
  as the outcome of an active corpus rather than a one-shot ETL job.
  New "Human-in-the-loop curation" bullet under "Why corpus-forge."
  Quickstart now shows `corpus-forge estimate <path>` between `migrate`
  and `ingest`, and the curation skill flow before `export`. New "For
  AI assistants" H2 cross-linking to `CLAUDE.md` / `GEMINI.md` /
  `AGENTS.md`. The MCP tool table now lists every tool with its
  `writes_enabled` gate. Banner alt-text updated to match.

#### Phase D — Universal multi-format ingest (waves 0–6)
- `Extractor` protocol (`corpus_forge/extractors/base.py`) + `ExtractorRegistry`
  with a per-extension lookup table and a second-pass `supported_filenames`
  fallback for the extension-less long tail (`Makefile`, `Dockerfile`, …).
- Seven document extractors landed under `corpus_forge/extractors/`:
  `PdfDigitalExtractor` (pymupdf4llm rag-helper), `HtmlExtractor`
  (readability-lxml + markdownify), `EpubExtractor` (ebooklib), `OfficeExtractor`
  (Docling for `.docx`/`.pptx`/`.xlsx`), `NotebookExtractor` (jupytext),
  `CsvExtractor` (pandas → markdown table, row-capped), and a 45+ extension
  `CodeExtractor` (tree-sitter-language-pack).
- `PassthroughMarkdownExtractor`, `PlainTextExtractor`, `StructuredDataExtractor`
  (`.json`/`.yaml`/`.toml`), `SubtitleExtractor` (`.srt`/`.vtt`).
- `FilesystemSource` — heterogeneous-tree walker that dispatches every file
  through the extractor registry. New `[[datasets.sources]]` plugin `filesystem`.
- `ChunkerDispatcher` — picks the per-document chunker from each
  `ExtractedDocument.metadata.chunker_hint`. `CodeChunker` (`chunkers/code.py`):
  tree-sitter AST walk with size-bounding + overlap, falling back to a brace-/
  blank-line byte chunker when the grammar is unavailable.
- New `[code]`, `[multi-format]`, and `[ocr]` optional extras. License posture
  documented in the README's "Distribution / licensing" section — `[multi-format]`
  AGPL-binds; `[code]` and `[ocr]` stay permissive.
- **P1 — Vision/OCR (waves 4–6).** `VLMBackend` protocol + `OllamaVLM`
  (local, `qwen2.5vl:7b` default) + `MistralOCR` (remote, `mistral-ocr-2503`).
  `PdfDigitalExtractor` Tier-1 → Tier-2 escalation on sparse text layers;
  `ImageExtractor` for `.png`/`.jpg`/`.tif`/`.bmp`/`.webp`/`.heic`. Failure
  ladder: missing poppler → ERROR + Tier-1 fallback; VLM timeout on a page →
  placeholder, remaining pages continue. Documented in `docs/architecture.md`.

#### Phase E — Document classification (rule → LLM chain)
- New `Classifier` protocol (`corpus_forge/classifiers/`) + `ClassifierRegistry`
  with ordered dispatch and the `tuple[str, ClassLabel] | None` return shape
  that distinguishes `classifier:rule` from `classifier:llm` on
  `document_labels.source`.
- `RuleBasedClassifier` (stdlib, microseconds/doc) — fast path covering the
  9-value taxonomy (`code`, `chat`, `book`, `textbook`, `paper`, `article`,
  `reference`, `note`, `other`) via format-label + path + body heuristics.
- `LLMClassifier` (Ollama `qwen2.5:7b-instruct` default; `POST /api/generate`
  with `format=json`, head+tail excerpt). Local-or-remote URL via
  `classifier.llm_url`; same swap-the-URL principle as `vlm.ollama_url`.
- New `corpus-forge classify` CLI with cost-guard preflight, `--dry-run`,
  `--limit`, `--json`, `--reclassify`, and `--classifier <name>` (bypass
  the chain).
- Alembic `0010_document_label_confidence` adds the `confidence REAL`
  column to `document_labels`.

#### Phase F — Content-defined chunking (FastCDC)
- New `CDCChunker` (`corpus_forge/chunkers/cdc.py`) — pure-Python FastCDC
  rolling-hash boundaries (MIT). Replaces positional slicing for prose classes
  (`book`/`textbook`/`paper`/`article`/`note`/`other`). Small edits ripple ≤ 2-3
  chunks, proven via Hypothesis property tests.
- `ChunkerDispatcher.for_class` — class-mapped chunker resolution
  (`code → CodeChunker`, `chat → ConversationChunker`,
  `reference → PassthroughChunker`, everything-else → `CDCChunker`).
- New `corpus-forge rechunk` CLI — walks classified documents and re-runs the
  chunker pass. Idempotent on chunk-text + metadata signature; preserves
  embeddings on identical chunks via `StorageBackend.replace_document_chunks`.
- New `StorageBackend.get_document_chunk_texts` + `get_document_chunk_metadatas`
  helpers powering the rechunk idempotency check.
- `[multi-format]` extra picks up `fastcdc>=1.6`.

#### Phase G — Whisper transcription + multi-modal embeddings
- **P0 — Whisper.** `WhisperBackend` protocol + `LocalWhisper` (faster-whisper
  in-process, tiny/base/small/medium/large) + `RemoteWhisper` (OpenAI-compatible
  `/audio/transcriptions`; works against OpenAI, Groq, Replicate, self-hosted
  whisper.cpp via HTTP). `AudioExtractor` for `.mp3`/`.wav`/`.m4a`/`.ogg`/`.flac`;
  `VideoExtractor` for `.mp4`/`.mov`/`.webm`/`.mkv`/`.avi` (uses imageio-ffmpeg).
  `[whisper]` extra; defaults to `backend = "none"` so audio/video files are
  silently skipped pre-opt-in.
- **P1 — Multi-modal embeddings.** New `MultiModalEmbedder` protocol
  (`corpus_forge/embedders/multimodal.py`) — distinct seam from the text
  `Embedder` so both keep clean APIs. `ClipLocalEmbedder` (sentence-transformers
  `clip-ViT-B-32`, 512 d; `jina-clip-v2` 1024 d also accepted) and
  `ClipRemoteEmbedder` (OpenAI-compatible `/v1/embeddings` with base64 data-URL
  image input).
- `corpus-forge embed --image` routes through `backfill_image_embedder`.
  Resolves image bytes from `metadata.image_b64` → `metadata.image_path` →
  the document's `filesystem://` URI in order.
- Alembic `0011_image_embeddings` adds `embedders.image BOOLEAN`; the dynamic
  `image_embeddings_<name>` per-embedder family mirrors the text
  `embeddings_<name>` family.

#### Phase H — Qwen3.6-35B-A3B code-chunk enrichment
- New `CodeEnricher` protocol (`corpus_forge/enrichers/`) + `CodeChunkEnrichment`
  dataclass (`{docstring, summary, symbols[], model, confidence}`) + `EnricherRegistry`.
- Two concrete backends to satisfy the local-or-remote URL principle:
  `QwenCoderLocal` (local Ollama `/api/generate`) and `QwenCoderRemote` —
  speaks either the Ollama shape OR OpenAI chat-completions
  (`response_format=json_object`) via `remote_api_shape`. Bearer auth
  optional on Ollama, required on OpenAI.
- New `corpus-forge enrich` CLI — walks `class=code` chunks only,
  cost-guard preflight, idempotent on `chunks.metadata.enrichment.model`.
  `--backend qwen-local|qwen-remote`, `--reclassify-on-model-change`,
  `--dataset`, `--limit`, `--dry-run`, `--json`.
- `iter_code_chunks_for_enrichment(model_tag)` + `update_chunk_enrichment`
  on both Postgres (`jsonb_set`) and SQLite (read-modify-write) backends.
- Default `[code_enricher].backend = "none"` keeps existing configs untouched.

### Changed

- Configuration: every model-client block (`[vlm]`, `[classifier]`, `[whisper]`,
  `[code_enricher]`) now carries explicit local + remote URL fields and rich
  one-comment-per-field documentation in `config.example.toml`. The
  local-or-remote URL principle is documented as a cross-cutting concern in
  `docs/architecture.md` and the README.
- `Config.backend.kind == "sqlite"` is now validated against multi-host sync
  (`Config.validate_sync_gate`) — `sync_enabled = true` on any dataset is
  rejected at config-load time so the failure is at startup, not on the first
  write.

## [0.1.0b1] - 2026-05-12

First beta release. The project is now feature-complete for the
single-host single-developer workflow described in the README and is
ready for external review.

### Added

#### Phase B — SQLite backend
- `corpus_forge/backends/sqlite_backend.py` — full SQLite + `sqlite-vec`
  storage backend, single-host only (no advisory locks, no cross-host
  sync).
- New `[sqlite]` optional install extra.
- Config-load validation rejects `sync_enabled = true` when
  `backend.kind = "sqlite"` so the failure surface is at startup, not
  on the first write.
- Schema migrations apply identically on PostgreSQL and SQLite (per-
  embedder vector tables, JSON metadata columns, content-hash dedup).

#### Phases CI-1 / CI-2 / CI-3 — release-ready CI/CD
- `.github/workflows/ci.yml` — `workflow_call`-able, `actionlint`
  gate, full lint + format + typecheck + parallel pytest with
  per-test timeout + coverage gate ≥ 85%.
- 3-OS × 3-Python matrix (ubuntu-22.04 / macos-14 / windows-2022 × py
  3.11 / 3.12 / 3.13) with `continue-on-error` on the still-landing
  py3.13 macOS-arm64 + Windows cells.
- `.github/workflows/integration.yml` — Linux + macOS Docker-backed
  pgvector integration runs.
- `.github/workflows/nightly.yml` — full matrix + `HYPOTHESIS_PROFILE=
  nightly` on a cron.
- Apache-2.0 license, PyPI classifiers, `py.typed` marker, per-OS
  installer scripts under `scripts/{linux,macos}/`.

#### Phases R1..R5 — retrieval + MCP surface
- `corpus_forge/retrieval/` — vector search + reranker over
  `chunks.text` (BGE reranker v2-m3 default).
- New `[retrieval]`, `[rerank]`, `[mcp]`, and `[eval]` extras.
- `corpus-forge search` and `corpus-forge mcp serve` CLI commands.
- In-process MCP server (`corpus_forge/mcp/server.py`) exposes
  `search`, `get_chunk`, `list_datasets` tools over stdio.
- Bundled retrieval-evaluation harness (`corpus-forge eval retrieval`)
  with a self-curated gold set under
  `corpus_forge/eval/datasets/forge_self.*`.

#### Phase CS — Claude integration drop-ins
- `examples/mcp-config/` — drop-in `.mcp.json` for Claude Code and
  `claude-desktop.json` for Claude Desktop.
- `.claude/skills/corpus-forge-search/SKILL.md` — Claude Code skill
  that surfaces `mcp__corpus-forge__*` tools with a citation-disciplined
  playbook.
- `.claude/agents/corpus-forge-researcher.md` — Agent SDK subagent
  scoped to the three MCP tools.
- `docs/claude-integration.md` — end-to-end walkthrough.
- Contract test (`tests/smoke/test_skill_tool_contract.py`) pins the
  `mcp__corpus-forge__<tool>` prefix against the server's live
  `tools/list` reply.

#### Phase BR — beta packaging
- `assets/banner.svg`, `assets/banner-dark.svg`, `assets/logo.svg` —
  anvil/forge + dataflow brand assets used in the README banner block.
- `CHANGELOG.md` (this file), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant 2.1), `SECURITY.md`.
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`,
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/dependabot.yml`
  (pip + github-actions weekly), `.github/FUNDING.yml`.
- `cliff.toml` — `git-cliff` config used by the release workflow.
- `.github/workflows/release.yml` — tag-triggered release pipeline
  (`gate` → `build` → `publish`); `gate` reuses `ci.yml` via
  `workflow_call`; `publish` uses `softprops/action-gh-release@v2`
  with `prerelease` auto-derived from beta / RC tags.
- Full README rewrite — banner block, shields.io badge row, expanded
  Agent integration (MCP) section, and a slimmer install / quickstart
  flow.

### Changed

- README condensed and reorganised from ~430 lines to ~250 lines; the
  three install scripts are in collapsible `<details>` blocks; the
  HF-export "what you get" section is promoted toward the top.
- The compact 3-bullet MCP pointer landed in CS is replaced by a full
  Agent-integration section with Prerequisites + Wire-up snippets.

### Security

- `SECURITY.md` lists `0.1.x` as the supported beta line and
  `evan@jwo3.io` as the vulnerability-reporting contact.

[Unreleased]: https://github.com/ulmentflam/corpus-forge/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/ulmentflam/corpus-forge/releases/tag/v0.1.0b1
