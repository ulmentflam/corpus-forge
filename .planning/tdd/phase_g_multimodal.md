# Phase G — Multi-Modal Embeddings + Whisper Transcription

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

## Status

**Phase F** complete (TBD). **Phase G** (this file): **pending kickoff**.

## Goal

Lift the corpus to two new modalities:

1. **Audio + video** — transcribed to Markdown via Whisper, fed through the existing extractor pipeline so the rest of the system sees plain text.
2. **Images** — embedded in a shared text-image vector space so retrieval can return image results from a text query (and vice versa).

Both built per the Phase E principle: **every model client supports a configurable local-or-remote URL**.

## Phase split (P0/P1)

### P0 — Whisper transcription

**WhisperBackend protocol** (`corpus_forge/whisper/base.py`):
```python
class WhisperBackend(Protocol):
    name: str
    def transcribe(self, audio: bytes, *, language: str | None = None) -> str: ...
    def warmup(self) -> None: ...
```

**Backends**:
- `corpus_forge/whisper/local.py::LocalWhisper` — via `faster-whisper` (~70 MB for `small`, ~250 MB for `medium`). Default model: `small` (good throughput on M-series). Configurable.
- `corpus_forge/whisper/remote.py::RemoteWhisper` — speaks the OpenAI Whisper API shape: `POST {base_url}/audio/transcriptions` with `model`, `file`. Works against OpenAI, Groq (free tier, fast), Replicate, self-hosted whisper.cpp via HTTP.

**Audio + video extractors**:
- `corpus_forge/extractors/audio.py::AudioExtractor` — extensions `.mp3 .wav .m4a .ogg .flac`. Reads bytes; pipes through active Whisper backend; returns Markdown transcript with timestamps.
- `corpus_forge/extractors/video.py::VideoExtractor` — extensions `.mp4 .mov .webm .mkv`. Uses `ffmpeg` (system dep) or `imageio-ffmpeg` (Python wheel) to extract audio track; then routes through Whisper.

**Config** (`corpus_forge/config.py`):
```toml
[whisper]
backend = "local"               # "local" | "remote" | "none"
model = "small"                 # local: tiny|base|small|medium|large; remote: provider-specific
local_compute_type = "auto"     # auto|float16|int8 for faster-whisper
remote_base_url = "https://api.openai.com/v1"   # OpenAI default; override for Groq/etc.
remote_api_key_env = "OPENAI_API_KEY"
timeout_s = 300.0
language = ""                   # blank = auto-detect
```

### P1 — Multi-modal embeddings

**MultiModalEmbedder protocol** (`corpus_forge/embedders/multimodal.py`):
```python
class MultiModalEmbedder(Protocol):
    name: str
    dimension: int
    def encode_text(self, texts: list[str]) -> list[list[float]]: ...
    def encode_image(self, images: list[bytes]) -> list[list[float]]: ...
    def warmup(self) -> None: ...
```

**Backends**:
- `corpus_forge/embedders/clip_local.py::ClipLocalEmbedder` — `sentence-transformers` with `clip-ViT-B-32` or `jina-clip-v2` (multilingual + better accuracy). Local model load; MPS/CPU device detection.
- `corpus_forge/embedders/clip_remote.py::ClipRemoteEmbedder` — OpenAI-compatible `/v1/embeddings` endpoint that accepts both text and image inputs (e.g. Voyage AI `voyage-multimodal-3`, Cohere `embed-v3-multimodal`).

**Schema**: new `image_embeddings_<embedder>` tables mirror existing `embeddings_<embedder>` shape but key on `chunks.id` for image-extractor chunks (`format=image` label from Phase D Wave 5/6). Postgres + SQLite migrations.

**ImageExtractor integration**: when `[multimodal]` is enabled, ImageExtractor stops requiring a VLM for OCR and just produces a thin RawDocument with the image bytes preserved in metadata; the MultiModalEmbedder consumes the bytes for vector encoding. (VLM OCR path stays available as a separate config — useful for screenshots with text.)

## Task table

### P0 — Whisper

| id | title | depends_on | surface | risk | status |
|----|-------|------------|---------|------|--------|
| G-01 | `WhisperBackend` protocol + registry | — | `corpus_forge/whisper/{__init__,base,registry}.py` (new), `tests/unit/test_whisper_registry.py` | low | pending |
| G-02 | `LocalWhisper` (faster-whisper) | G-01 | `corpus_forge/whisper/local.py`, `tests/unit/test_whisper_local.py` (mocked model) | med | pending |
| G-03 | `RemoteWhisper` (OpenAI-compatible HTTP) | G-01 | `corpus_forge/whisper/remote.py`, `tests/unit/test_whisper_remote.py` (mocked HTTP) | med | pending |
| G-04 | `WhisperConfig` pydantic + `Config.whisper` | — | `corpus_forge/config.py`, `tests/unit/test_config_whisper.py` | low | pending |
| G-05 | `AudioExtractor` | G-02 or G-03 | `corpus_forge/extractors/audio.py`, `tests/unit/test_extractor_audio.py` | med | pending |
| G-06 | `VideoExtractor` (ffmpeg) | G-05 | `corpus_forge/extractors/video.py`, `tests/unit/test_extractor_video.py`, `pyproject.toml` (`imageio-ffmpeg`) | med | pending |
| G-07 | `config.example.toml` `[whisper]` rich-docs block | G-04 | `config.example.toml` | low | pending |
| G-08 | Live e2e (requires_whisper_local marker) | G-05, G-06 | `tests/integration/test_whisper_local_e2e.py`, `tests/integration/conftest.py` (probe) | med | pending |
| G-09 | **P0 gate** — `make ci` green | G-08 | — | gate | pending |

### P1 — Multi-modal embeddings

| id | title | depends_on | surface | risk | status |
|----|-------|------------|---------|------|--------|
| G-10 | `MultiModalEmbedder` protocol | G-09 | `corpus_forge/embedders/multimodal.py`, `tests/unit/test_multimodal_protocol.py` | low | pending |
| G-11 | `ClipLocalEmbedder` (sentence-transformers) | G-10 | `corpus_forge/embedders/clip_local.py`, `tests/unit/test_clip_local.py` | med | pending |
| G-12 | `ClipRemoteEmbedder` (OpenAI-compat HTTP) | G-10 | `corpus_forge/embedders/clip_remote.py`, `tests/unit/test_clip_remote.py` (mocked HTTP) | med | pending |
| G-13 | `image_embeddings_<embedder>` schema migration | — | `corpus_forge/alembic/versions/0011_image_embeddings.py`, `tests/integration/test_migrate_image_embeddings.py` | low | pending |
| G-14 | Backend `write_image_embeddings` + `image_chunks_missing_embedding` helpers | G-13 | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/sqlite.py`, `tests/unit/test_backend_image_embeddings.py` | med | pending |
| G-15 | `corpus-forge embed --image` integration | G-11, G-14 | `corpus_forge/embed.py`, `corpus_forge/cli.py` | med | pending |
| G-16 | Live e2e | G-15 | `tests/integration/test_multimodal_embed_e2e.py` (`requires_clip_local` marker) | med | pending |
| G-17 | **P1 gate** — `make ci` green + manual cross-modal retrieval smoke | G-16 | — | gate | pending |

## Local-or-remote requirement

Per `project_model_local_or_remote.md`:
- Whisper: `[whisper] backend = "local" | "remote"` with explicit URL config on remote path
- Multi-modal: ship BOTH local (sentence-transformers) AND remote (OpenAI-compat URL) backends
- `config.example.toml` shows both backends with rich comments for each model URL

## Definition of Done

**P0 (G-09)** — Audio + video files in a `FilesystemSource` tree get transcribed to Markdown and ingested as documents with `format=audio` / `format=video` labels. `make ci` exit 0. One live Whisper test passes against `faster-whisper` (`requires_whisper_local` marker — skip when unavailable).

**P1 (G-17)** — Image fixtures from Phase D Wave 6 (`tests/fixtures/multi_format_corpus/images/`) get embeddings stored in `image_embeddings_<embedder>`. `corpus-forge search "screenshot with code"` returns the screenshot image fixture in top-3 results. `make ci` exit 0.

## Out of scope (P2)

- Speaker diarization
- Real-time streaming transcription
- Video frame-level embedding (only audio track for Phase G)
- Image OCR via the multi-modal embedder (Phase D VLM path remains the OCR escape hatch)
