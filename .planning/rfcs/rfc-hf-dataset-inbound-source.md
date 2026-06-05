# RFC: Inbound HuggingFace Datasets source

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P3
**Depends on**: `rfc-corpus-growth-controls.md` (per-source `max_rows`
cap is the safety belt that keeps this from swamping the corpus)

## Context

`corpus_forge/exports/huggingface.py` pushes corpus-forge data *to*
HF Hub. The reverse direction — pulling a public dataset *into*
corpus-forge — doesn't exist. Yet that's exactly what the user
asked for: "combining samples from other corpuses." Use cases:

- Pull `HuggingFaceH4/ultrachat_200k` to seed a chat dataset with
  generic high-quality assistant turns.
- Pull `openai/gsm8k` to add math reasoning examples.
- Pull a domain-specific code corpus (e.g.,
  `bigcode/the-stack-smol`).

The corresponding ingest path needs to be:

- **Safe by default**: hard row-count cap per import (we don't want a
  user accidentally pulling 100M rows).
- **Configurable**: column mapping (which column is the message,
  which is the role, etc.) varies between datasets.
- **Provenance-preserving**: every imported row carries
  `hf_dataset_id`, `hf_split`, `hf_row_idx` in metadata so we can
  always trace back to source.

## Goals

- A new `hf_dataset` source plugin
  (`corpus_forge/sources/hf_dataset.py`) reachable from config.
- Column-mapping config so the user names which column holds
  messages / role / content / score.
- Per-import row-budget cap (default 10 000; configurable in source
  block).
- Streaming mode so very large datasets don't have to be downloaded
  in full.
- Wires into `_instantiate_source` in `corpus_forge/ingest.py` using
  the exact pattern PR #29 added for the other chat sources.

## Non-goals

- No write-back to HF Hub from this source (that's the existing
  export path).
- No automatic license check. The user picks the dataset; license
  responsibility is theirs. Document this clearly.
- No fine-grained authentication. Use the HF token from env
  (`HF_TOKEN`) or `huggingface_hub` cached login, same as the
  existing export path.

## Approach

### Source plugin

`corpus_forge/sources/hf_dataset.py`:

```python
class HFDatasetSource(WatchedSource):
    name = "hf_dataset"
    dataset_kind = "chat" | "text"   # configured
    _session_link_client: str | None = None  # no live link

    def __init__(
        self,
        dataset_id: str,
        split: str = "train",
        column_map: dict[str, str] | None = None,
        max_rows: int = 10_000,
        streaming: bool = False,
        **kwargs,
    ): ...

    def discover(self) -> Iterator[Path]:
        # Datasets aren't files; yield a sentinel marker that parse() ignores.
        yield Path(f"hf://{dataset_id}/{split}")

    def parse(self, _sentinel) -> Iterator[RawConversation | RawDocument]:
        # Stream rows; apply column_map; yield up to max_rows.
        ...
```

Source URI scheme: `hf-dataset://<dataset_id>/<split>/<row_idx>`.

### Config

Extend `DatasetSourceConfig` in `corpus_forge/config.py`:

- `dataset_id: str | None`
- `split: str | None`
- `column_map: dict[str, str] | None`
- `max_rows: int | None` (caps per-import; the
  `rfc-corpus-growth-controls.md` cap is dataset-wide)
- `streaming: bool = False`

### Wiring

Add an `elif source_config.plugin == "hf_dataset"` branch to
`corpus_forge/ingest.py::_instantiate_source`, following the exact
shape PR #29 used for the other chat sources (empty/whitespace
guard on `dataset_id`, ValueError with field name on miss).

Add `hf-dataset://` to `_SOURCE_URI_TO_CLIENT` (mapped to
`"hf-dataset"`) for feedback-session client classification.

### Example column maps

Ship a small registry of preset column maps for common datasets in
`corpus_forge/sources/hf_dataset_presets.py`:

- `HuggingFaceH4/ultrachat_200k` → `{messages: "messages",
  role: "role", content: "content"}`
- `openai/gsm8k` → text mode, prompt+answer concatenation
- `bigcode/the-stack-smol` → text mode, code body in `content` col

User can override per-source. Presets are *hints*, not required.

### Sampling strategy

When the source dataset is larger than `max_rows`:

- Default: take the first `max_rows` in declared order (cheapest).
- Optional `--sample uniform` or `--sample stratified=<col>` for
  better distribution (post-MVP).

## Tasks

- [x] `corpus_forge/sources/hf_dataset.py`: `HFDatasetSource`. — local proposal (branch `nightly/hf-dataset-source-195137Z`, commit `a58e1b6`)
- [x] `corpus_forge/sources/hf_dataset_presets.py`: — same local proposal as above; a small dict of
      known dataset → column-map presets.
- [x] Extend `corpus_forge/config.py::DatasetSourceConfig` with the
      five new fields. — local proposal (branch `nightly/hf-config-200028Z`, commit `646e134`; 4 new fields — `max_rows` already shipped in PR #42)
- [x] Wire into `corpus_forge/ingest.py::_instantiate_source` (same
      pattern as the chat sources in PR #29). — local proposal (branch `nightly/hf-wiring-200441Z`, commit `d3b99a1`)
- [x] Add `hf-dataset://` to `_SOURCE_URI_TO_CLIENT`. — same local proposal as above (task 0013).
- [x] Config example block in `config.example.toml`. — local proposal (branch `nightly/hf-example-200853Z`, commit `c82acd2`)
- [x] Tests:
  - [x] `tests/unit/test_source_hf_dataset.py` — task 0011 local proposal (named `test_hf_dataset_source.py`, 18 tests covering load_dataset stub, column-map, max_rows, streaming branches)
  - [x] `tests/unit/test_hf_dataset_presets.py` — task 0011 local proposal (preset registry tests bundled into the same file: 12 tests of source + 6 of presets)
  - [x] `tests/unit/test_ingest_chat_source_wiring.py` — task 0013 local proposal (new file `test_ingest_instantiate_hf.py`, 6 tests for the dispatch branch)
  - [ ] `tests/integration/test_hf_dataset_ingest_e2e.py` — (Deferred: requires network access; gated by `pytest.mark.requires_network`. Belongs in a separate PR once the stack merges.)
- [x] CHANGELOG entry. Call out the license-responsibility note. — bullets in each task's local proposal (0011/0012/0013/0014). License note: HF dataset licenses vary; users are responsible for compliance with each dataset's terms.

## Verification

- `corpus-forge ingest --once` against a `hf_dataset` source with
  `max_rows=50` produces exactly 50 rows, no more.
- Every ingested chunk carries `hf_dataset_id` / `hf_split` /
  `hf_row_idx` in metadata — a SQL query confirms.
- Setting `dataset_id = ""` (blank) raises the same `ValueError`
  shape PR #29 introduced for the other chat sources (consistent UX).
- Streaming mode does not download the full dataset:
  `du -sh ~/.cache/huggingface/` stays below the dataset's full size
  after a `streaming=True` import.

## References

- HF outbound path: `corpus_forge/exports/huggingface.py`.
- Source plugin base: `corpus_forge/sources/base.py::WatchedSource`.
- Wiring pattern (PR #29): `corpus_forge/ingest.py::_instantiate_source`
  branches for `gemini_cli`, `codex_cli`, etc.
- Source-URI client table: `corpus_forge/ingest.py::_SOURCE_URI_TO_CLIENT`.
- Existing chat-source wiring tests:
  `tests/unit/test_ingest_chat_source_wiring.py`.
- `[hf]` optional dep: `pyproject.toml` (`datasets>=2.20`).
