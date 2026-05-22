# Task 0002 — bug-report bundle smoke

## What I exercised

```
uv run corpus-forge bug-report --no-zip --out ./bundle
```

Against a host with no `~/.config/corpus-forge/config.toml` (the
"unconfigured but installed" path — the worst-case smoke for the
post-doctor/admin refactor).

## Results

**Bundle generates.** Exit 0. The CLI prints a prefilled GitHub issue
URL and a result-event JSON line.

**Bundle contents are intact** (matches the doctor/admin refactor's
expected surface):

```
config.redacted.toml
db_summary.json
deps.txt
doctor.json
env.txt
logs/
  cli.log.txt
  recent_events.txt
manifest.json
README.txt
service_status.txt
```

**JSON shape is sane**:

- `manifest.json` — well-formed; carries `corpus_forge_version`, `os`,
  `os_version`, `python_version`, `arch`, `ts_utc`, `hostname_hash`,
  `tool_path`, `redaction_log` (empty array), and a new
  `agent_mode_at_time_of_capture` block (correctly identified the
  invocation as `claude-code_2-1-145_agent`).
- `doctor.json` — well-formed; `checks` is an array of
  `{name, status, detail}` objects. Statuses observed: OK / WARN /
  SKIP, matching the post-refactor enum.
- `db_summary.json` — gracefully degraded with
  `{"unavailable": "Configuration file not found: ..."}`. No crash,
  no half-written JSON.

**`agent_mode_at_time_of_capture` is new** and correctly identifies the
Claude Code agent mode — this is a nice addition for triage. The
hostname is hashed (privacy-safe).

## Caveats / gaps

- I did NOT exercise the happy path with a live Postgres DB. The
  `db_summary.json` schema when a backend is reachable is unverified
  here. Priority-list item #5 also called out that this needs to be
  exercised against the live DB; this smoke covered the unconfigured
  path only.
- One UserWarning leaks through stderr before the bundle is written
  (`Field name "schema" in "BackendConfig" shadows an attribute in
  parent "BaseModel"`). NOT a regression — this is expected on
  `phase-r-deployment` (the branch the venv resolves against), which
  doesn't have PR #18's `warnings.filterwarnings` suppression yet. On
  `main` (post-PR-#18) the filter is in `corpus_forge/config.py:23`.
  Will clear once `phase-r-deployment` rebases on main or PR #18's
  filter lands there.

## Verdict

Smoke passes for the no-config path. Worth a follow-up smoke against a
live DB before fully signing off priority item #5.
