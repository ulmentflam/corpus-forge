# RFC: Fleet 7 — installer picks the right `llama-cpp-python` build (CUDA / Metal / CPU)

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-08
**Depends on**: none (install-side; composes with rfc-fleet-3's `--join` and rfc-fleet-5's drain loop)

## Context

`[llama-cpp]` pulls the in-process `llama-cpp-python` embedder
(pyproject.toml:202-221, MIT). The wheel you get from a plain
`pip`/`uv` install is **CPU-only** unless it was built — or fetched —
against an accelerator backend. pyproject already documents the manual
escape hatch for macOS Metal
(`CMAKE_ARGS="-DGGML_METAL=on" pip install …`, pyproject.toml:213-215),
but the **installers do not act on any of it**: both `install.sh`
(`:398`) and `install.ps1` (`:291`) just run
`uv tool install '<...>[extras]'` and take whatever wheel resolves.

Consequences, worst on Windows (where the operator just joined a box to
the fleet):

- A Windows host with a CUDA GPU gets the **CPU-only**
  `llama-cpp-python` — in-process embedding runs on the CPU, ignoring
  the very GPU the box was added to the fleet for. Combined with
  fleet-5's drain loop, the host "drains" the backlog at CPU speed
  while its GPU idles — silent, no error, just slow.
- Or, with no prebuilt wheel matching the platform, `uv` falls back to
  a **source build** that needs a C/C++ toolchain + CUDA toolkit the
  box may not have → a confusing compiler-error wall during install.

Meanwhile `corpus_forge/acceleration.py` *already* detects exactly what
we need at install time: `detect_accelerator()` probes `nvidia-smi`
(CUDA + device name + VRAM, `:109 _detect_cuda`), MPS (`:152
_mps_available`), else CPU. The installer just never consults it to
choose a wheel.

`llama-cpp-python` publishes prebuilt accelerated wheels behind
per-backend extra-index URLs (CPU, `cuXXX` CUDA variants, Metal). The
fix is to **detect, then install from the matching index** — with a
clean CPU fallback and an explicit operator override.

## Goals

- A fresh install on a CUDA box (Windows especially) ends up with a
  **CUDA-enabled** `llama-cpp-python` by default — the in-process
  embedder offloads to the GPU without the operator hand-editing
  `CMAKE_ARGS` or extra-index URLs.
- macOS / Apple Silicon gets a **Metal-enabled** build by default;
  everything else (no detectable accelerator) gets the **CPU** wheel,
  which always resolves — never a surprise source build.
- The choice is **detected, announced, and overridable**: the
  installer prints which backend it picked and why, and accepts an
  explicit flag / env var to force CPU / a specific CUDA variant /
  skip llama-cpp entirely.
- Robust degradation: if the accelerated wheel can't be fetched
  (offline index, unsupported CUDA version, arch with no prebuilt
  wheel), fall back to the CPU wheel with a clear warning — install
  never hard-fails on the accelerator step.
- `doctor` reports the truth: detected accelerator **vs** the
  llama-cpp build actually installed, and WARNs on the expensive
  mismatch (CUDA GPU present but CPU-only `llama-cpp-python` loaded).

## Non-goals

- **No source compilation by default.** We prefer prebuilt wheels from
  the published indexes; a `CMAKE_ARGS` source build stays an
  *explicit opt-in* flag, not the happy path (it needs a toolchain we
  can't assume).
- **No CUDA toolkit / driver installation.** We detect what's present
  (`nvidia-smi`) and match a wheel to it; installing drivers is out of
  scope (doctor can hint if `nvidia-smi` is missing but a GPU seems
  present).
- **No change to the `[llama-cpp]` extra's dependency set** in
  pyproject — this is *which wheel/index* the installer selects, not a
  new dependency. Ollama / OpenAI-shape embedders are unaffected
  (they're out-of-process and don't care about the wheel backend).
- No Linux-specific ROCm/Intel backends in v1 — CUDA, Metal, CPU only.
  The selection logic reserves room for more lanes later.

## Approach

### Detect → select index → install

Both installers gain a shared step (logic mirrored in `.sh` and
`.ps1`), reusing the *same* signals `acceleration.py` uses so the
install-time choice and the runtime probe agree:

1. **Detect** the accelerator the way `acceleration._detect_cuda` /
   `_mps_available` do — `nvidia-smi` for CUDA (+ driver/CUDA version
   to pick the `cuXXX` variant), platform check for Apple Silicon
   Metal, else CPU. The installer is shell/PowerShell, so it
   re-implements the *same probe* (nvidia-smi presence + version),
   not a Python import (corpus-forge isn't installed yet).
2. **Select** the matching `llama-cpp-python` install source:
   - CUDA → the `cuXXX` prebuilt-wheel extra-index URL closest to the
     detected CUDA version (with a documented fallback chain).
   - Apple Silicon → Metal build (prebuilt wheel, or the documented
     `CMAKE_ARGS="-DGGML_METAL=on"` opt-in if no prebuilt matches).
   - else → CPU wheel.
3. **Install** `corpus-forge[...,llama-cpp]` pointing at that index;
   on fetch failure, retry against the CPU index and WARN.
4. **Announce**: print "Detected NVIDIA <name> (CUDA 12.x) → installing
   CUDA-enabled llama-cpp-python" so the operator sees the decision.

### Override surface

- `install.sh`: `--llama-backend {auto|cuda|cuda121|metal|cpu|none}`
  (default `auto`) and env `CF_LLAMA_BACKEND`. `none` skips the
  `[llama-cpp]` extra entirely.
- `install.ps1`: `-LlamaBackend <...>` parameter and
  `$env:CF_LLAMA_BACKEND`, chained paste-safe per the existing
  PowerShell one-liner convention (CLAUDE.md install section — no
  `iwr | iex`).
- These thread through the `--join` one-liner too (fleet-3), so a
  GPU box joined to the fleet gets CUDA llama-cpp in the same single
  command.

### Doctor reconciliation

`corpus-forge doctor` already surfaces the detected accelerator
(`acceleration.py` is its source). Add: read the installed
`llama-cpp-python`'s actual backend (it exposes build info / supports a
GPU-offload probe) and compare to `detect_accelerator()`:

- CUDA detected **but** CPU-only llama-cpp loaded → **WARN** with the
  reinstall fix (`--llama-backend cuda`), because this is the silent
  "draining on CPU" trap.
- Backends agree → OK line.

### Docs

- pyproject's `[llama-cpp]` comment (`:202-221`) and the README
  `[llama-cpp]` row: document that the **installer auto-selects** the
  accelerated wheel, and the `--llama-backend` / `-LlamaBackend`
  overrides. Keep the manual `CMAKE_ARGS` note as the source-build
  escape hatch.
- CLAUDE.md install section + "Add a second machine": mention that a
  GPU joiner gets CUDA llama-cpp automatically, and the override flag.
- Troubleshooting row: "in-process embedding is slow / GPU idle on a
  CUDA box" → `doctor` (mismatch WARN) → reinstall with
  `--llama-backend cuda`.

**Coverage note:** ≥ 89 % line coverage (current `make test-unit`
floor — see Makefile) on any new Python (the doctor reconciliation +
any backend-detection helper). Installer-script logic is covered by the
existing install smoke matrix.

## Tasks

- [x] Shared install-time accelerator probe in `install.sh`
      (`nvidia-smi` + CUDA version; Apple Silicon; CPU fallback) →
      selects the matching `llama-cpp-python` index; announces the
      choice; CPU fallback + WARN on fetch failure.
- [x] Same logic in `install.ps1`, paste-safe single-line form,
      Windows-first (the box this RFC came from).
- [x] `--llama-backend {auto|cuda|cudaXXX|metal|cpu|none}` +
      `CF_LLAMA_BACKEND` (sh) and `-LlamaBackend` + `$env:CF_LLAMA_BACKEND`
      (ps1); thread through the fleet-3 `--join` one-liner.
- [ ] `doctor`: read installed `llama-cpp-python` backend, compare to
      `detect_accelerator()`, WARN on CUDA-present-but-CPU-wheel.
- [ ] Tests: doctor mismatch WARN fires on a stubbed
      CUDA-detected / CPU-wheel state and stays quiet when they agree;
      backend-selection helper maps detected (kind, cuda_version) →
      expected index/flag. *(backend-selection-helper half done in
      `tests/scripts/test_install_llama_backend.py`; doctor-mismatch half
      pending the item above.)*
- [ ] Install smoke-matrix cases: CUDA-detected path selects the CUDA
      index; no-accelerator path selects CPU and never source-builds;
      `--llama-backend cpu` / `none` honored.
- [x] pyproject `[llama-cpp]` comment, README `[llama-cpp]` row,
      CLAUDE.md install + second-machine + troubleshooting updates.

## Verification

- **Windows repro fixed:** on a CUDA Windows box, the one-liner installs
  a CUDA-enabled `llama-cpp-python`; `doctor` shows
  detected=cuda / llama-cpp=cuda (no mismatch WARN); an in-process
  embed run offloads to the GPU (rate >> CPU baseline, visible in
  fleet-1 telemetry).
- **No surprise builds:** on a box with no toolchain and no
  accelerator, install completes with the CPU wheel and zero compiler
  output.
- **Override honored:** `--llama-backend cpu` on a CUDA box installs
  the CPU wheel and `doctor` WARNs (operator chose it, so a WARN — not
  an error — is correct); `--llama-backend none` omits the extra.
- **Graceful fallback:** with the accelerated index unreachable,
  install falls back to CPU + WARN, never hard-fails.

## References

- `install.ps1` (`:291` install step), `install.sh` (`:398` install
  step) — where `uv tool install` runs today with no backend choice.
- `corpus_forge/acceleration.py` — `detect_accelerator`,
  `_detect_cuda` (`:109`, nvidia-smi + VRAM), `_mps_available`
  (`:152`); the runtime probe the installer must agree with and doctor
  reconciles against.
- `pyproject.toml:202-221` — `[llama-cpp]` extra + the existing manual
  `CMAKE_ARGS` Metal note this RFC automates.
- `.planning/rfcs/rfc-fleet-3-federated-config-and-setup.md` — the
  `--join` one-liner the `--llama-backend` flag threads through.
- `.planning/rfcs/rfc-fleet-5-service-embed-drain.md` — the drain loop
  that would otherwise silently run on CPU on a mis-installed GPU box.
- CLAUDE.md §"1. Install" + §"Add a second machine".
