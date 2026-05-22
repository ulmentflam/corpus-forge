# RFC: Runtime feedback — sandboxed code execution + profiling

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P2
**Depends on**: `rfc-source-provenance-git-and-lines.md` (we need
file/line refs to know which function to run)

## Context

We capture conversations, code, prose. We *don't* capture what happens
when ingested code actually runs. The user wants:

- **Stack traces as feedback**: auto-run snippets / functions / test
  cases against ingested code; when they raise, attach the
  exception + traceback to the corresponding chunk's feedback
  record.
- **Profiling traces as feedback**: cProfile samples per
  invocation, attached to the same chunks so retrieval can rank
  "this function is hot."
- **Return values + assertions**: assert-style outcomes (this
  function returned X with input Y) — high-signal feedback for
  self-distillation.

The default must be *safe*: a sandbox with no network, capped CPU/mem,
ephemeral filesystem. The user-chosen escape hatch is
project-local-opt-in: run inside the user's existing project venv when
they explicitly pass `--mode=local`.

`corpus_forge/mcp/server.py:_dispatch_add_feedback` already stores
feedback objects against chunks (`kind`/`rating`/`text` shape). The
plumbing exists; we extend the shape and add the runner.

## Goals

- A `Sandbox` ABC under `corpus_forge/execfeedback/` with two
  initial impls:
  - `SubprocessSandbox` — Python `subprocess.run` under a stripped
    env, `resource.setrlimit` CPU/AS caps, `tempfile.TemporaryDirectory`
    cwd, network off (best-effort: no proxy env vars; document the
    limit).
  - `LocalVenvSandbox` — runs in the user's project venv (resolved
    from `corpus_forge.config.Config`); skips the resource caps,
    inherits env. Only fires when caller passes `mode="local"`.
- A `ProfileCapture` wrapper around `cProfile.Profile()` that
  produces a compact `{top_functions: [...], total_time: float,
  call_count: int}` summary suitable for storing in feedback.
- A new MCP tool `run_chunk_and_capture(chunk_id, mode)` that:
  1. Resolves the chunk's `file_path` + line range (via
     `get_source_file_context` from
     `rfc-source-provenance-git-and-lines.md`).
  2. Extracts the snippet (function or top-level block).
  3. Runs it under the chosen sandbox with profiling on.
  4. Attaches `{stack_trace, return_value, cprofile_summary, mode,
     duration_ms}` as a new-shape feedback row.
- Threat-model document inline in this RFC (below).

## Non-goals

- No Docker/MicroVM sandbox in this RFC — subprocess + rlimit is
  good enough for first cut. A follow-up RFC can add containerised
  sandboxes if needed.
- No arbitrary-language execution; Python only (corpus-forge is
  Python).
- No long-running daemons — every call is one snippet, capped to a
  default 30s wall-clock.
- No write back to the source file. Feedback is *captured*, never
  *patched*.

## Threat model

The `SubprocessSandbox` is meant to make running *adversarial* code
safe enough for batch ingest experiments — not bulletproof against a
motivated attacker. Concrete controls and known limits:

- **CPU/mem caps** via `resource.setrlimit(RLIMIT_CPU, RLIMIT_AS)`
  before `os.exec*`. Hard caps: 30s CPU, 512 MB RSS.
- **Filesystem**: `cwd = tempfile.TemporaryDirectory(prefix="cf-sandbox-")`,
  deleted on exit. No bind mounts. The snippet *can* still read
  outside the cwd; we rely on the OS user's privilege.
- **Network**: best-effort; we strip `HTTP_PROXY`/`HTTPS_PROXY` and
  set `NO_PROXY=*`. We do not unshare network namespaces (would
  require root); a determined snippet can still hit the network.
- **Env**: stripped to a minimal set (`PATH`, `HOME=<tmp>`,
  `LANG=C.UTF-8`). Secret-bearing vars are excluded by allow-list.
- **Imports**: no module restriction in the sandbox. Importing the
  user's project source is intentional — that's what we're testing.

`LocalVenvSandbox` has none of those guards. We name it
`mode="local"` and require an explicit user opt-in (CLI flag, MCP
tool param) — never the default. Document the trust boundary in the
MCP tool's docstring.

## Approach

### Module layout

```
corpus_forge/execfeedback/
  __init__.py
  sandbox.py        # Sandbox ABC, SubprocessSandbox, LocalVenvSandbox
  profile.py        # ProfileCapture wrapper
  runner.py         # high-level attach_execution_feedback(chunk_id, mode)
  snippet.py        # extract a runnable snippet from a chunk
```

`snippet.py` does the heavy lifting of "given a chunk, produce a
self-contained executable file." For a function-shaped chunk it wraps
in `if __name__ == "__main__": <call with synthesized inputs>`. For
inputs, start with `None` and an empty dict — better
input-synthesis is a follow-up RFC.

### Feedback schema extension

Extend the feedback object shape (consumed by
`mcp/server.py:_dispatch_add_feedback`) to accept optional fields:

```json
{
  "kind": "execution",
  "stack_trace": "...string...",
  "return_value_repr": "...",
  "cprofile_summary": {...},
  "mode": "sandbox" | "local",
  "duration_ms": 42
}
```

These land in the existing `recent_feedback` storage; no schema
migration if we use a JSON column (which the existing implementation
does).

### MCP tool

```python
@server.tool(name="run_chunk_and_capture")
def run_chunk_and_capture(
    chunk_id: int,
    mode: Literal["sandbox", "local"] = "sandbox",
    inputs: dict | None = None,
) -> dict:
    """Run the code at chunk_id; attach result as feedback. ..."""
```

CLI mirror: `corpus-forge exec-chunk <chunk_id> [--mode sandbox|local]`.

## Tasks

- [ ] `corpus_forge/execfeedback/sandbox.py`: `Sandbox` ABC +
      `SubprocessSandbox` (rlimit + tempdir cwd + scrubbed env) +
      `LocalVenvSandbox` (resolve user's venv from config).
- [ ] `corpus_forge/execfeedback/profile.py`: `ProfileCapture`
      wrapping `cProfile`; emit top-N functions by `tottime`.
- [ ] `corpus_forge/execfeedback/snippet.py`: extract a runnable
      snippet from a chunk's text using AST parse for Python; emit a
      temp `.py` file or a stdin program.
- [ ] `corpus_forge/execfeedback/runner.py`:
      `attach_execution_feedback(backend, chunk_id, mode, inputs)`
      orchestration; calls into `_dispatch_add_feedback`.
- [ ] Extend the MCP `run_chunk_and_capture` tool in
      `corpus_forge/mcp/server.py`.
- [ ] CLI verb `corpus-forge exec-chunk` in `corpus_forge/cli.py`.
- [ ] Tests:
  - [ ] `tests/unit/test_sandbox_subprocess.py` — runs `print(1+1)`;
        captures stdout; CPU-bound infinite loop is killed within
        budget; OOM-ish script hits memory cap.
  - [ ] `tests/unit/test_sandbox_local_venv.py` — gated behind a
        `requires_local_venv` marker; runs a trivial import from the
        project's own package.
  - [ ] `tests/unit/test_profile_capture.py` — known hot-loop
        function's `tottime` dominates.
  - [ ] `tests/unit/test_snippet_extractor.py` — function chunks
        get wrapped with a call; class chunks emit a no-op success.
  - [ ] `tests/integration/test_run_chunk_and_capture_e2e.py` —
        round-trip a chunk through the MCP tool; verify feedback row
        contains all five fields; verify a failing snippet attaches
        a non-empty `stack_trace`.
- [ ] CHANGELOG entry with prominent threat-model note for
      `mode="local"`.

## Verification

- Sandboxed run of `1/0` produces a feedback row with `kind:
  "execution"`, non-empty `stack_trace` containing `ZeroDivisionError`.
- Sandboxed run of `while True: pass` is killed within 30s and
  attaches a "timed out" feedback record (no hung CI).
- Local-mode run of a benign function from the user's own project
  succeeds and attaches `return_value_repr` + cprofile summary.
- `corpus-forge exec-chunk <id>` round-trips without error and the
  next `MCP get_chunk` call surfaces the new feedback under
  `recent_feedback`.

## References

- Existing feedback storage:
  `corpus_forge/mcp/server.py:_dispatch_add_feedback`.
- Existing feedback shape: `commit_curation`'s feedback object
  (`kind`/`rating`/`text`).
- Logging / redaction: `corpus_forge/diagnostics/redact.py` (use to
  scrub stack traces of absolute paths before storage).
- Source-provenance lookup (dep): the MCP
  `get_source_file_context` tool added by
  `rfc-source-provenance-git-and-lines.md`.
