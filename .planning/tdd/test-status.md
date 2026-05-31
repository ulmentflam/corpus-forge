# Test Status — owned by tdd-tester
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T1 | red | tests/unit/test_embedder_llama_cpp.py — 50 tests collected; fails on ImportError of corpus_forge.embedders.llama_cpp (expected). |
| T2 | red | tests/unit/test_pyproject_llama_cpp_extra.py + tests/unit/test_embedder_config_llama_cpp.py — provider regex / fields not yet added. |
| T3 | red | tests/unit/test_embedder_register_from_config.py — 5 new tests appended; will fail until registry + per-provider-extras lands. |
