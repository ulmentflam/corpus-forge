# /corpus-curate — OpenCode slash-command

Run the **corpus-forge curation loop**: find the weakest entries in your
indexed corpus, talk through improvements, and commit edits via MCP.

## Usage

```
/corpus-curate [dataset=<name>] [batch=<n>]
```

## When to use

Invoke when the user says "curate", "improve my data", "fix labels", or
"fortify this corpus entry". Also trigger when recently-ingested entries
look thin on metadata.

## Five-step loop

1. **Pick** — `next_curation_target(dataset="<name>")` (or
   `next_curation_batch(limit=N)` for bulk mode).
2. **Present** — surface `chunk_id`, `score`, `current_labels`,
   `missing_fields`, and the first 200 chars of `text`.
3. **Ask** — at most three focused questions about what to fix.
4. **Commit** — `commit_curation(chunk_id=..., add_labels=[...],
   set_metadata={...}, set_description="...", feedback={...})`.
5. **Loop** — "next one?" loops to step 1.

## SDFT capture — `record_demonstration`

After committing a *generalizable* edit, call `record_demonstration` to
capture it as an SDFT demonstration triple for fine-tuning.

```json
{
  "query": "<text that prompted the edit>",
  "student_messages": [{"role": "assistant", "content": "<before>"}],
  "teacher_messages": [{"role": "assistant", "content": "<after>"}],
  "target": "<final committed value>",
  "source": "opencode",
  "dataset_id": "<dataset name>"
}
```

Always set `source="opencode"` for demonstrations captured in OpenCode.
Skip `record_demonstration` for pure metadata or structural fixes.

## MCP tools

| Tool | Purpose |
|------|---------|
| `record_demonstration` | Capture a generalizable edit as an SDFT triple |
| `commit_curation` | Atomically write label / metadata / description edits |
| `rate_search_result` | Record thumbs-up/down on a retrieved chunk |
| `add_feedback` | Attach free-text feedback without a full commit |

## Notes

- Requires `corpus-forge` MCP server running with `writes_enabled=True`.
- If `commit_curation` is not available, surface that to the user.
