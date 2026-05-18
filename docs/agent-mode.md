# corpus-forge agent mode

corpus-forge detects when it's running inside an AI coding agent (Claude
Code, OpenCode, Gemini CLI, GitHub Copilot CLI, OpenAI Codex,
Sourcegraph Amp, or any `AI_AGENT=*`-aware tool) and flips every output
surface to a single JSONL contract on stdout. The motivation: human
chrome (banners, progress chrome, log prefixes) is pure token cost when
an agent is downstream — agent mode replaces it with terse, parseable
events.

## Detection precedence

Mirrors the canonical implementation in `cli/cli`'s
[`internal/agents/detect.go`](https://github.com/cli/cli/blob/trunk/internal/agents/detect.go).
The first match wins.

| # | Signal | Outcome |
|---|---|---|
| 1 | `--agent <type>` flag | Forces the named client. `off` → HUMAN; `auto` falls through. |
| 2 | `CF_AGENT=<type>` env | Same vocabulary as `--agent`. |
| 3 | `AI_AGENT=<name>` env | Prefix (before first `_`) is matched against the enum; unknown → `ai-generic`. |
| 4 | `AGENT=amp` | Sourcegraph Amp (checked before Claude Code — Amp also sets `CLAUDECODE`). |
| 5 | `CODEX_SANDBOX` / `CODEX_CI` / `CODEX_THREAD_ID` | OpenAI Codex. |
| 6 | `GEMINI_CLI` | Google Gemini CLI. |
| 7 | `COPILOT_CLI` | GitHub Copilot CLI. |
| 8 | `OPENCODE` | OpenCode. |
| 9 | `CLAUDECODE` | Anthropic Claude Code (checked last in the agent block). |
| 10 | `CF_MCP_TRANSPORT=stdio` or `argv ⊇ ['mcp', 'serve', '--transport', 'stdio']` | MCP stdio carve-out — always agent mode. |
| 11 | `CI=true` AND no TTY on stdin/stdout | `generic`. |
| 12 | (default) | `human`. |

Force agent mode for one invocation: `--agent generic` or `CF_AGENT=generic corpus-forge ...`.
Disable: `--agent off`.

## Event schema (JSONL)

Every line on stdout is a JSON object terminated by a single `\n`. The
discriminator is `"event"`.

```jsonl
{"event":"command.start","ts":"2026-05-17T14:22:01.482Z","cmd":"embed","args":{"embedder":"qwen3_8b"},"version":"0.1.0b3","agent":"claude-code"}
{"event":"status","ts":"...","level":"info","msg":"Loading embedder qwen3_8b"}
{"event":"progress","ts":"...","op":"embed","done":3120,"total":12481,"rate_per_s":74.2,"pct":0.25}
{"event":"status","ts":"...","level":"warn","msg":"Embedder drift: qwen3_8b -> bge-m3"}
{"event":"result","ts":"...","cmd":"embed","status":"ok","data":{"embedded":12481,"elapsed_s":167.4}}
```

### Event types

| `event` | Shape |
|---|---|
| `command.start` | `cmd`, `args`, `version`, `agent` |
| `status` | `level` (`ok\|warn\|error\|info`), `msg` |
| `progress` | `op`, `done`, optional `total`, optional `pct`, `rate_per_s` |
| `log` | `level`, `logger`, `msg` |
| `panel` | `title`, `body` |
| `result` | `cmd`, `status`, `data` |
| `error` | `cmd`, `kind`, `msg` |

Each line carries `ts` (UTC ISO 8601 with millisecond precision,
`Z`-suffixed). No embedded newlines: `json.dumps` escapes them inside
strings so a single `\n` terminates the record.

### Per-command result payloads

| Command | `data` shape |
|---|---|
| `search` | `{"hits":[{"chunk_id":int,"score":float,"text":str,"doc":str}]}` |
| `estimate` | The `SyncEstimate` dataclass, plus `scan` and `pending` sub-dicts. |
| `doctor` | `{"checks":[{"name","status","detail"}],"summary":"ok|warn|fail","version","ts"}` |
| `config get` | `{"key":str,"value":any,"type":str}` |
| `config show` | `{"config":object,"redacted_keys":[str]}` |
| `embedder list` | `{"embedders":[object]}` |
| `bug-report` | `{"zip":"path","bytes":int,"redacted_count":int,"issue_url":str}` |
| `capabilities` | `{"corpus_forge_version","agent_mode","commands","result_schemas","agent_mode_contract"}` |
| (other) | `{}` (default OK) |

## Behavioural changes under agent mode

| Surface | Human mode | Agent mode |
|---|---|---|
| Banner | rounded ember box | suppressed |
| Progress bar | live `rich.progress` | sparse JSONL `progress` events at 25% boundaries (or every 10s) |
| Stderr logs | `RichHandler` with INFO+ | `AgentLogHandler` emits `log` events; default level WARNING |
| `ui.ok` / `warn` / `error` / `info` | colored prefix line | structured `status` event |
| Prompts | interactive | hard fail with `error{"kind":"requires_interactive"}` + exit 2 |
| Tables (config show, doctor table) | rich `Table` | single JSON `result` event |
| Tracebacks | Rich formatted | structured `error` with `kind=<ExceptionClass>` |

## Exit codes

Stable across modes:

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | generic error |
| 2 | invalid input / `requires_interactive` |
| 3 | config error |
| 4 | backend error |
| 5 | agent-interactive-required (reserved) |
| 64+ | command-specific |

## TypeScript schema (for MCP clients)

```typescript
type Event =
  | CommandStart
  | Status
  | Progress
  | Log
  | Panel
  | Result
  | ErrorEvent;

interface Common {
  event: string;
  ts: string;            // UTC ISO 8601, ms precision, Z-suffixed
}

interface CommandStart extends Common {
  event: "command.start";
  cmd: string;
  args: Record<string, unknown>;
  version: string;
  agent: AgentClient;
}

interface Status extends Common {
  event: "status";
  level: "ok" | "warn" | "error" | "info";
  msg: string;
}

interface Progress extends Common {
  event: "progress";
  op: string;
  done: number;
  total?: number;
  pct?: number;
  rate_per_s: number;
}

interface Log extends Common {
  event: "log";
  level: string;     // logger level, lowercase
  logger: string;
  msg: string;
}

interface Panel extends Common {
  event: "panel";
  title: string;
  body: string;
}

interface Result extends Common {
  event: "result";
  cmd: string;
  status: "ok" | "error";
  data: Record<string, unknown>;
}

interface ErrorEvent extends Common {
  event: "error";
  cmd: string;
  kind: string;
  msg: string;
}

type AgentClient =
  | "claude-code"
  | "opencode"
  | "gemini-cli"
  | "copilot-cli"
  | "codex"
  | "amp"
  | "ai-generic"
  | "generic"
  | "human";
```

## Discovery: `corpus-forge capabilities`

At startup an agent can issue a single `corpus-forge --agent generic
capabilities` to receive a JSON document listing every command, its
flags, and the agent-mode contract.

```json
{
  "corpus_forge_version": "0.1.0b3",
  "agent_mode": "generic",
  "commands": [
    {"name": "search", "help": "Search the corpus...", "params": [...]},
    ...
  ],
  "result_schemas": {...},
  "agent_mode_contract": {"events": [...], "exit_codes": {...}}
}
```

## How to disable

- One-shot: `--agent off`
- Per session: `export CF_AGENT=off`

## Implementation pointers

- Detection: `corpus_forge.ui.agent.detect`
- Emission: `corpus_forge.ui.agent.emit`, `result`, `error`
- ProgressEmitter: `corpus_forge.ui.agent.ProgressEmitter`
- Stderr-log swap: `corpus_forge.logging_config.AgentLogHandler`
- Bug-report manifest field: `agent_mode_at_time_of_capture`
