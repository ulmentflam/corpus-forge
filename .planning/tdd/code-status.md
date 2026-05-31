# Code Status — owned by tdd-coder (feat/embedder-routing)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T4 | green | `EmbedderConfig.extensions: list[str]` + `_normalise_extensions` field-validator (lowercase, leading-dot enforced). `Config._check_routing_invariant` model-validator delegates to `validate_routing_invariant`. 11/11 RED tests pass. |
| T5 | green | `corpus_forge/embedders/routing.py` (~150 LOC) — `extension_for`, `claims`, `route_for`, `validate_routing_invariant`, `EmbedderRoutingError`. `BaseEmbedder.__init__` + every concrete embedder (`sentence_transformers`, `openai`, `model2vec`, `llama_cpp`) accept `extensions` kwarg and forward to super. `_per_provider_extras` adds `extensions` to every provider's dispatch dict. 24/24 routing-module tests pass. |
| T6 | green | `StorageBackend.chunks_missing_embedding` widened to `Iterator[tuple[int, str, str]]`. Postgres + SQLite both JOIN `documents` + `conversations` (COALESCE — chunks XOR the two parents) so `source_uri` rides along. |
| T7 | green | `embed.backfill_embedder` filters per-batch via `route_for(...)`; breaks the loop when every row routed away (no infinite-page loop). `_write_embeddings_for_chunks` gains `active_embedders` kwarg (default `[embedder]`) for the same filter; `_flush_all_pending_embeddings` threads the full active list through. 13/13 routing-filter tests pass. |
| T8 | green | `config.example.toml` carries a commented dual-tower block (nomic catchall + nomic-code specialist via llama-cpp). `README.md` gains a "Dual-tower retrieval (extension-based routing)" subsection under embedder recommendations. |
| BACKCOMPAT | green | Updated 10 pre-existing test files to the new 3-tuple shape + `extensions=[]` mocks: test_embed_backfill, test_ingest_embedders, test_remaining, test_ingest_extended, test_embed_extended, test_cli_eval_embedders, test_embed_progress, test_postgres_backend, test_sqlite_backend, test_eval_runner. Single production consumer in `cli.py` (`corpus-forge eval embedders`) strips source_uri to keep the eval signature unchanged. |
