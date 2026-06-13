# corpus-forge docs

Index of the long-form documentation under `docs/`. The four top-level guides
(`README.md`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) live in the repo root; the
references below go deeper.

## Architecture & internals

- [`architecture.md`](architecture.md) — system architecture, the plug-in
  protocol seams (Source / Extractor / Chunker / Embedder / Backend /
  Classifier), and the multi-machine ingest model.
- [`schema.md`](schema.md) — database schema reference (the `corpus.*` tables).

## Integrations

- [`claude-integration.md`](claude-integration.md) — full Claude Code / Claude
  Desktop / Anthropic Agent SDK walkthrough.
- [`gemini-integration.md`](gemini-integration.md) — Gemini CLI integration
  walkthrough.
- [`opencode-integration.md`](opencode-integration.md) — OpenCode integration
  walkthrough.
- [`agent-mode.md`](agent-mode.md) — corpus-forge agent mode (assistant-facing
  CLI ergonomics and `--agent` hints).
- [`skill_packs.md`](skill_packs.md) — installing the shipped skill / slash-command
  packs across clients.

## Sources & embedders

- [`sources.md`](sources.md) — source plugins and their configuration.
- [`sources/zotero.md`](sources/zotero.md) — the Zotero library connector.
- [`embedding-models.md`](embedding-models.md) — per-lane embedding-model
  recommendations (the full survey behind the README's headline table).

## Deployment

- [`deployment-satellite.md`](deployment-satellite.md) — multi-host (satellite)
  topology setup.
- [`deployment/postgres.md`](deployment/postgres.md) — bare-metal PostgreSQL on
  Debian/Ubuntu.
- [`deployment/docker.md`](deployment/docker.md) — self-contained pgvector Docker
  Compose stack.
- [`deployment/lxc.md`](deployment/lxc.md) — Proxmox LXC sizing, Tailscale, and
  backups.
