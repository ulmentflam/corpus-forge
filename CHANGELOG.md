# Changelog

All notable changes to **corpus-forge** are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/)
version numbers (so `0.1.0b1` is the first beta of the `0.1.0` line).

## [Unreleased]

Nothing yet.

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
  `evan@qwerky.ai` as the vulnerability-reporting contact.

[Unreleased]: https://github.com/ulmentflam/corpus-forge/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/ulmentflam/corpus-forge/releases/tag/v0.1.0b1
