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
* **Survive a 429 storm on the first warm.** GitHub's hosted
  runners share egress IPs, so an unlucky cold-cache run can land
  on a 429 from the Hub's CDN.  This script wraps each download in
  an exponential-backoff retry loop that honours ``Retry-After``
  when the Hub provides one.  Worst-case wall time per model is
  ~8 minutes (5 attempts x ~30/60/90/120/180s) which fits inside
  every workflow's job timeout with room to spare.
* **Loud failure on bad config.** Permanent errors (404, auth,
  malformed repo id) exit non-zero so the warm step itself fails.
  Transient HTTP errors (429 / 5xx) that survive the full retry
  budget log loudly but exit 0 — the Hub's CDN refusing to serve
  this runner pool is an environment-level flake, not a config bug,
  and unit/smoke/fuzz tests mock model loads so a partially-cold
  cache doesn't break them.  Integration tests that actually touch
  the missing models will skip via their own
  ``model_loads_ok`` conftest fixtures instead of producing a
  confusing offline-mode error far from the warm step.

Usage
-----

  uv run python .github/scripts/warm_hf_cache.py

  # Or override the model list path (rare; useful for one-shot
  # bisects when investigating a single model's behaviour).
  uv run python .github/scripts/warm_hf_cache.py /path/to/list.txt
"""

from __future__ import annotations

import os
import random
import sys
import time
from collections.abc import Iterator
from pathlib import Path

_DEFAULT_MODEL_LIST = Path(".github/ci-models.txt")

# Per-model retry budget.  5 attempts with the schedule below yields a
# worst-case ~8-minute wall time before we give up — long enough to
# ride out the CDN's typical 429 cooldown, short enough that two
# models in series still fit inside the integration job's 30-minute
# timeout with margin.
_MAX_ATTEMPTS = 5
_BACKOFF_SECONDS: tuple[float, ...] = (30.0, 60.0, 90.0, 120.0, 180.0)
# Cap Retry-After honouring so a pathological header value can't
# strand the warm step longer than a backoff bucket.
_RETRY_AFTER_CAP_SECONDS = 300.0
# Transient HTTP status codes we consider worth retrying.  Everything
# else (auth, 404 on the repo, …) is a permanent error.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


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


def _is_transient(exc: BaseException) -> tuple[bool, int | None, str | None]:
    """Inspect *exc* (and its chained causes) for a transient HTTP failure.

    ``huggingface_hub`` wraps the actual ``HfHubHTTPError`` in a
    ``LocalEntryNotFoundError`` for snapshot_download's "couldn't find
    the revision" path, so we have to walk ``__cause__`` rather than
    just ``isinstance``-checking the top exception.

    Returns ``(is_transient, status_code, retry_after_header)``.  The
    ``retry_after_header`` is the raw string value (seconds or an
    HTTP-date) if the response carried one; the caller is responsible
    for interpreting it.
    """
    # Late import: keeps the malformed-list early-exit path off the
    # huggingface_hub import cost.
    from huggingface_hub.utils import HfHubHTTPError  # noqa: PLC0415

    cur: BaseException | None = exc
    while cur is not None:
        if isinstance(cur, HfHubHTTPError) and getattr(cur, "response", None) is not None:
            status = cur.response.status_code
            if status in _TRANSIENT_STATUS:
                retry_after = cur.response.headers.get("Retry-After")
                return True, status, retry_after
            return False, status, None
        cur = cur.__cause__
    return False, None, None


def _sleep_for_attempt(attempt_idx: int, retry_after: str | None) -> float:
    """Return the number of seconds to sleep before the next retry.

    Prefers the server's ``Retry-After`` (numeric form only — we
    don't try to parse the HTTP-date variant; in practice the Hub
    sends seconds) and falls back to the scheduled backoff bucket
    with a small jitter so two warmups racing on the same runner
    pool don't lock-step.
    """
    if retry_after is not None and retry_after.strip().isdigit():
        base = min(float(retry_after), _RETRY_AFTER_CAP_SECONDS)
    else:
        base = _BACKOFF_SECONDS[min(attempt_idx, len(_BACKOFF_SECONDS) - 1)]
    return base + random.uniform(0, base * 0.25)


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


def _warm_one_with_retry(repo_id: str) -> None:
    """Warm *repo_id*, retrying on transient HTTP failures.

    Permanent errors (auth, 404, malformed repo id) raise immediately.
    Transient errors (429 / 5xx, possibly wrapped by
    ``LocalEntryNotFoundError``) trigger up to ``_MAX_ATTEMPTS - 1``
    retries with exponential backoff + jitter, honouring
    ``Retry-After`` when the Hub provides one.
    """
    for attempt_idx in range(_MAX_ATTEMPTS):
        try:
            print(
                f"warm_hf_cache: snapshot_download {repo_id!r} "
                f"(attempt {attempt_idx + 1}/{_MAX_ATTEMPTS})",
                flush=True,
            )
            _warm_one(repo_id)
            return
        except Exception as exc:
            transient, status, retry_after = _is_transient(exc)
            if not transient or attempt_idx + 1 == _MAX_ATTEMPTS:
                raise
            sleep_s = _sleep_for_attempt(attempt_idx, retry_after)
            print(
                f"warm_hf_cache: {repo_id!r} got HTTP {status} "
                f"(attempt {attempt_idx + 1}/{_MAX_ATTEMPTS}); "
                f"retrying after {sleep_s:.1f}s "
                f"(Retry-After={retry_after!r})",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(sleep_s)


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
            _warm_one_with_retry(repo_id)
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
        # If EVERY failure is a transient HTTP issue (429 storm, 5xx) we've
        # already burned the full retry budget — the Hub's CDN simply isn't
        # serving this runner pool right now. Treat that as
        # environment-level flake and exit 0 so the rest of the workflow
        # can proceed: unit/fuzz/smoke tests mock the model loads, so a
        # cold cache is harmless for them; integration tests that actually
        # touch HF models will skip via their own ``model_loads_ok``
        # conftest fixtures (see ``tests/integration/conftest.py``).
        # Permanent failures (404, auth, malformed repo id) still exit
        # non-zero because those are config bugs the warm step must
        # surface loudly.
        permanent_failures = [
            (repo, exc) for repo, exc in failures if not _is_transient(exc)[0]
        ]
        if permanent_failures:
            perm_names = ", ".join(repo for repo, _ in permanent_failures)
            print(
                f"warm_hf_cache: {len(permanent_failures)} permanent failure(s): "
                f"{perm_names}; failing the warm step",
                file=sys.stderr,
            )
            return 1
        print(
            f"warm_hf_cache: all {len(failures)} failure(s) were transient HTTP errors "
            f"(429/5xx) — Hub CDN is rate-limiting this runner pool. Continuing with "
            f"a partially-warm cache; offline-mode tests that need a missing model "
            f"will surface a clearer error than the warm step would.",
            file=sys.stderr,
        )
        return 0
    print(
        f"warm_hf_cache: cached {len(repo_ids)} repo(s) into ~/.cache/huggingface",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
