# Test Status — owned by tdd-tester (feat/embedder-routing)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T1 | red | tests/unit/test_embedder_config_routing.py — 11 tests; verified RED via `AttributeError: 'EmbedderConfig' object has no attribute 'extensions'`. |
| T2 | red | tests/unit/test_embedder_routing.py — 19 tests; module `corpus_forge.embedders.routing` does not exist yet → ImportError on every test. |
| T3 | red | tests/unit/test_embed_routing_filter.py (4 tests) + tests/unit/test_ingest_routing_filter.py (8 tests). All RED — `chunks_missing_embedding` 3-tuple shape + `active_embedders` kwarg not implemented yet. |
