# Embedding-model recommendations (per lane)

corpus-forge is multi-embedder by design — you can declare several `[[embedders]]`
blocks and backfill them independently — but the choice of *which* embedder to
run for a given corpus is yours. This document is a **grounded survey** of
strong embedding models in four common lanes (**English text retrieval**,
**code**, **multilingual**, **multimodal (image+text)**), with a clear *default*,
a *fast/local* option, and an *API* option per lane. It is the qualitative,
literature-grounded half of the picture; the empirical complement — the
on-machine A/B ranking that times and scores candidate embedders against *your*
corpus — is a separate RFC item (`.planning/rfcs/`) and should be the tiebreaker
whenever two picks are close.

> **Freshness caveat.** Embedding leaderboards move fast. Repo metadata
> (ids, licenses, parameter counts, architectures) below was verified against
> the Hugging Face Hub via MCP at authoring time; leaderboard standings were
> cross-checked against the live MTEB/MMTEB, CoIR, and ViDoRe leaderboards
> (URLs in [Sources](#sources)). The assistant's own knowledge cutoff is
> ~January 2026, and the leaderboards re-rank continuously, so treat *relative*
> standing as more durable than any single score, and re-check the linked
> leaderboards before committing to a model for a long-lived corpus.
> Where an exact MTEB/CoIR number could not be confirmed from a primary source
> it is given as a range or as relative standing rather than a fabricated point
> value.

Dimensions marked "MRL" use Matryoshka Representation Learning — the model
emits a long vector you may safely truncate to a shorter prefix (e.g. 1024→256)
for a modest quality loss and a large storage saving. Set `dimension` in the
`[[embedders]]` block to the prefix you actually store.

---

## English text retrieval

| Model | HF repo id / API | Local/API | License | Dim(s) | Max context | When to pick it |
|---|---|---|---|---|---|---|
| **Qwen3-Embedding-8B** *(default, quality)* | `Qwen/Qwen3-Embedding-8B` | Local | Apache-2.0 | 4096 (MRL-shrinkable) | 32K | Best open-weight quality; you have a GPU (~16 GB+) and want top retrieval accuracy. corpus-forge's `config.example.toml` default. |
| Qwen3-Embedding-4B *(mid)* | `Qwen/Qwen3-Embedding-4B` | Local | Apache-2.0 | 2560 (MRL) | 32K | Most of 8B's quality at ~half the VRAM. |
| stella_en_1.5B_v5 *(strong small)* | `NovaSearch/stella_en_1.5B_v5` | Local | MIT | 1024 / up to 8192 (MRL heads) | ~8K (512–1024 typical) | High MTEB-English standing for 1.5B params; good quality/size midpoint. Needs `trust_remote_code`. |
| BGE-large-en-v1.5 *(proven baseline)* | `BAAI/bge-large-en-v1.5` | Local | MIT | 1024 | 512 | Battle-tested, cheap, fast on CPU/modest GPU. Short 512-token window — chunk accordingly. |
| **Qwen3-Embedding-0.6B** *(fast/local)* | `Qwen/Qwen3-Embedding-0.6B` | Local | Apache-2.0 | 1024 (MRL) | 32K | CPU-friendly, long context, same recipe as the 8B; the pragmatic laptop default. |
| nomic-embed-text-v1.5 *(fast/local alt)* | `nomic-ai/nomic-embed-text-v1.5` | Local | Apache-2.0 | 768 (MRL → 256/128) | 8192 | Tiny (~137M), long context, MRL — great when storage/latency dominate. |
| **OpenAI text-embedding-3-large** *(API)* | `text-embedding-3-large` | API | Proprietary (paid) | 3072 (MRL → 1024/256) | 8191 | No local GPU; want a managed endpoint. Solid but now trails the best open-weight 8B models on MTEB-English. |
| Voyage / Cohere *(API alt)* | `voyage-3-large` / `embed-v3` (`embed-english-v3.0`) | API | Proprietary (paid) | 1024–2048 (MRL) / 1024 | 32K / 512 | Voyage-3-large posts top-tier API retrieval scores at low dims (cheap vector DB); Cohere v3 is mature but short-context. |

**Tradeoffs.** On the MTEB v2 / MMTEB English boards the frontier is currently
led by large proprietary models (Google's Gemini Embedding sits at the top of
English MTEB v2 in early-2026 snapshots), with open-weight Qwen3-Embedding-8B and
NVIDIA's NV-Embed family close behind — the open/closed gap has narrowed to a few
points. For a *local* corpus, Qwen3-Embedding-8B is the quality default and
0.6B the latency default; both are Apache-2.0 (commercially safe) and share an
asymmetric query-instruction recipe that corpus-forge's `encode_query` path
supports. Note that some chart-topping models carry non-commercial licenses —
e.g. **NV-Embed-v2 is CC-BY-NC-4.0** — so verify the license before shipping a
product on a leaderboard leader. (Sources: [MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard),
secondary MTEB-2026 roundups in [Sources](#sources).)

---

## Code

| Model | HF repo id / API | Local/API | License | Dim(s) | Max context | When to pick it |
|---|---|---|---|---|---|---|
| **nomic-embed-code** *(default, quality)* | `nomic-ai/nomic-embed-code` | Local | Apache-2.0 | 3584 | ~8K (Qwen2.5-Coder base) | Strong open-weight text↔code retrieval (CoIR-trained on the CoRNStack data); pick when you have GPU headroom (~7B params). |
| Qwen3-Embedding (8B/4B/0.6B) *(general+code)* | `Qwen/Qwen3-Embedding-{8B,4B,0.6B}` | Local | Apache-2.0 | 4096 / 2560 / 1024 (MRL) | 32K | One embedder for prose *and* code; long context suits whole-file chunks. Best when a single index must serve mixed corpora. |
| jina-embeddings-v2-base-code *(small)* | `jinaai/jina-embeddings-v2-base-code` | Local | Apache-2.0 | 768 | 8192 (ALiBi) | ~161M params, 30 programming languages, long context, cheap to run. Good CPU/GPU midpoint. |
| **potion-code-16M** *(fast/local, static)* | `minishlab/potion-code-16M` | Local | MIT | 256 | n/a (static, token-pooled) | Tokenizer-static (`model2vec`), ~16 MB, CPU-instant. corpus-forge's recommended `fast_tier_embedder_name` for the shortcut/rerank tier — not a standalone quality index. |
| **Voyage voyage-code-3** *(API)* | `voyage-code-3` | API | Proprietary (paid) | 1024 (MRL → 256, int8/binary) | 32K | Best-documented code-retrieval API; Voyage reports large gains over OpenAI-v3-large on code and quantizes to low dims to cut vector-DB cost. |

**Tradeoffs.** Code retrieval is benchmarked primarily on **CoIR** (CoIR: A
Comprehensive Benchmark for Code Information Retrieval Models, ACL 2025), whose
results feed the MTEB "Code" task group. General-purpose top models (Gemini
Embedding, Qwen3-Embedding) score very well on code too, so a dedicated
code model is only worth it when code dominates your corpus. For a local index,
`nomic-embed-code` is the quality pick and `jina-embeddings-v2-base-code` the
lighter one; `potion-code-16M` is a *speed tier*, not a quality tier — wire it
in front of a dense embedder (see [retrieval] `fast_tier_embedder_name` in
`config.example.toml`), not as the only index. On the API side `voyage-code-3`
is the strongest documented option. (Sources: [CoIR paper](https://arxiv.org/abs/2407.02883)
/ [CoIR org](https://huggingface.co/CoIR-Retrieval), [MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard)
Code tab, [voyage-code-3 announcement](https://blog.voyageai.com/2024/12/04/voyage-code-3/).)

### EOS/SEP terminator — `append_eos`

The **nomic-embed** family (text *and* code) is trained to terminate each
input with the model's EOS/SEP token; its pooled sentence embedding assumes
that terminator is present. `nomic-embed-code` in particular uses
**last-token pooling** (the embedding *is* the final token's hidden state),
so a missing terminator doesn't merely dilute the vector — it anchors the
whole embedding on the wrong token. A GGUF served with
`tokenizer.ggml.add_eos_token` unset (the common `manutic/nomic-embed-code`
blob does) drops the terminator silently, which is what the per-input
`... at least one last token ... is not SEP ... add_eos_token should be set
to 'true'` server warning is telling you.

corpus-forge fixes this client-side with the per-embedder `append_eos`
flag, so correctness no longer depends on each host's GGUF header:

```toml
[[embedders]]
name      = "nomic-code"
provider  = "llama-cpp"
model_id  = "manutic/nomic-embed-code:latest"
# append_eos is inferred from the known-model registry (true for the
# nomic-embed family); set it explicitly to override.
append_eos = true
```

Left unset, `append_eos` is resolved from a small known-model registry
(`corpus_forge/embedders/known_models.py`) that defaults the nomic-embed
family to `true`. For the in-process `llama-cpp` transport the terminator
is appended at the **token layer** (the EOS is a no-surface-form special
token, so a string append can't reproduce it).

> **Re-embed after enabling.** Turning `append_eos` on changes the vectors
> a model produces, so it is folded into the embedder **fingerprint**:
> enabling it makes the embedder show as *drifted* against the vectors
> already in your corpus. `corpus-forge doctor` surfaces the drift and the
> estimated re-embed cost, and the daemon's re-embed loop recomputes the
> affected embedder's vectors (or run `corpus-forge embed -e <name>` to
> backfill). Re-embedding is deliberate, **not** an implicit consequence of
> upgrading — until it runs, old (un-terminated) and new (terminated)
> vectors coexist in the same table and retrieve inconsistently. An
> `append_eos=False` embedder keeps its existing fingerprint, so models that
> never wanted a terminator are untouched.

---

## Multilingual

| Model | HF repo id / API | Local/API | License | Dim(s) | Max context | When to pick it |
|---|---|---|---|---|---|---|
| **Qwen3-Embedding-8B** *(default, quality)* | `Qwen/Qwen3-Embedding-8B` | Local | Apache-2.0 | 4096 (MRL) | 32K | Tops the MMTEB (multilingual) board in early-2026 snapshots; long context, 100+ languages, Apache-2.0. Pick when quality outweighs VRAM. |
| BGE-M3 *(versatile)* | `BAAI/bge-m3` | Local | MIT | 1024 | 8192 | One model, three retrieval modes (dense + sparse/lexical + ColBERT-style multi-vector), 100+ languages. Excellent default when you want hybrid retrieval from a single checkpoint. |
| jina-embeddings-v3 *(task-LoRA)* | `jinaai/jina-embeddings-v3` | Local | **CC-BY-NC-4.0** | 1024 (MRL → 32) | 8192 | Strong multilingual quality with task-specific LoRA adapters. **Non-commercial license — flag before shipping a product.** |
| gte-multilingual-base *(small)* | `Alibaba-NLP/gte-multilingual-base` | Local | Apache-2.0 | 768 (MRL) | 8192 | ~305M params, 70+ languages, long context — the efficient multilingual workhorse. |
| **multilingual-e5-large-instruct** *(fast/local)* | `intfloat/multilingual-e5-large-instruct` | Local | MIT | 1024 | 512 | Mature, well-understood, instruction-tuned XLM-R (~560M). Short 512 window — chunk small. The safe, permissive multilingual baseline. |
| **Cohere embed-multilingual-v3** *(API)* | `embed-multilingual-v3.0` | API | Proprietary (paid) | 1024 | 512 | Managed endpoint, 100+ languages, mature. Short context; consider Voyage-3 (32K) or Cohere embed-v4 (128K) when long multilingual docs matter. |

**Tradeoffs.** On MMTEB, Qwen3-Embedding-8B held the #1 multilingual slot in
early-2026 snapshots, with BGE-M3, multilingual-E5, gte-multilingual, and
jina-v3 forming a strong open-weight tier below it. The biggest practical
splits are **license** and **context window**: gte-multilingual-base, the E5
family, BGE-M3, and Qwen3 are all permissive (Apache/MIT), whereas **jina-v3 is
CC-BY-NC-4.0** (non-commercial). BGE-M3 is uniquely attractive if you want
dense + lexical + multi-vector retrieval from one model. For latency-bound or
CPU deployments, multilingual-E5-large-instruct or gte-multilingual-base are the
fast picks (mind E5's 512-token window). (Sources: [MTEB/MMTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard),
[BGE-M3 paper](https://arxiv.org/abs/2402.03216).)

---

## Multimodal (image + text)

| Model | HF repo id / API | Local/API | License | Dim(s) | Max ctx / input | When to pick it |
|---|---|---|---|---|---|---|
| **SigLIP 2 (so400m)** *(default, dense)* | `google/siglip2-so400m-patch14-384` | Local | Apache-2.0 | 1152 | 384px image / short text | Strong open dense image↔text encoder; better than original CLIP on zero-shot + retrieval. The quality default for single-vector image search. |
| OpenCLIP ViT-H/14 *(big dense)* | `laion/CLIP-ViT-H-14-laion2B-s32B-b79K` | Local | MIT | 1024 | 224px / 77 tokens | LAION-2B-trained, widely used, MIT. Pick when you want a large, permissive, well-studied CLIP. Very short text side (77 tokens). |
| CLIP ViT-L/14 *(baseline)* | `openai/clip-vit-large-patch14` | Local | (MIT-style, OpenAI weights) | 768 | 224px / 77 tokens | The classic baseline — small, fast, ubiquitous. Use as a cheap reference or when downstream tooling assumes CLIP-L. |
| jina-clip-v2 *(multilingual)* | `jinaai/jina-clip-v2` | Local | **CC-BY-NC-4.0** | 1024 (MRL → 64) | 512 tokens text / image | Multilingual text side + longer text context than vanilla CLIP. **Non-commercial license — flag before shipping.** |
| nomic-embed-vision-v1.5 *(fast/local)* | `nomic-ai/nomic-embed-vision-v1.5` | Local | Apache-2.0 | 768 | image / pairs with nomic-text-v1.5 | Tiny (~93M) image tower aligned to `nomic-embed-text-v1.5` — shared text+image space, MRL, permissive. The lightweight local pick. |
| ColQwen2 / ColPali *(page-image retrieval)* | `vidore/colqwen2-v1.0` | Local | Apache-2.0 | multi-vector (late interaction) | full document page image | "Screenshot RAG": embed *rendered PDF/slide pages* directly, no OCR. Multi-vector (ColBERT-style) — needs a late-interaction-aware store, not a plain single-vector index. |
| **Voyage / Cohere multimodal** *(API)* | `voyage-multimodal-3` / `embed-v4` | API | Proprietary (paid) | model-defined / configurable | interleaved text+image; Cohere v4 up to 128K | Best managed option for interleaved docs (PDFs, screenshots, tables). Voyage-multimodal-3(.5) and Cohere embed-v4 trade the top spot across visual-doc benchmarks. |

**Tradeoffs.** Two distinct families live in this lane. **Single-vector dual
encoders** (SigLIP 2, OpenCLIP, CLIP, jina-clip, nomic-vision) put images and
text in one cosine space and drop straight into a normal vector index —
SigLIP 2 is the current open quality default, nomic-embed-vision the
lightweight one, and OpenCLIP ViT-H the big permissive option. **Late-interaction
page-image models** (ColPali / ColQwen) lead the **ViDoRe** visual-document
benchmarks for retrieving over *rendered pages* (charts, tables, scanned PDFs)
without OCR, but emit multi-vector representations that a single-vector store
can't index as-is. For managed APIs, Voyage-multimodal-3(.5) and Cohere embed-v4
are the strongest interleaved-document options (Cohere v4 notably offers a 128K
context). Watch licensing: jina-clip-v2 is **CC-BY-NC-4.0**; SigLIP 2, OpenCLIP
ViT-H, nomic-vision, and ColQwen2 are permissive (Apache/MIT). (Sources:
[ViDoRe v2 blog](https://huggingface.co/blog/manu/vidore-v2),
[ColPali paper](https://arxiv.org/abs/2407.01449),
[SigLIP 2 paper](https://arxiv.org/abs/2502.14786),
[voyage-multimodal-3](https://blog.voyageai.com/2024/11/12/voyage-multimodal-3/),
[Cohere embed-v4](https://docs.cohere.com/changelog/embed-multimodal-v4).)

---

## Mapping to corpus-forge

corpus-forge selects an embedder backend from the `provider` field of each
`[[embedders]]` block (see `corpus_forge/embedders/registry.py`). The picks
above map onto providers as follows:

| Pick type | corpus-forge `provider` | Backend class | Notes |
|---|---|---|---|
| HF dense text models (Qwen3, BGE, E5, stella, nomic-text, gte, jina-v2-code, nomic-code, BGE-M3, jina-v3) | `sentence_transformers` | `SentenceTransformersEmbedder` (`sentence_transformers.py`) | Auto-resolves `device = "auto"` to CUDA/MPS/CPU. Models with `custom_code` (stella, gte, jina, nomic) require `trust_remote_code`. |
| OpenAI / Cohere / Voyage / any OpenAI-compatible HTTP (incl. Ollama, vLLM, TEI) | `openai` | `OpenAIEmbedder` (`openai.py`) | Set `model_id`, `api_key_env`, and (for non-OpenAI hosts) `base_url`. This is the path for the API picks and for self-hosting an HF model behind vLLM/Ollama. |
| Static / fast tier (`potion-code-16M`) | `model2vec` | `Model2VecEmbedder` (`model2vec.py`) | CPU-only, no device kwarg. Wire as `[retrieval].fast_tier_embedder_name`, not as the sole index. |
| Multimodal — local CLIP-family (SigLIP 2, OpenCLIP, CLIP, jina-clip, nomic-vision) | (multimodal, activated on-demand) | `ClipLocalEmbedder` (`clip_local.py`) | In-process via `sentence-transformers`; produces the `image_embeddings_<name>` table. |
| Multimodal — remote (`voyage-multimodal-3`, `embed-v4`, self-hosted CLIP service) | (multimodal, OpenAI-compatible HTTP) | `ClipRemoteEmbedder` (`clip_remote.py`) | Talks to any `POST {base_url}/embeddings` accepting interleaved text+image input. |
| Late-interaction page-image (ColPali/ColQwen) | *not yet first-class* | — | Multi-vector / late-interaction retrieval isn't a built-in `[[embedders]]` provider today; listed as a forward-looking option for visual-document corpora. |

Copy-paste `[[embedders]]` config blocks for the recommended picks live in the
README's recommendations section (the RFC box) rather than being duplicated
here; the canonical, fully-commented schema for an `[[embedders]]` block — with
the static-tier (`model2vec`) and multimodal examples — is in
[`config.example.toml`](../config.example.toml), and the backend classes are
under [`corpus_forge/embedders/`](../corpus_forge/embedders/). Remember the
backfill workflow: add the block, keep existing embedders `active`, then
`corpus-forge embed --embedder <name>` to encode only the missing vectors.

---

## Sources

Leaderboards (re-check before committing — they re-rank continuously):

- **MTEB / MMTEB leaderboard** — https://huggingface.co/spaces/mteb/leaderboard
- **CoIR (Code Information Retrieval) benchmark** — paper: https://arxiv.org/abs/2407.02883 ; org/data: https://huggingface.co/CoIR-Retrieval ; code: https://github.com/CoIR-team/coir (CoIR results are also surfaced under the MTEB leaderboard's Code task group)
- **ViDoRe (Visual Document Retrieval) benchmark v2** — https://huggingface.co/blog/manu/vidore-v2 ; paper: https://arxiv.org/abs/2505.17166

Key model papers / pages (repo metadata verified via HF Hub MCP):

- Qwen3-Embedding — https://huggingface.co/Qwen/Qwen3-Embedding-8B (paper arXiv:2506.05176)
- BGE-large-en-v1.5 / BGE-M3 — https://huggingface.co/BAAI/bge-large-en-v1.5 ; https://huggingface.co/BAAI/bge-m3 (BGE-M3 paper arXiv:2402.03216)
- E5 — https://huggingface.co/intfloat/e5-large-v2 ; https://huggingface.co/intfloat/multilingual-e5-large-instruct (papers arXiv:2212.03533, arXiv:2402.05672)
- stella_en_1.5B_v5 — https://huggingface.co/NovaSearch/stella_en_1.5B_v5
- nomic-embed-text-v1.5 / nomic-embed-code / nomic-embed-vision-v1.5 — https://huggingface.co/nomic-ai/nomic-embed-text-v1.5 ; https://huggingface.co/nomic-ai/nomic-embed-code ; https://huggingface.co/nomic-ai/nomic-embed-vision-v1.5
- gte-multilingual-base — https://huggingface.co/Alibaba-NLP/gte-multilingual-base (paper arXiv:2407.19669)
- jina-embeddings-v3 / jina-clip-v2 / jina-embeddings-v2-base-code — https://huggingface.co/jinaai/jina-embeddings-v3 ; https://huggingface.co/jinaai/jina-clip-v2 ; https://huggingface.co/jinaai/jina-embeddings-v2-base-code
- potion-code-16M (model2vec static) — https://huggingface.co/minishlab/potion-code-16M
- SigLIP 2 — https://huggingface.co/google/siglip2-so400m-patch14-384 (paper arXiv:2502.14786)
- OpenCLIP ViT-H/14 (LAION-2B) — https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K
- CLIP ViT-L/14 — https://huggingface.co/openai/clip-vit-large-patch14
- ColQwen2 / ColPali — https://huggingface.co/vidore/colqwen2-v1.0 (ColPali paper arXiv:2407.01449)

API model docs:

- OpenAI text-embedding-3-large — https://platform.openai.com/docs/guides/embeddings (3072 dims, MRL-shrinkable, 8191-token context)
- Voyage voyage-3-large / voyage-code-3 / voyage-multimodal-3(.5) — https://blog.voyageai.com/2025/01/07/voyage-3-large/ ; https://blog.voyageai.com/2024/12/04/voyage-code-3/ ; https://blog.voyageai.com/2024/11/12/voyage-multimodal-3/
- Cohere embed-v3 / embed-multilingual-v3 / embed-v4 — https://docs.cohere.com/docs/cohere-embed ; https://docs.cohere.com/changelog/embed-multimodal-v4

> Couldn't fully verify from a primary source at authoring time: exact, current
> MTEB/MMTEB/CoIR/ViDoRe point scores and the precise day-of-fetch #1 model
> (the official MTEB Space was still re-rendering on fetch). Reported standings
> draw on the live leaderboards plus secondary 2026 roundups and should be
> re-confirmed against the linked leaderboards before use.
