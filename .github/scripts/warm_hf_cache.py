"""Pre-warm the HuggingFace Hub cache for CI.

Reads the model list at ``.github/ci-models.txt`` and downloads each
repo into ``~/.cache/huggingface/hub`` via
``huggingface_hub.snapshot_download``.  The companion ``setup-uv``
composite action gates this script on a cache miss; on a hit the
cache restore step has already populated everything and this script
is not invoked.

Goals
-----

* **Hermetic test runs.** After the first warm pass, CI test steps
  set ``HF_HUB_OFFLINE=1`` so they fail loudly on any model that
  isn't already cached — eliminating Hub 429 flakes from the
  network-dependent integration / multimodal suites.
* **Loud failure on bad config.** If a repo id in ``ci-models.txt``
  is missing or unreachable, this script exits non-zero so the
  warm step itself fails (rather than silently leaving the cache
  half-populated).

Usage
-----

  uv run python .github/scripts/warm_hf_cache.py

  # Or override the model list path (rare; useful for one-shot
  # bisects when investigating a single model's behaviour).
  uv run python .github/scripts/warm_hf_cache.py /path/to/list.txt
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

_DEFAULT_MODEL_LIST = Path(".github/ci-models.txt")


def _iter_repo_ids(path: Path) -> Iterator[str]:
    """Yield non-comment, non-blank ``owner/repo`` lines from *path*."""
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "/" not in line:
            print(
                f"warm_hf_cache: ignoring malformed entry (missing 'owner/repo' shape): {line!r}",
                file=sys.stderr,
            )
            continue
        yield line


def _warm_one(repo_id: str) -> None:
    """Download every file in *repo_id* into the HF cache.

    ``snapshot_download`` is the canonical way to populate the
    Hub cache — sentence-transformers, faster-whisper, and
    cross-encoder loaders all read from the same
    ``~/.cache/huggingface/hub`` tree, so the next call to
    ``SentenceTransformer(...)`` / ``WhisperModel(...)`` finds the
    snapshot locally and skips the network.
    """
    # Imported lazily so a malformed model-list (and the resulting
    # early sys.exit) doesn't pay the huggingface_hub import cost.
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    print(f"warm_hf_cache: snapshot_download {repo_id!r}", flush=True)
    t0 = time.monotonic()
    snapshot_download(
        repo_id=repo_id,
        # Skip ``.bin`` weights when a ``.safetensors`` sibling exists —
        # cheaper to cache + the same models load fine without the
        # legacy pickle format.
        ignore_patterns=["*.bin"],
    )
    elapsed = time.monotonic() - t0
    print(f"warm_hf_cache: {repo_id!r} ready in {elapsed:.1f}s", flush=True)


def main(argv: list[str]) -> int:
    model_list = Path(argv[1]) if len(argv) > 1 else _DEFAULT_MODEL_LIST
    if not model_list.exists():
        print(
            f"warm_hf_cache: model list {model_list} not found",
            file=sys.stderr,
        )
        return 2

    repo_ids = list(_iter_repo_ids(model_list))
    if not repo_ids:
        print(
            "warm_hf_cache: model list contains no repo ids; nothing to warm",
            file=sys.stderr,
        )
        return 0

    # If HF_HUB_OFFLINE was set ahead of warmup (mis-ordered workflow
    # step), every snapshot_download below would fail with a confusing
    # network-disabled error.  Unset it for the duration of the warm
    # so the test step can re-enable it afterwards.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)

    failures: list[tuple[str, BaseException]] = []
    for repo_id in repo_ids:
        try:
            _warm_one(repo_id)
        except Exception as exc:
            failures.append((repo_id, exc))
            print(
                f"warm_hf_cache: ERROR warming {repo_id!r}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if failures:
        names = ", ".join(repo for repo, _ in failures)
        print(
            f"warm_hf_cache: {len(failures)} repo(s) failed to warm: {names}",
            file=sys.stderr,
        )
        return 1
    print(
        f"warm_hf_cache: cached {len(repo_ids)} repo(s) into ~/.cache/huggingface",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
