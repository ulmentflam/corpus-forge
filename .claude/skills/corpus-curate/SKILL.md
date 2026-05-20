---
name: corpus-curate
description: Run the corpus-forge data-improvement chat loop. Use when the user wants to fortify low-confidence or metadata-poor entries — find the weakest, talk through the fix, and commit edits via MCP.
allowed-tools:
  - mcp__corpus-forge__next_curation_target
  - mcp__corpus-forge__next_curation_batch
  - mcp__corpus-forge__commit_curation
  - mcp__corpus-forge__list_datasets
  - mcp__corpus-forge__get_chunk
  - mcp__corpus-forge__search
---

## What corpus-forge curation is

`corpus-forge` indexes documents, chunks, conversations, and code into a
training-grade corpus. The curation loop is the human-in-the-loop step
that turns a freshly-ingested corpus into a *trained-on-able* one: the
ranker finds the weakest entry (low classifier confidence, sparse
metadata, anomalous embedding, freshly ingested), you and the user
agree on a fix, and the MCP server writes the edits back atomically.

Citations still matter — every entry you fortify is a candidate row in
the next training run.

## When to invoke

Invoke this skill when:

- The user says "let's curate", "improve my data", "fix labels", "this
  looks under-tagged", or any phrasing about *making the corpus
  better* (vs. searching it).
- The user just ran an ingest (`corpus-forge ingest …` or the daemon
  caught fresh files) and many recently-imported entries look thin on
  metadata.
- The user has asked twice for the same fact and the cited chunks are
  poorly labelled — a curation pass on the source is more useful than
  another search.
- The user explicitly asks the librarian / corpus-curate agent to run
  a curation pass.

## When NOT to invoke

Skip this skill when:

- The user asks a question that needs a citation — that's a job for the
  `corpus-forge-search` skill, not this one.
- The MCP server has `writes_enabled=False` — `commit_curation` will
  not be in `/mcp` and the loop is read-only. Surface that to the user
  and suggest they relaunch the server with writes enabled.
- The user is mid-flow on something else (writing code, debugging,
  running a long search) and didn't ask for curation. Don't drift the
  conversation.

## Tool playbook

Follow the five-step loop:

1. **Pick** — call `next_curation_target(dataset="<name>")`. The
   response carries `chunk_id`, `text`, `heading`, `current_labels`,
   `current_metadata`, `missing_fields`, `classifier_confidence`,
   `score`, `score_breakdown`, and a one-line `selection_reason`
   explaining why this entry was picked.
   - For batched mode (user said "let's batch many"), call
     `next_curation_batch(limit=N)` instead. The response includes a
     `cohesion_score` (0–1) so you can show the user *how tight* the
     group is before they ratify it.
2. **Present** — surface the entry to the user in a few lines:
   ```
   chunk #{chunk_id} (score {score:.2f}, {selection_reason})
   text   : {text[:200]}…
   labels : {current_labels}
   missing: {missing_fields}
   ```
3. **Ask** — at most three focused questions about what to fix. Useful
   prompts:
   - Should we add or remove labels? (Offer concrete suggestions when
     `missing_fields` includes `labels`.)
   - Does the heading or description need correcting?
   - Is there a factual correction or follow-up note worth recording as
     feedback?
4. **Commit** — call `commit_curation(chunk_id=..., add_labels=[...],
   remove_labels=[...], set_metadata={...}, set_description="...",
   feedback={...})`. The server routes each piece through the existing
   MCP write surface and returns a per-kind count plus a list of audit
   ids. Pass `dry_run=true` first when the change set is big.
5. **Loop** — ask "next one?". Yes → step 1. No → emit a short summary
   of what was changed this session (chunk_ids touched + counts per
   kind).

## Response handling

`next_curation_target` returns:

```json
{"target": {
  "chunk_id": 123, "document_id": 7,
  "text": "...", "heading": null,
  "current_labels": [["topic", "ml"]],
  "current_metadata": {"language": "en"},
  "missing_fields": ["description"],
  "classifier_confidence": 0.42,
  "score": 0.61,
  "score_breakdown": {
    "confidence_deficit": 0.58, "missing_metadata": 0.17,
    "ranker_elevation": 0.50, "freshness": 1.0
  },
  "selection_reason": "classifier confidence 0.42"
}}
```

`commit_curation` returns:

```json
{"writes": {"add_label": 2, "remove_label": 0,
            "set_metadata": 1, "set_description": 1, "add_feedback": 1},
 "chunk_ids_processed": [123], "dry_run": false,
 "audit_ids": [501, 502, 503, 504, 505]}
```

## Citation format

When quoting a chunk during the chat, use:

```
From {title} ({source_uri}): {quote}
```

Keep quotes ≤ 2 sentences; call `get_chunk` for the full text when the
user needs more context.

## When to call `record_demonstration`

`record_demonstration` captures a *generalizable* edit as a supervised
demonstration triple for fine-tuning (SDFT format). Use it when the
curation session surfaces an improvement that would teach a future model
*how to curate*, not merely *what this entry should say*.

### Demonstration triple shape

```json
{
  "query": "<the question or text that prompted the edit>",
  "student_messages": [
    {"role": "assistant", "content": "<original / before state>"}
  ],
  "teacher_messages": [
    {"role": "assistant", "content": "<improved / after state>"}
  ],
  "target": "<the final committed value (description, label, etc.)>",
  "source": "claude_code",
  "dataset_id": "<dataset name>"
}
```

### Call it for generalizable edits — not pure metadata fixes

Call `record_demonstration` when:
- You rewrote a description that was factually thin or misleading.
- You reassigned labels and the rationale would transfer to other similar
  entries (e.g. "short code snippets without docstrings get the
  `needs_docstring` label").
- The user articulated a *policy* during the fix ("always add the
  language tag for multilingual docs").

Do **not** call it when:
- The edit is purely structural (adding a missing `language` metadata
  key, fixing a typo in a field name).
- The commit only adjusts numeric metadata (timestamps, token counts).
- The fix is idiosyncratic to a single entry with no transfer value.

### Source value for this client

Always set `source="claude_code"` when recording demonstrations from a
Claude Code session. This value identifies the capture origin in the
`sdft_demonstrations` table and lets the fine-tuning pipeline filter by
assistant client.

### MCP tool names (all four)

| Tool | When to call |
|------|-------------|
| `record_demonstration` | Capture a generalizable curated edit as an SDFT triple |
| `commit_curation` | Atomically write label / metadata / description edits |
| `rate_search_result` | Record thumbs-up/down on a retrieved chunk |
| `add_feedback` | Attach free-text feedback to a chunk without a full curation commit |
