"""Integration test — RFC fleet-6 item 8: two-host alias telemetry collapse.

The acceptance bar of rfc-fleet-6 item 3 (shipped): the fleet telemetry
``model_key`` is now :func:`corpus_forge.embedders.identity.canonical_model_key`
rather than the raw ``(provider, model_id)`` pair. So when the *same*
underlying model is served under two different provider names — each host
declaring the other's name as a :class:`~corpus_forge.config.ModelAlias` —
both hosts' benchmarks must accrue under **one** canonical
``corpus.models`` row, not two split lanes.

This test proves that end-to-end against a testcontainers Postgres:

1. Two :class:`EmbedderConfig`s describe the same model under swapped
   ``(provider, model_id)`` pairs, each aliasing the other. Both resolve
   to the same canonical key.
2. Each host seeds ``corpus.models`` (UPSERT dedupes on the shared
   canonical ``model_key`` → one row) and inserts a benchmark row keyed
   on that canonical key.
3. :meth:`PostgresBackend.list_models_with_latest_benchmark` returns
   exactly ONE distinct ``model_key`` for this model — the two hosts
   cooperate on one lane instead of fragmenting into two.

Gated on ``requires_docker``; uses the session-scoped ``pg_dsn`` fixture
from the root conftest.
"""

from __future__ import annotations

import sys

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.config import EmbedderConfig, ModelAlias
from corpus_forge.embedders.identity import canonical_model_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_docker,
    # testcontainers needs a Linux Postgres image; standard Windows GH runners
    # run Windows containers and can't run it. The `requires_docker` skip
    # spuriously passes when the Docker CLI is present but the daemon can't run
    # Linux containers, so the test hung ~35min on the windows-2022 cell. Skip
    # on win32, mirroring tests/integration/test_scan_parity.py.
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="testcontainers Linux Postgres unavailable on Windows GH runners",
    ),
]

# The same nomic-embed-code model served under two provider names. The
# ``provider`` field is validated against the EmbedderConfig regex
# (sentence_transformers|openai|model2vec|llama-cpp), so we use ``openai``
# (an OpenAI-compatible local shim) and ``llama-cpp`` (in-process) rather
# than the RFC's illustrative "ollama".
_OPENAI_PAIR = ("openai", "text-embedding-nomic-embed-code")
_LLAMA_PAIR = ("llama-cpp", "manutic/nomic-embed-code")
_DIMENSION = 768


def _host_a_config() -> EmbedderConfig:
    """Host A serves the OpenAI-compatible name, aliasing the llama-cpp one."""
    return EmbedderConfig(
        name="nomic-code",
        provider=_OPENAI_PAIR[0],
        model_id=_OPENAI_PAIR[1],
        dimension=_DIMENSION,
        model_aliases=[ModelAlias(provider=_LLAMA_PAIR[0], model_id=_LLAMA_PAIR[1])],
    )


def _host_b_config() -> EmbedderConfig:
    """Host B serves the llama-cpp name, aliasing the OpenAI-compatible one."""
    return EmbedderConfig(
        name="nomic-code",
        provider=_LLAMA_PAIR[0],
        model_id=_LLAMA_PAIR[1],
        dimension=_DIMENSION,
        model_aliases=[ModelAlias(provider=_OPENAI_PAIR[0], model_id=_OPENAI_PAIR[1])],
    )


def _seed_model(backend: PostgresBackend, model_key: str) -> None:
    """UPSERT the canonical ``corpus.models`` row (FK target for benchmarks).

    ``upsert_models`` dedupes on ``model_key``, so calling it once per host
    with the *same* canonical key leaves exactly one row.
    """
    provider, model_id = model_key.split(":", 1)
    backend.upsert_models(
        [
            {
                "model_key": model_key,
                "kind": "embedder",
                "provider": provider,
                "model_id": model_id,
                "dimension": _DIMENSION,
            }
        ]
    )


def test_two_host_alias_collapses_to_one_canonical_model(pg_dsn: str) -> None:
    cfg_a = _host_a_config()
    cfg_b = _host_b_config()

    # (1) Both swapped configs resolve to the SAME canonical key — the
    # lexicographically smallest pair, ``llama-cpp:manutic/nomic-embed-code``.
    key_a = canonical_model_key(cfg_a)
    key_b = canonical_model_key(cfg_b)
    assert key_a == key_b, f"alias configs disagree on canonical key: {key_a!r} vs {key_b!r}"
    canonical = key_a
    assert canonical == "llama-cpp:manutic/nomic-embed-code"
    # Sanity: the raw split names WOULD have been two distinct keys.
    raw_a = f"{cfg_a.provider}:{cfg_a.model_id}"
    raw_b = f"{cfg_b.provider}:{cfg_b.model_id}"
    assert raw_a != raw_b

    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()

    backend.upsert_host(host_id="host-a", hostname="alpha", os="Linux", accelerator=None)
    backend.upsert_host(host_id="host-b", hostname="bravo", os="macOS", accelerator=None)

    # (2) Each host seeds the canonical model row (UPSERT → one row) and
    # (3) inserts a benchmark keyed on the canonical key.
    for host_id, cfg, rate in (("host-a", cfg_a, 120.0), ("host-b", cfg_b, 350.0)):
        _seed_model(backend, canonical_model_key(cfg))
        backend.insert_model_benchmark(
            host_id=host_id,
            model_key=canonical_model_key(cfg),
            source="embed-run",
            transport="local",
            device="cpu",
            batch_size=32,
            sample_chunks=64,
            chunks_per_s=rate,
            tokens_per_s=None,
            latency_p50_ms=None,
            latency_p95_ms=None,
        )

    # (4) Exactly ONE distinct model_key for this model — collapsed lane.
    rows = backend.list_models_with_latest_benchmark()
    model_keys = {r["model_key"] for r in rows}
    assert model_keys == {canonical}, (
        f"expected one collapsed lane {canonical!r}, got {sorted(model_keys)!r}"
    )

    # Cooperative one-lane: both hosts have a benchmark row under that key.
    bench_rows = [r for r in rows if r["model_key"] == canonical and r["host_id"] is not None]
    host_ids = {r["host_id"] for r in bench_rows}
    assert host_ids == {"host-a", "host-b"}, (
        f"both hosts should accrue under the canonical key; got {sorted(host_ids)!r}"
    )

    backend.close()
