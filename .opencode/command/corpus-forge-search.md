---
name: corpus-forge-search
description: Search a corpus-forge training corpus via its MCP server. Use when the user asks a question that should be grounded in indexed content (codebases, docs, conversations) rather than answered from model knowledge.
allowed-tools:
  - mcp__corpus-forge__search
  - mcp__corpus-forge__get_chunk
  - mcp__corpus-forge__list_datasets
  - mcp__corpus-forge__render_conversation
  - mcp__corpus-forge__list_chat_templates
---

## What is corpus-forge

`corpus-forge` is a local training-corpus indexer. It chunks files, embeds them,
and exposes a hybrid (dense + lexical) retriever over MCP. The corpus it
searches is whatever the operator told it to index — typically a codebase,
documentation set, or curated conversation history that **should** ground the
assistant's answers.

The tool lives upstream of training: the corpus you search here is also a
candidate corpus for fine-tuning. Citations matter.

## When to invoke

Invoke this command when:

- The user asks a question whose answer plausibly lives in the indexed corpus
  ("how does the chunker handle markdown?", "what does the daemon log on
  startup?", "find me past discussions about pgvector tuning").
- The user asks for citations, "where in the repo", "show me the code that…",
  "what does our doc say about…".
- The user explicitly asks corpus-forge / the librarian / the research agent
  to look something up.
- You'd otherwise guess at internal jargon, file paths, or version-specific
  behaviour that the corpus knows authoritatively.

## When NOT to invoke

Skip this command when:

- The user is asking about **the corpus-forge project itself** (its CLI flags,
  this command, the MCP wire format) — that's documentation, not corpus content.
- The question is general programming knowledge ("how do I write a Python
  generator?") with no project-specific grounding required.
- The user is in the middle of an edit/run/test loop and just needs the tool
  in front of them, not a citation.
- A previous `search` call in this conversation already returned the relevant
  chunks — re-use them rather than re-querying.

## Tool playbook

1. **Survey first** with `list_datasets()` if you don't already know which
   datasets are loaded. The response is `{"datasets": [{name, kind,
   description, document_count, chunk_count}, ...]}`. Pick the most relevant
   `name` for downstream `dataset=` scoping.

2. **Hybrid search** with `search(query, k=10)` as the default. Knobs:
   - `query` (required string) — natural-language question or keyword.
   - `k` (int, default 10) — number of hits to retrieve.
   - `dataset` (str | null) — scope to a single dataset; omit for cross-corpus.
   - `fusion` (`"rrf"` | `"alpha"`, default `"rrf"`) — how dense+lexical fuse.
   - `alpha` (float, default 0.5) — only used when `fusion="alpha"`.
   - `rerank` (bool, default **false**) — opt-in cross-encoder rerank.
   - `rerank_top_n` (int) — number of fused hits the reranker scores.

3. **Reranker is opt-in** — `rerank=true` triggers a one-time **600 MB**
   download of `BAAI/bge-reranker-v2-m3` on the server. Use it only when
   top-of-list precision matters (e.g. a citation must be right, not "close
   enough"). Default queries should stay `rerank=false`.

4. **Pull full context** with `get_chunk(chunk_id)` when a hit looks
   promising but the preview text is too short. The response is the full
   chunk record (or a `CallToolResult(isError=True, text="chunk_id={n} not
   found")` on miss). Chain multiple `get_chunk` calls when you need
   surrounding context.

5. **Conversation tools** — use `render_conversation` to format retrieved
   conversation chunks into readable dialogue, and `list_chat_templates` to
   see what chat formatting templates are available for fine-tuning output.

## Response handling

`search` returns a wrapped payload:

```json
{"hits": [{"chunk_id": 123, "score": 0.91, "text": "...", "document_id": 7,
           "conversation_id": null, "message_id": null,
           "source_uri": "file://...", "title": "...",
           "dataset_id": 2, "metadata": {...}, "source": "fused"}, ...]}
```

`source` tells you **how** the hit surfaced:

- `"dense"` — only the vector retriever liked it.
- `"lexical"` — only the BM25 / sparse retriever liked it.
- `"fused"` — both retrievers liked it; usually the strongest signal.
- `"reranked"` — promoted by the cross-encoder (only present when
  `rerank=true`).

Prefer `"fused"` and `"reranked"` hits when picking citations.

## Citation format

When quoting a hit in your answer, use:

```
From {title} ({source_uri}): {quote}
```

Keep the quote short (≤ 2 sentences); call `get_chunk` for the full chunk
text if the reader will need it. Always include the `source_uri` so the user
can jump directly to the source.
