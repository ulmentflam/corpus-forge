# Code Status — owned by tdd-coder (feat/llama-cpp-runtime-n-ctx-seq)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T1 | green | corpus_forge/embedders/llama_cpp.py: added `self._runtime_logged: bool = False` to __init__; replaced n_ctx_seq computation in encode() with runtime introspection of llama_cpp.llama_n_ctx / llama_n_seq_max via self._llama._ctx.ctx, safety margin -4, floor 64, fallback to configured-value math on AttributeError/TypeError/ImportError. Widened exception tuple beyond user-spec to include ImportError because llama_cpp may not be installed in test/minimal-install environments where fake _llama is injected — without it the import crashes the encode path; the wider catch preserves the user-spec intent (fall back to configured values) and is the only way the existing fake-handle test suite stays green. Added once-per-instance INFO log "LlamaCppEmbedder runtime n_ctx_seq" with runtime AND configured (n_ctx, n_seq_max) plus lookup_ok flag. |
