## Things I'm not sure about

- **Whether `corpus-forge migrate` ever prompts interactively.** I read
  `corpus_forge/schema/migrate.py::main` and as of main HEAD (post-PR #18) it
  loads `Config.load()` and dispatches to a backend non-interactively. If a
  future code path ever calls `input()` from inside the migrate command tree
  the installer will hang at the new `corpus-forge migrate` call, because we
  redirect both streams to a log file and never prompt the user. There is no
  `--non-interactive` flag passed (the command doesn't expose one today). If
  this changes, the installer needs to be updated in lockstep or `corpus-forge
  migrate` needs to grow a `--non-interactive` flag.

- **Whether the migrate call honours `CF_CONFIG` end-to-end.** The new test
  `test_cf_config_propagates_to_setup_and_migrate` asserts the env var reaches
  the subprocess; it does NOT assert that the migrate command actually reads
  it. That's a corpus-forge contract owned by `Config.load()`, not the
  installer. If `Config.load()` ever stops honouring `CF_CONFIG`, the
  installer is correct but the user-facing behaviour breaks silently.

## Things that could break

- **macOS `mktemp -t` ignores the `.log` suffix.** On macOS `mktemp -t
  PREFIX` takes a prefix and emits `/tmp/<prefix>.<rand>` — the `.log`
  template suffix is not honoured. The `|| mktemp` fallback covers
  functional cases but the warn message points at a file path that won't
  look like `.log`. Cosmetic only; everything still works.

- **PowerShell `Set-StrictMode -Version Latest` corner case.** The new
  block sets `$LASTEXITCODE = 0` *before* the `try` to guarantee
  initialisation, but a future refactor that introduces a code path between
  the strict-mode declaration and the `try` block could re-introduce the
  "uninitialised variable" throw under StrictMode. The defensive
  initialisation is in place — keep it.

- **`pwsh` is not on the local PATH, so
  `test_install_ps1_migrate_failure_does_not_abort` was skipped.** The Linux
  CI lane will skip it too. The install.ps1 migrate block is therefore
  tested only by reading — no machine has executed it on this branch. Add a
  Windows / pwsh-enabled CI matrix entry, or accept the risk and exercise
  it manually on a Windows VM once before merge.

- **Test extraction depends on the sentinel comment.** The body extractor
  uses `# END __cf_post_install_handoff` as the end marker. If a future
  edit drops the sentinel, the test fails loudly via the regex's
  `assert match is not None`. That's by design — better a loud failure
  than silent truncation.

## Things I skipped on purpose

- **`setup-corpus-forge.sh` (contributor clone-and-run)** — out of scope.
  It's for contributors, not end users. CLAUDE.md doesn't claim migrate
  runs there.

- **A Windows/pwsh CI lane** — adding a CI matrix entry is a separate
  change, larger than this task's file scope.

- **Auto-cleaning the migrate log on success** — currently the diff
  deletes the temp log on success and leaves it on failure (so the user
  can read it). Could also auto-tail the last 20 lines into the warn
  output for curl-pipe-bash users who never see the file path. Punted;
  failure path already prints the path.

- **Mirroring the PR-#18 bug-#3/#4 fixes anywhere** — those landed in
  PR #18 and don't need redoing. This task only added the install→migrate
  *handoff* that was still missing, plus the corresponding install.ps1
  always-pass-`--non-interactive` fix (PR #18 only fixed install.sh's
  bug-#1; the same bug existed in install.ps1).

## Approval needed for

- Nothing. This task did not touch any of the six refusal categories
  (no destructive git, no production state, no external communication, no
  unknown-domain network egress, no scope creep, no test/type-safety
  bypasses).
