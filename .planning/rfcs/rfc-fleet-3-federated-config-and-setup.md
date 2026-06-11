# RFC: Fleet 3/4 — federated config scope + multi-host setup/install flow

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-04
**Depends on**: rfc-fleet-1-model-telemetry-and-bench (hosts table); pairs with rfc-fleet-2 (lanes are local scope)

## Context

corpus-forge config is fully machine-local, but parts of it are
really **corpus-shaped**: dataset names/kinds, embedder definitions +
fingerprints + dimensions, retrieval settings. Cross-host consistency
today is hand-maintained — the operator's config literally carries
the comment "Names must match the Mac's so find_dataset_id_by_name
resolves to the same rows." One typo on a new machine silently forks
the corpus.

Onboarding a new machine is also entirely manual: install, run the
wizard from scratch, hand-copy the shared blocks, hope they match.
With fleet-2 making extra machines genuinely useful (they can drain
embedding backlog), the join flow becomes the bottleneck.

**Hard backwards-compat bar:** a local-only single-instance setup
with no `[federation]` block must behave byte-for-byte as today.

## Goals

- Split config fields into **shared** scope (datasets sans local
  roots, embedders, retrieval, classifier/enricher model choices) and
  **local** scope (DSN, daemon, source roots/paths, devices,
  `api_key_env` names, `[embed] lanes`).
- `corpus-forge config publish` / `config pull [--apply]` — versioned
  shared-scope storage in the DB, dry-run-default pull, comment-
  preserving TOML rewrite, version-conflict refusal.
- **Join flow**: `corpus-forge setup --join <dsn>` takes a fresh
  machine from installed → registered host with converged shared
  config in one command.
- Installer awareness: `install.sh` / `install.ps1` accept a join
  target so a brand-new machine is one command end-to-end.

## Non-goals

- **No secrets in shared config** — `api_key_env` *names* may ship;
  values never exist in config at all (env vars). The scope extractor
  carries a deny-list test for path-shaped and secret-shaped fields.
- **No auto-apply.** Daemon detects drift and WARNs; a human (or an
  agent told to) runs `config pull --apply`. No background config
  mutation, ever.
- **No multi-corpus federation.** One shared Postgres = one corpus =
  one shared-config row. Cross-corpus sync is out of scope.
- **No SQLite support** — federation requires the shared Postgres by
  definition (doctor WARNs, same policy as fleet-2).

## Approach

### Scope annotations

Mark scope on the existing pydantic config models (field metadata —
not a file-format change). `shared_scope_dict(config)` extracts the
shared subset; `merge_shared_scope(local_toml, shared)` rewrites only
shared-scope keys via tomlkit, preserving comments and ordering.
`DatasetSourceConfig` splits naturally: the dataset's name/kind are
shared; each source's plugin + root stays local (different machines
ingest different directories — that's a feature).

### Storage + verbs

```
corpus.shared_config
  corpus_id     int primary key default 1
  version       int
  body          jsonb
  published_by  text references corpus.hosts
  published_at  timestamptz
```

- `config publish` — extract, validate (deny-list scan), bump
  version, write. Refuses when the DB version is newer than the last
  version this host pulled ("pull first"), preventing blind
  clobbering.
- `config pull` — fetch + diff against local, print the delta
  (dry-run default, mirrors `prune`); `--apply` rewrites shared keys
  in `config.toml`.
- `config diff` — alias for the dry-run pull output.
- Daemon startup compares hashes and logs one WARN on drift.
- `[federation]` block, default `enabled = false` → none of this
  runs; no new tables touched at runtime; config loading unchanged.

### Join flow (the install/configure surface)

`corpus-forge setup --join <dsn>`:

1. Connect to the DSN; verify schema (`migrate` state) and read
   `corpus.shared_config`.
2. Write a minimal local `config.toml`: backend block (the DSN),
   pulled shared scope, generated `host_id`, empty local blocks with
   commented examples (source roots, `[embed] lanes`).
3. Register the host (fleet-1 upsert) and print next steps
   (`doctor`, `bench embed --all`, optionally `service install`).

Wizard path: `corpus-forge setup` gains a first question — "new
corpus here, or join an existing one?" — when answering join, prompt
for the DSN (fleet-4 will offer tailnet names here).
Non-interactive: `CF_NON_INTERACTIVE=1 corpus-forge setup --join
<dsn> [--embed-lanes a,b]`.

Installers: `install.sh --join <dsn>` / `install.ps1 -Join <dsn>`
pass through to the non-interactive join (install → migrate-check →
setup --join → doctor in one shot). Existing no-flag behavior is
untouched.

### Backcompat proof

A frozen 0.1.0b16-era `config.toml` fixture parses to an identical
`Config` object with federation off, and `setup` with no `--join`
produces today's wizard verbatim (snapshot test on the prompt tree).

**Coverage note:** ≥ 90 % line coverage on all new code is part of
"done" per task (`make test-unit` gate).

## Tasks

- [x] Scope annotations on config models + `shared_scope_dict` /
      `merge_shared_scope` (tomlkit, comment-preserving) + deny-list
      test (no path-shaped / secret-bearing field ever extracted).
- [x] Alembic revision: `corpus.shared_config` (versioned jsonb);
      idempotent re-run test. _(merged via #110)_
- [x] `config publish` / `config pull [--apply]` / `config diff`
      verbs; version-conflict refusal; dry-run default. _(merged via #110)_
- [x] `[federation]` block (default off); daemon drift WARN; frozen-
      config backcompat regression + wizard snapshot test. _(merged via #111)_
- [x] `setup --join <dsn>` (wizard question + non-interactive flag);
      host registration + minimal local config render + next-steps
      print. _(merged via #113)_
- [x] `install.sh --join` / `install.ps1 -Join` pass-through; CI
      install-script matrix exercises the flag against a disposable
      Postgres (or stubs the DSN check where containers are
      unavailable). _(merged via #117 — shell-driven tests in
      `tests/scripts/test_install_sh_join.py`)_
- [x] Docs: CLAUDE.md / AGENTS.md / README "add a second machine"
      section (install one-liner with `--join`). _(merged via #116, #117)_

## Verification

- Frozen-config + wizard-snapshot tests prove the local-only path is
  untouched (the hard bar).
- Integration: publish from host A's config fixture, `setup --join`
  as host B against the same testcontainers Postgres → B's shared
  scope equals A's; B's local scope is independent.
- Manual fleet acceptance: join a fresh machine with one installer
  command; `corpus-forge doctor` green; dataset/embedder names match
  without hand-editing.
- `make test-unit` ≥ 90 % coverage on scope extraction, verbs, and
  join flow.

## References

- `corpus_forge/config.py` — the models gaining scope annotations.
- `corpus_forge/admin/` — verb pattern; `prune.py` dry-run-default
  precedent.
- `install.sh` / `install.ps1` — installer flag surface.
- `.planning/rfcs/rfc-fleet-1-model-telemetry-and-bench.md` — host
  registration the join flow reuses.
- `.planning/rfcs/rfc-fleet-2-distributed-embedding.md` —
  `[embed] lanes` (local scope) and `--embed-lanes`.
