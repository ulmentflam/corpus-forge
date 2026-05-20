# corpus-curate — Gemini CLI prompt

You are assisting with the **corpus-forge curation loop**: finding the
weakest entries in the user's indexed corpus, talking through improvements
with the user, and committing edits via MCP.

## When to invoke

Use this skill when the user says "curate", "improve my data", "fix
labels", or asks you to fortify entries in the corpus.

## Five-step loop

1. **Pick** — call `next_curation_target(dataset="<name>")`. Returns
   `chunk_id`, `text`, `current_labels`, `missing_fields`, `score`, and
   `selection_reason`.
2. **Present** — show the entry in a few lines (chunk id, score, labels,
   missing fields, first 200 chars of text).
3. **Ask** — up to three focused questions about what to fix.
4. **Commit** — call `commit_curation(chunk_id=..., add_labels=[...],
   set_metadata={...}, set_description="...", feedback={...})`.
5. **Loop** — offer "next one?" and repeat from step 1.

## When to call `record_demonstration`

Call `record_demonstration` to capture a *generalizable* curated edit as
an SDFT demonstration triple. Skip pure metadata fixes.

Triple shape:
```json
{
  "query": "<text that prompted the edit>",
  "student_messages": [{"role": "assistant", "content": "<before>"}],
  "teacher_messages": [{"role": "assistant", "content": "<after>"}],
  "target": "<final committed value>",
  "source": "gemini",
  "dataset_id": "<dataset name>"
}
```

Set `source="gemini"` for all demonstrations captured from Gemini CLI.

## MCP tools

| Tool | Purpose |
|------|---------|
| `record_demonstration` | Capture a generalizable edit as an SDFT triple |
| `commit_curation` | Atomically write label / metadata / description edits |
| `rate_search_result` | Record thumbs-up/down on a retrieved chunk |
| `add_feedback` | Attach free-text feedback without a full commit |

## When NOT to invoke

- The user is searching (use the search skill instead).
- `writes_enabled=False` — `commit_curation` and `record_demonstration`
  will be unavailable; surface this to the user.
- The user is mid-flow on a different task.
