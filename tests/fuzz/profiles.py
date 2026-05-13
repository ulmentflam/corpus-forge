"""Hypothesis profile registry for corpus-forge.

Three named profiles tier the fuzz suite by rigor:

- ``dev`` — fast local feedback. Few examples, short deadline.
- ``ci`` — PR gate. Richer examples, kept under a sensible deadline so the
  CI matrix doesn't blow past the 10-minute target.
- ``nightly`` — deep search. Long deadline; many examples; tolerates
  longer-running shrinking.

Resolution rule (applied in ``tests/conftest.py``): the active profile is
read from the ``HYPOTHESIS_PROFILE`` environment variable, defaulting to
``dev`` when unset. ``CI=true`` is intentionally NOT auto-promoted to
``ci`` here — the GitHub Actions workflow sets ``HYPOTHESIS_PROFILE=ci``
explicitly, which keeps local-shell behavior deterministic and avoids
surprising contributors running ``make test-fuzz`` in a Docker container
that happens to ship ``CI=true``.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings


def register_hypothesis_profiles() -> None:
    """Register the dev / ci / nightly hypothesis profiles.

    Idempotent: hypothesis allows repeat ``register_profile`` calls and
    simply overwrites the prior settings, so calling this multiple times
    (e.g. once at conftest import + once per test that pulls a fresh
    profile) is safe.
    """
    # ── dev: fast local feedback ─────────────────────────────────────────
    settings.register_profile(
        "dev",
        max_examples=25,
        deadline=400,  # ms per example
        derandomize=False,
        print_blob=True,
        suppress_health_check=(HealthCheck.too_slow,),
    )

    # ── ci: PR-gate matrix runs (3 OS × 3 Py later; single-OS in CI-1) ──
    settings.register_profile(
        "ci",
        max_examples=100,
        deadline=800,
        derandomize=False,
        print_blob=True,
        suppress_health_check=(HealthCheck.too_slow,),
    )

    # ── nightly: long, deep search; cron-only ────────────────────────────
    settings.register_profile(
        "nightly",
        max_examples=500,
        deadline=2_000,
        derandomize=False,
        print_blob=True,
        suppress_health_check=(HealthCheck.too_slow,),
    )
