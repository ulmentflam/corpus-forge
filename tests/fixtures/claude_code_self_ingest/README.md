# Claude Code self-ingest fixture

This fixture exercises the `ClaudeCodeSource` parser against a real on-disk
session shape (rather than synthesised JSON like in
`tests/integration/test_claude_code_session_link_e2e.py::_write_fake_session`).
It backs `tests/integration/test_claude_code_self_ingest_e2e.py`.

## Layout

```
projects/
  -home-test-user-workspace-corpus-forge/
    fed1bafe-0001-4000-8000-000000000001.jsonl
```

The `projects_root` mirrors Claude Code's on-disk layout
(`<projects_root>/<project-slug>/<session-uuid>.jsonl`); the project slug
matches what Claude Code's CLI would produce for a checkout at
`/home/test-user/workspace/corpus-forge` (slashes → dashes, leading dash).

## Anonymisation transformations applied

The source was a real Claude Code session captured against this repo. To
make it shippable as a fixture, the following deterministic substitutions
were made (full script: `scrub_session.py` in the rescue worktree's run
notes — not checked in to avoid the appearance of a re-runnable PII
pipeline against developer machines):

| Field / pattern | Real source | Fixture value |
|---|---|---|
| `sessionId` (filename + body) | a real UUID | `fed1bafe-0001-4000-8000-000000000001` |
| All other UUIDs (`uuid`, `parentUuid`, `requestId`, `promptId`) | real values | deterministic synthetic in the `fed1bafe-0002-4000-8000-*` namespace |
| `cwd` | absolute path under `/home/<real-user>/` | `/home/test-user/workspace/corpus-forge` |
| `gitBranch` | personal feature branch | `main` |
| `/home/<real-user>` (anywhere in content) | absolute home path | `/home/test-user` |
| `<real-user>@<hostname>` (shell prompts) | real values | `test-user@test-host` |
| `userType`, `isVisibleInTranscript` | per-machine flags | dropped |

The `fed1bafe-0002-4000-8000-*` UUID namespace is intentional and unique:
greps for that prefix find every substituted UUID, so future maintainers
can audit the scrub.

## Synthetic appendages

The source session had `user`, `assistant`, `attachment`,
`permission-mode`, `file-history-snapshot`, `queue-operation`,
`last-prompt`, and `system` events — but no `ai-title` or `pr-link`. To
exercise the full parser surface in one pass, two synthetic events are
appended at the end of the file:

- `ai-title`: title "Fixture session — exercises full Claude Code parser
  surface", timestamp `2026-05-22T00:00:00Z`.
- `pr-link`: PR #9999 in `test-user/corpus-forge`, timestamp
  `2026-05-22T00:00:01Z`.

These two events are clearly distinguishable from real data by the PR
number (9999 — sentinel) and the `aiTitle` text (says "Fixture session").

## Verifying anonymisation

The integration test asserts that no real PII slipped through:

```bash
grep -RInE '/home/[^/]+/(?!test-user)|[a-z0-9]{8}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{12}' tests/fixtures/claude_code_self_ingest/ \
  | grep -v 'fed1bafe-' \
  | grep -v 'test-user'
```

If this command emits any output, an unscrubbed value snuck in — fix the
fixture before merging.

## Parser surface covered

Per `corpus_forge/sources/claude_code.py`:

- Top-level event `type`s: `user`, `assistant`, `attachment`,
  `ai-title`, `last-prompt`, `pr-link`, `permission-mode`,
  `file-history-snapshot` (plus `queue-operation` and `system` which the
  parser silently ignores — included to verify they don't break parsing).
- `message.content` block types (parsed via `_flatten.flatten_message`):
  `text`, `tool_use`, `tool_result`.
- Conversation metadata fields: `sessionId`, `cwd`, `gitBranch`,
  `version` (folded into `RawConversation.metadata`).

If a future parser change introduces a new event type, add a
representative instance to the fixture and update this README.
