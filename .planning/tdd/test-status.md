# Test Status — owned by tdd-tester (feat/llama-cpp-tuning)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T1 | red | tests/unit/test_embedder_llama_cpp.py — added TestTuningIdentity (6 tests), TestTruncation (8 tests), TestLoaderForwardsTuningKwargs (1 test), plus 1 new gated smoke test. Verified RED by attribute errors (`n_seq_max` missing on embedder). |
| T2 | red | tests/unit/test_embedder_config_llama_cpp.py — added 9 tests for n_seq_max / n_batch / n_ubatch field pins. Verified RED (9 failures, AttributeError on missing fields). |
| T3 | red | tests/unit/test_embedder_register_from_config.py — appended TestPerProviderExtrasLlamaCppTuning (6 tests) + TestRegisterFromConfigLlamaCppTuning (2 tests). |
