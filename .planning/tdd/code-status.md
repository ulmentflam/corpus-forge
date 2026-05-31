# Code Status — owned by tdd-coder (feat/llama-cpp-tuning)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T4 | green | corpus_forge/embedders/llama_cpp.py — constructor gains `n_seq_max=1`, `n_batch=None`, `n_ubatch=None`; n_batch / n_ubatch default to n_ctx when None. New `_maybe_truncate(text, n_ctx_seq)` helper does the per-chunk tokenize+slice+detokenize with a DEBUG log. `_load_llama_handle` forwards new kwargs to Llama() AND post-mutates `handle.context_params.n_seq_max` for forward-compat / introspection. |
| T5 | green | corpus_forge/config.py — EmbedderConfig adds `n_seq_max: int = Field(default=1, gt=0)`, `n_batch: int \| None = Field(default=None, gt=0)`, `n_ubatch: int \| None = Field(default=None, gt=0)`. corpus_forge/embedders/registry.py — `_per_provider_extras` llama-cpp branch always forwards `n_seq_max`, forwards `n_batch` / `n_ubatch` only when set. |
| T6 | green | config.example.toml — added n_seq_max + n_batch + n_ubatch under existing commented `[llama-cpp]` block, with a multi-line comment explaining the `n_ctx_seq = n_ctx / n_seq_max` relationship + the `n_batch`/`n_ubatch` default-to-n_ctx rule. README.md `[llama-cpp]` row — appended a Gotchas sentence covering n_seq_max + client-side truncation. |
