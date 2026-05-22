# RFC: End-to-end test for Claude Code self-ingest of this repo

Status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0
**Depends on**: none

## Context

`tests/integration/test_claude_code_session_link_e2e.py` is an H-03 RED
trip-wire — it writes a fake `.jsonl` session and asserts the
feedback_sessions ↔ conversations link gets created during ingest. The
test exercises the *link* and only the *link*. There is no test that
walks the real on-disk shape of `~/.claude/projects/-home-evan-workspace-corpus-forge/*.jsonl`
through the full pipeline (parser → chunker → embedder → backend →
search → MCP `get_chunk`). That gap matters because:

- The Claude Code parser landed in PR #29 (`corpus_forge/sources/claude_code.py`)
  filters by JSONL `type` field and folds metadata-only events
  (`permission-mode`, `file-history-snapshot`, `ai-title`,
  `last-prompt`, `pr-link`) into `RawConversation.metadata`. None of
  that is exercised end-to-end against real files.
- The `_session_link_client = "claude-code"` linker has no test that
  rides through `ingest.ingest_once` with a real source-uri scheme.
- We have zero confidence that a fresh `corpus-forge ingest --once`
  pointed at this repo's Claude Code data actually produces queryable
  conversations.

## Goals

- A green E2E integration test that ingests **this repo's own** Claude
  Code conversations (or a fixture clone of one), embeds, indexes, and
  asserts that a known query returns a known turn.
- A regression-safe shape: the test must not depend on the contents of
  the live `~/.claude/projects/` directory. It uses a checked-in
  fixture file (anonymised) under `tests/fixtures/claude_code_self_ingest/`.
- Confirms metadata folding: assert that `RawConversation.metadata`
  contains `session_id`, `cwd`, `git_branch`, `client_version` on a
  conversation parsed from the fixture.
- Confirms `_session_link_client` wiring: pre-populate
  `feedback_sessions` for a known session ID, ingest, then assert the
  link landed.

## Non-goals

- No new source plugin; this test consumes the existing
  `ClaudeCodeSource`.
- No changes to the parser. If the test surfaces a parser bug, file a
  follow-up RFC; this one stays scoped to test coverage.
- No live-disk I/O against `~/.claude/projects/` — every byte read is
  under `tests/fixtures/`.

## Approach

Build on the existing E2E harness pattern (in-memory SQLite backend +
`ConversationChunker` + `ingest_one`) used by
`tests/integration/test_claude_code_session_link_e2e.py` and the other
`tests/integration/test_*_e2e.py` files.

1. **Fixture corpus.** Anonymise one real session file from
   `~/.claude/projects/-home-evan-workspace-corpus-forge/*.jsonl` —
   scrub session UUIDs, request IDs, absolute paths outside the repo,
   any pasted content that names files outside corpus-forge. Drop into
   `tests/fixtures/claude_code_self_ingest/sample-session.jsonl`.
   Pick a session that contains at least one of each interesting event
   type (`user`, `assistant`, `attachment`, `ai-title`,
   `permission-mode`, `pr-link`, `tool_use`, `tool_result`) so a single
   pass exercises the full parser surface.

2. **In-memory backend harness.** Mirror the setup used in
   `tests/integration/test_claude_code_session_link_e2e.py::_make_backend`
   — `SQLiteBackend(":memory:")` + `apply_migrations` + `get_or_create_dataset`.

3. **Ingest happy path.** Construct
   `ClaudeCodeSource(projects_root=fixture_dir / "projects")`, iterate
   `source.scan()`, run each `RawConversation` through `ingest_one(...)`
   with `ConversationChunker()` and a deterministic fake embedder (the
   pattern used in other E2E tests — see
   `tests/integration/test_chunk_reuse_e2e.py`).

4. **Assertions.** A new
   `tests/integration/test_claude_code_self_ingest_e2e.py` with at
   least these assertions:
   - `conversations` row count > 0 with `source_uri` starting with
     `claude-code://`.
   - The conversation row's `metadata` JSON includes `session_id`,
     `cwd`, `git_branch`, `client_version`.
   - At least one message has `tool_calls` populated and at least one
     has `tool_results` populated.
   - `permission-mode` lines did **not** produce empty-content message
     rows (regression for the bug PR #29 fixed).
   - Pre-populating `feedback_sessions(client="claude-code",
     session_id=<known>)` before ingest results in
     `feedback_sessions.conversation_id` pointing at the new
     conversation row after ingest (via
     `_session_link.link_session_to_conversation`).
   - A retrieval round-trip: register a deterministic embedder, embed
     the fixture, then assert that `retriever.search(<known query
     substring>)` returns the matching chunk's `chunk_id`.

5. **Marker.** `pytest.mark.integration` so it runs in the integration
   tier, not the unit tier.

## Tasks

- [ ] Pick + anonymise one real session file from
      `~/.claude/projects/-home-evan-workspace-corpus-forge/`. Store
      under `tests/fixtures/claude_code_self_ingest/projects/-home-evan-workspace-corpus-forge/sample-session.jsonl`.
- [ ] Add a short `tests/fixtures/claude_code_self_ingest/README.md`
      naming the anonymisation transformations applied (so future
      maintainers know what's mocked).
- [ ] Write `tests/integration/test_claude_code_self_ingest_e2e.py`
      with the assertions above. Use the in-memory SQLite + fake
      embedder pattern from
      `tests/integration/test_chunk_reuse_e2e.py` and
      `tests/integration/test_claude_code_session_link_e2e.py`.
- [ ] Add a `test_metadata_fields_present` micro-test inside the new
      file that just asserts `session_id`/`cwd`/`git_branch`/`client_version`
      are non-empty.
- [ ] Add a `test_session_link_lands_during_full_ingest` test that
      seeds `feedback_sessions` then runs the full `ingest_once`-style
      loop (not just `ingest_one`) end-to-end.
- [ ] Run locally: `pytest tests/integration/test_claude_code_self_ingest_e2e.py -v`
- [ ] CHANGELOG entry under `### Added`.

## Verification

- `pytest tests/integration/test_claude_code_self_ingest_e2e.py -v` is
  green on a fresh checkout (no `~/.claude/` access required — the
  fixture is on-disk in the repo).
- The fixture's anonymisation is verifiable: `grep -RInE
  '/home/[^/]+|[A-Za-z0-9-]{20,}-[A-Za-z0-9-]{20,}'
  tests/fixtures/claude_code_self_ingest/` returns no real paths or
  real UUIDs.
- Running the same test against a temp-copy of a *real* session file
  (e.g., `cp ~/.claude/projects/-home-evan-workspace-corpus-forge/*.jsonl
  /tmp/test/`) also succeeds — confirms the test isn't accidentally
  hard-coded to fixture-only quirks.

## References

- Parser: `corpus_forge/sources/claude_code.py` (PR #29).
- Existing E2E pattern: `tests/integration/test_claude_code_session_link_e2e.py`,
  `tests/integration/test_chunk_reuse_e2e.py`,
  `tests/integration/test_feedback_loop_e2e.py`.
- Session link helper: `corpus_forge/sources/_session_link.py`.
- Ingest entrypoint: `corpus_forge/ingest.py::ingest_one`, `ingest_once`.
- Chunker: `corpus_forge/chunkers/conversation.py`.
