# Code Status — owned by tdd-coder
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T4 | green | corpus_forge/embedders/llama_cpp.py — LlamaCppEmbedder + resolve_gguf_path + _load_llama_handle. Resolver lives behind the loader seam so unit tests patching _load_llama_handle don't have to stand up a real GGUF on disk. Lazy import via LLAMA_CPP_AVAILABLE module-level bool. No Matryoshka truncation in this first cut — llama-cpp returns native dim verbatim; mismatch raises ValueError. |
| T5 | green | corpus_forge/embedders/registry.py — `"llama-cpp"` dispatch + `_per_provider_extras` branch forwarding n_ctx, n_gpu_layers, gguf_path (only when truthy). corpus_forge/config.py — EmbedderConfig regex now `r"^(sentence_transformers|openai|model2vec|llama\-cpp)$"`, three optional fields added (gguf_path / n_ctx / n_gpu_layers). |
| T6 | green | pyproject.toml — `llama-cpp = ["llama-cpp-python>=0.3"]` with neighbor comment explaining Metal CMAKE_ARGS. README.md — extras table row added. config.example.toml — new commented `[[embedders]]` block between qwen3_8b and openai_3l with `active = false`, full GGUF resolver rule explanation, and cross-reference to the deactivated qwen3-4096 in the user's `~/.config/corpus-forge/config.toml`. |
