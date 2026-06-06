# RFC: Fleet 4/4 — Tailscale-native configuration and discovery

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P1 — operator-requested 2026-06-04 (ergonomics layer over fleet 1–3)
**Depends on**: rfc-fleet-1-model-telemetry-and-bench (hosts table); enhances rfc-fleet-3's join flow

## Context

Every machine in the operator's fleet is on one tailnet; the shared
Postgres DSN is already a hand-pinned Tailscale IP (`100.124.253.81`),
and the same pattern is emerging for OpenAI-compatible embedder
endpoints (Ollama on `:11434`, vLLM on `:8000`) that other machines
could call over the tailnet. corpus-forge has no Tailscale awareness:
raw 100.x IPs in config break when nodes re-key or the operator
re-architects the tailnet, and there's no health check that names the
actual failure ("tailscaled down" vs "Postgres down" look identical
today).

Tailscale already solves discovery (MagicDNS names are stable) and
transport security; this RFC only teaches corpus-forge to *speak
names* and *check reachability* — read-only integration.

## Goals

- `ts://<magicdns-name>[:port]` accepted everywhere a host URL/DSN
  appears: `backend.dsn`, `EmbedderConfig.base_url`,
  `ollama.base_url`, `classifier.llm_url`, `enricher` URLs, VLM/
  Whisper endpoints.
- `[tailscale]` config block (default `enabled = false`) controlling
  resolution behavior.
- Doctor `tailscale` check: binary present, backend state `Running`,
  every `ts://` name resolves and accepts a TCP connect.
- Setup/join wizard offers live tailnet peers when picking the
  Postgres host or remote embedder endpoints (no more typing 100.x
  IPs).
- `hosts list` (fleet-1) marks rows whose `tailscale_name` matches a
  live peer.

## Non-goals

- **No Tailscale lifecycle management** — never `tailscale up/down`,
  no ACL or key handling. Read `tailscale status --json` only.
- **No hard dependency** — Tailscale absent → `ts://` names fail with
  a clear error naming the doctor check; everything else (plain
  hostnames, IPs) works exactly as before.
- No non-Tailscale mesh support (WireGuard-raw, Nebula, …). The
  scheme prefix leaves room (`ts://` is explicit) if ever wanted.

## Approach

### Resolution

New `corpus_forge/net/tailscale.py`:

- `resolve(name) -> str`: MagicDNS-first — when MagicDNS is enabled a
  bare peer name is just a hostname, so resolution is a no-op rename
  (`ts://gb10 → gb10`). Fallback: `tailscale status --json`
  (`subprocess.run`, 5 s timeout, mirroring the `acceleration.py`
  shellout pattern) → peer's tailnet IP. Result cached for process
  lifetime.
- `peers() -> list[Peer]`: name, IPs, online flag — for wizard
  pickers and `hosts list` annotation.
- Failure shapes mirror `ProcessDiscoveryUnavailable` (PR #91):
  `TailscaleUnavailable` distinguishes "no binary / not running" from
  "name not found in tailnet" — different operator remediations.

Config plumbing: a single `resolve_endpoint(url)` helper applied at
the point each URL/DSN is consumed (not at parse time — config stays
inert, resolution is lazy, errors surface where the connection is
attempted with full context).

### `[tailscale]` block

```toml
[tailscale]
enabled = true            # default false; ts:// errors when false
prefer_magicdns = true    # skip status-shellout when MagicDNS works
```

### Doctor + wizard

- Doctor `tailscale` check: OK + "not configured" when the block is
  absent and no `ts://` appears in config; otherwise binary →
  `Running` state → per-name resolve + TCP connect to the configured
  port, each failure named individually.
- `setup` / `setup --join` (fleet-3): when `tailscale status`
  succeeds, present a live-peer picker for the Postgres host and for
  remote embedder `base_url`s, rendering `ts://` names into the
  config. Skipped silently when Tailscale is absent.
  `CF_NON_INTERACTIVE` accepts `ts://` values in the existing flags —
  no new flags needed.
- `hosts list` cross-references `corpus.hosts.tailscale_name` against
  live peers (●/○ online marker); fleet-1's heartbeat fills
  `tailscale_name` properly once this lands (best-effort until then).

**Coverage note:** ≥ 90 % line coverage on all new code is part of
"done" per task (`make test-unit` gate). Shellout tests follow the
`test_mcp_restart_and_doctor.py` pattern — patch the module-level
boundary, never the real binary (and mind sys.modules hygiene; see
issue from PR #91's CI flake).

## Tasks

- [x] `corpus_forge/net/tailscale.py`: `resolve` / `peers` /
      `TailscaleUnavailable` (MagicDNS-first, status-JSON fallback,
      5 s timeout, process-lifetime cache).
- [ ] `ts://` scheme accepted by every URL/DSN config field via a
      lazy `resolve_endpoint` helper at consumption points; clear
      unresolved-name error naming the doctor check.
- [ ] `[tailscale]` config block (default off) + validation
      (`ts://` present while disabled → config error at load with a
      fix-it message).
- [ ] Doctor `tailscale` check (binary / state / per-name resolve +
      TCP connect; OK "not configured" path).
- [ ] Wizard + join-flow live-peer picker (graceful skip without
      Tailscale); `hosts list` online markers; heartbeat fills
      `tailscale_name`.
- [ ] Docs: README + CLAUDE.md fleet section — "point every box at
      ts://pg-host, done"; troubleshooting row for
      `TailscaleUnavailable`.

## Verification

- Unit: resolution fallback order, cache behavior, both
  `TailscaleUnavailable` shapes, disabled-block validation — all via
  patched shellouts (no real tailscale in CI).
- Integration: config with `ts://` + a stubbed resolver connects the
  backend through the resolved address; doctor check exercises all
  three outcomes.
- Manual fleet acceptance: switch the real `backend.dsn` to
  `ts://<pg-host>` on one machine; `doctor` green; daemon syncs;
  `bench embed` against a remote Ollama via `ts://` records an
  `api`-transport benchmark row.
- `make test-unit` ≥ 90 % coverage on `net/tailscale.py` and plumbing.

## References

- `corpus_forge/acceleration.py` — shellout-with-timeout pattern.
- `corpus_forge/mcp/lifecycle.py` — `ProcessDiscoveryUnavailable`
  error-shape precedent (PR #91).
- `corpus_forge/config.py` — URL/DSN fields gaining `ts://`.
- `.planning/rfcs/rfc-fleet-1-model-telemetry-and-bench.md`,
  `rfc-fleet-3-federated-config-and-setup.md` — consumers of peers().
- Tailscale MagicDNS + `tailscale status --json` (stable CLI surface).
