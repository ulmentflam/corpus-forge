# corpus-forge

HF-format corpus + multi-embedder ingestion daemon for personal text and chat data.

## What it is

corpus-forge ingests text and chat data from personal sources (Obsidian vaults, Claude Code sessions, OpenCode storage) into an SQL store designed for HuggingFace Datasets compatibility. It maintains multiple vector embeddings per chunk using pluggable embedding models, enabling personal-knowledge → fine-tuning pipelines without re-ingesting source files when new embedding models become available.

## Status & support

**Supported sources:**
- ✅ Obsidian/Vault markdown (`markdown_vault`)
- ✅ Claude Code sessions (`claude_code`)
- ✅ OpenCode storage (`opencode`)

**Supported embedders:**
- ✅ Sentence Transformers (local, e.g., Qwen3-Embedding-8B)
- ✅ OpenAI API (text-embedding-3-large, etc.)

**Supported backends:**
- ✅ PostgreSQL + pgvector (multi-host, sync-capable)
- ✅ SQLite + sqlite-vec (single-host, zero-setup; sync disabled)

**Current limitations:**
- SQLite backend is single-host only — cross-host sync requires PostgreSQL
- No HF Hub push authentication in CI (cost/security)
- Live OpenAI embedder not contract-tested in CI (cost)

## Install

corpus-forge ships as a wheel on PyPI (Apache-2.0 licensed). Pick the
section for your OS below — each one installs the same package, then
offers an optional service-registration script.

<details>
<summary><strong>Linux</strong> (systemd user unit)</summary>

```bash
# 1. Install the package
pip install 'corpus-forge[sqlite,hf]'   # or: uv add 'corpus-forge[sqlite,hf]'

# 2. (optional) Register a systemd user unit for the daemon
git clone https://github.com/ulmentflam/corpus-forge ~/corpus-forge
bash ~/corpus-forge/scripts/linux/install.sh
# → writes ~/.config/systemd/user/corpus-forge.service
# → enables + starts it via `systemctl --user`

# 3. Configure
cp ~/corpus-forge/config.example.toml ~/.config/corpus-forge/config.toml
cp ~/corpus-forge/secrets.env.example ~/.config/corpus-forge/secrets.env
# Edit both with your paths / keys

# 4. Apply schema + smoke-test ingestion
corpus-forge migrate
corpus-forge ingest --once
```

To stop / uninstall: `bash ~/corpus-forge/scripts/linux/stop.sh` /
`bash ~/corpus-forge/scripts/linux/uninstall.sh`.
</details>

<details>
<summary><strong>macOS</strong> (launchd agent)</summary>

```bash
# 1. Install the package
pip install 'corpus-forge[sqlite,hf]'   # or: uv add 'corpus-forge[sqlite,hf]'

# 2. (optional) Register a launchd agent for the daemon
git clone https://github.com/ulmentflam/corpus-forge ~/corpus-forge
bash ~/corpus-forge/scripts/macos/install.sh
# → renders ~/Library/LaunchAgents/com.${USER}.corpus-forge.plist
# → `launchctl load …` is printed; start with `launchctl kickstart -k …`

# 3. Configure
cp ~/corpus-forge/config.example.toml ~/.config/corpus-forge/config.toml
cp ~/corpus-forge/secrets.env.example ~/.config/corpus-forge/secrets.env
# Edit both with your paths / keys

# 4. Apply schema + smoke-test ingestion
corpus-forge migrate
corpus-forge ingest --once
```

To stop / uninstall: `bash ~/corpus-forge/scripts/macos/stop.sh` /
`bash ~/corpus-forge/scripts/macos/uninstall.sh`.
</details>

<details>
<summary><strong>Windows</strong> (NSSM or Task Scheduler — manual)</summary>

corpus-forge runs on Windows but does not ship an installer script.
After `pip install 'corpus-forge[sqlite,hf]'`, wrap the daemon yourself.
The easiest path is [NSSM](https://nssm.cc/) (the Non-Sucking Service
Manager):

```bat
:: in an elevated PowerShell / cmd
nssm install corpus-forge ^
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" ^
    -m corpus_forge daemon
nssm start  corpus-forge
```

Or schedule `python -m corpus_forge daemon` to run at login via Task
Scheduler. Config files live under
`%APPDATA%\corpus-forge\config.toml` and `\secrets.env`.
</details>

### Source install (developer mode)

```bash
git clone --recurse-submodules https://github.com/ulmentflam/corpus-forge ~/corpus-forge
cd ~/corpus-forge
uv sync --all-extras --group dev
uv run pre-commit install
make migrate
make ingest --once
make daemon          # foreground, or use the installer scripts above
```

## Quickstart

Five commands to get running:

```bash
# 1. Copy and edit configuration
cp config.example.toml ~/.config/corpus-forge/config.toml
cp secrets.env.example ~/.config/corpus-forge/secrets.env
# Edit ~/.config/corpus-forge/config.toml with your paths
# Edit ~/.config/corpus-forge/secrets.env with passwords/keys

# 2. Apply database schema
make migrate

# 3. Run one-shot ingestion pass
make ingest --once

# 4. Verify with SQL
psql "$DATABASE_URL" -c "SELECT name, kind FROM corpus.datasets;"
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM corpus.chunks;"

# 5. Start daemon (background service)
launchctl load ~/Library/LaunchAgents/com.${USER}.corpus-forge.plist
launchctl kickstart -k "gui/$(id -u)/com.${USER}.corpus-forge"
```

## Architecture

```
Sources → Chunkers → Orchestrator → Backend → per-embedder tables
      ↑              ↑              ↑              ↑
Sources:    Chunkers:    Orchestrator:  Backend:
- markdown  - markdown   - never branches  - Protocol impl
- claude    - conversation  on plugin       - Postgres/SQLite
- opencode                 identity        - Advisory locks
                            dedup         - Per-embedder tables
                            HF export
```

**Three Protocols** define the entire extension surface:
- `Source`: Ingest data (`sources/base.py`)
- `Embedder`: Create vectors (`embedders/base.py`)  
- `StorageBackend`: Persist data (`backends/base.py`)

**DRY by construction**: Common machinery in base classes from day one:
- `WatchedSource`: File watching, debounce, identity, hash short-circuit
- `Chunker base`: Size-bounding + overlap loop
- Embedder/Backend bases: Shared functionality

## Configuration reference

See `config.example.toml` and `secrets.env.example` for full reference. Key sections:

### [backend]
- `kind`: `"postgres"` | `"sqlite"`
- `dsn`: PostgreSQL connection string with `${VAR}` interpolation when `kind = "postgres"`; doubles as the SQLite file path (or `":memory:"`) when `kind = "sqlite"` — the field name is repurposed
- `schema`: Database schema (default: `"corpus"`); ignored by the SQLite backend (kept for protocol parity)

#### Local-only with SQLite

SQLite is the zero-setup option for single-machine use — no Postgres server, no `pgvector` extension, no daemons. The `dsn` field is repurposed as the path to the database file. Cross-host sync (`sync_enabled = true`) is rejected at config-load with the SQLite backend; for that you need PostgreSQL.

Install the optional `sqlite-vec` extra for `vec0`-backed nearest-neighbour search (without it the backend falls back to write-only BLOB storage):

```bash
pip install 'corpus-forge[sqlite]'   # or: uv sync --extra sqlite
```

Minimal `config.toml`:

```toml
[backend]
kind = "sqlite"
dsn  = "~/Library/Application Support/corpus-forge/corpus.db"

[[datasets]]
name = "obsidian-vault"
kind = "text"
  [[datasets.sources]]
  plugin     = "markdown_vault"
  vault_root = "~/Documents/vault"
  chunker    = "markdown"

[[embedders]]
name      = "qwen3_8b"
provider  = "sentence_transformers"
model_id  = "Qwen/Qwen3-Embedding-8B"
dimension = 4096
```

### [daemon]
- `debounce_seconds`: File change debounce (default: 2.0)
- `log_level`: DEBUG|INFO|WARNING|ERROR|CRITICAL
- `log_format`: text|json

### [[datasets]]
Repeat for each dataset:
- `name`: Dataset identifier
- `kind`: "text"|"chat"
- `description`: Optional description
- `[[datasets.sources]]`: Repeat for each source
  - `plugin`: "markdown_vault"|"claude_code"|"opencode"
  - Source-specific paths (vault_root, projects_root, etc.)
  - `chunker`: "markdown"|"conversation"
  - `chunker_config`: Chunker-specific settings

### [[embedders]]
Repeat for each embedder:
- `name`: Embedder identifier
- `provider`: "sentence_transformers"|"openai"
- `model_id`: Model identifier (HF hub or OpenAI)
- `dimension`: Vector dimension (must match model)
- `normalize`: L2 normalize vectors (default: true)
- `distance`: "cosine"|"l2"|"ip" (default: cosine)
- `active`: Whether to use this embedder
- `batch_size`: Inference batch size
- `device`: "auto"|"mps"|"cuda"|"cpu" (for local embedders)
- `api_key_env`: Env var for API keys (OpenAI only)

## Adding a Source / Embedder / Backend

### Adding a Source
1. Implement the `Source` protocol (or extend `WatchedSource` for file sources)
2. Override `discover()` and `parse()` methods
3. Add configuration example to docs
4. Add unit tests in `tests/unit/test_source_*.py`

### Adding an Embedder
1. Implement the `Embedder` protocol
2. Add to `embedders/` directory
3. Export in `embedders/registry.py`
4. Add configuration example
5. Add contract tests in `tests/integration/test_embedder_contract.py`

### Adding a Backend
1. Implement the `StorageBackend` protocol
2. Add to `backends/` directory
3. Update config validation
4. Add integration tests in `tests/integration/test_*_backend.py`

## HF export

Export to HuggingFace Datasets format:

```bash
# Text export (chunks)
uv run corpus-forge export hf --dataset obsidian-vault --view corpus_text_export

# Chat export (conversations)  
uv run corpus-forge export hf --dataset claude-code --view corpus_chat_export
```

Or programmatically:
```python
from corpus_forge.exports.huggingface import export_to_hf_dataset, push_to_hub

dataset = export_to_hf_dataset("corpus_text_export")
push_to_hub(dataset, "username/my-personal-corpus")
```

The views map directly to HF columns:
- `corpus_text_export`: id, text, source, title, heading, role, metadata, labels
- `corpus_chat_export`: id, source, title, messages (ShareGPT format), metadata

## Backfill workflow

To add a new embedding model later:

1. **Add to config.toml**:
   ```toml
   [[embedders]]
   name      = "new-embedder"
   provider  = "sentence_transformers"
   model_id  = "new/model"
   dimension = 1024
   active    = true
   ```

2. **Create the table**:
   ```bash
   make migrate  # Creates table via register_embedder()
   ```

3. **Backfill existing chunks**:
   ```bash
   make embed E=new-embedder  # Processes chunks missing this embedder
   # Or limit to dataset: make embed E=new-embedder d=obsidian-vault
   ```

4. **Verify**:
   ```bash
   psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM corpus.embeddings_new_embedder;"
   ```

**Key property**: Only embedding computation is repeated. Source files are never re-read; `chunks.text` is the durable source of truth.

## Operations

### Service management
```bash
# Start/stop daemon
launchctl kickstart -k "gui/$(id -u)/com.${USER}.corpus-forge"
launchctl kill SIGTERM gui/$(id -u)/com.${USER}.corpus-forge

# View logs
tail -f ~/Library/Logs/corpus-forge.err.log
tail -f ~/Library/Logs/corpus-forge.out.log

# Manual control (dev)
make daemon     # foreground
make stop       # stop launchd service
```

### Common tasks
```bash
# One-shot ingestion pass
make ingest --once

# Backfill specific embedder
make embed E=qwen3_8b

# Backfill all active embedders
make backfill

# Apply schema migrations
make migrate

# Run tests
make test          # all categories
make test-unit     # fast unit tests
make test-integration  # requires Docker
make test-fuzz     # property-based tests
make test-smoke    # end-to-end
```

### Database maintenance
```bash
# Check embedder tables
psql "$DATABASE_URL" -c "\d corpus.embeddings_*"

# Check ingestion stats
psql "$DATABASE_URL" -c "
  SELECT d.name as dataset, COUNT(*) as chunks
  FROM corpus.chunks c
  JOIN corpus.documents d ON d.id = c.document_id
  GROUP BY d.name
"

# Check embedding coverage
psql "$DATABASE_URL" -c "
  SELECT e.name as embedder, COUNT(*) as embeddings
  FROM corpus.embeddings_qwen3_8b e
  JOIN corpus.embedders emb ON emb.id = e.embedder_id
  WHERE emb.name = 'qwen3_8b'
  GROUP BY e.name
"
```

## Development

```bash
# Setup development environment
make dev  # installs deps + pre-commit

# Code quality
make format     # auto-format with ruff
make lint       # ruff check + fix
make typecheck  # pyrefly strict type checking

# Testing
make test-unit  # fast unit tests (coverage-gated ≥85%)
make test       # all test categories
make ci         # full CI pipeline (format-check lint typecheck test)

# Writing fuzz tests
# See tests/fuzz/ for examples using hypothesis
# Property tests should check invariants, not specific values

# Pre-commit hooks
# Runs on commit: ruff format/check, pyrefly, unit tests
# Pre-push: unit tests only
```

### Conventions
- **Line length**: 100 characters (ruff)
- **Quotes**: double quotes (ruff)
- **Type checking**: pyrefly strict mode on `corpus_forge/`
- **Docstrings**: Required for all public functions and classes
- **Error handling**: Log and continue where possible, fail fast on config/setup
- **Security**: Never log secrets, use secrets.env (mode 600) for passwords/keys

## Agent integration (MCP)

- Drop-in Claude Code skill: see `examples/mcp-config/` and `.claude/skills/corpus-forge-search/`.
- Agent SDK subagent: `.claude/agents/corpus-forge-researcher.md`.
- Full walkthrough: `docs/claude-integration.md`.

## License

corpus-forge is licensed under the [Apache License, Version 2.0](LICENSE).
Copyright 2026 Evan Owen / corpus-forge contributors.
