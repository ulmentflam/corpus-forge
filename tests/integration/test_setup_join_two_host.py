"""Integration test — RFC fleet-3 item 5 join flow end-to-end.

The RFC's acceptance shape: publish from host A's config fixture, then
``setup --join`` as host B against the *same* testcontainers Postgres,
and assert that

* B's rendered ``config.toml`` loads as a :class:`Config`,
* B's shared scope equals A's published body
  (``shared_scope_dict(B) == published_body``),
* B's ``backend.dsn`` is the join DSN,
* B's host row was registered in ``corpus.hosts``, and
* B's local federation-state file records the published version.

Host A publishes via the real backend ``put_shared_config`` seam (the
``config publish`` verb's storage call); host B runs the genuine
:func:`corpus_forge.setup.run_join` (direct call, not a subprocess) so
the connect → verify-schema → register → render → record path runs for
real against the live schema the primary migrated.

Gated on ``requires_docker``; uses the function-scoped ``pg_dsn``
fixture (clean ``corpus`` schema per test).
"""

from __future__ import annotations

import io
import tomllib

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.config import Config
from corpus_forge.config_scope import shared_scope_dict
from corpus_forge.setup import run_join

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]


# Host A's config fixture — a fully-populated single-machine config whose
# shared scope (datasets sans sources, embedders, retrieval, model
# choices) is the body host B should converge on.
_HOST_A_CONFIG = """\
[backend]
kind = "postgres"
dsn  = "postgresql://host-a:5432/corpus_forge"

[daemon]
host_id = "host-a"

[[datasets]]
name = "notes"
kind = "text"
sources = [{plugin = "filesystem", root = "/host-a/notes", chunker = "markdown"}]

[[datasets]]
name = "chats"
kind = "chat"
sources = [{plugin = "filesystem", root = "/host-a/chats", chunker = "conversation"}]

[[embedders]]
name      = "nomic"
provider  = "sentence_transformers"
model_id  = "nomic-ai/nomic-embed-text-v1.5"
dimension = 768
normalize = true
distance  = "cosine"
active    = true

[retrieval]
alpha = 0.4
default_k = 12

[classifier]
chain = ["rule"]
"""


def _migrate_schema(dsn: str) -> None:
    """Primary-host migrate: create the corpus schema the join verifies."""
    backend = PostgresBackend(dsn=dsn, schema="corpus")
    try:
        backend.migrate()
    finally:
        backend.close()


def test_join_converges_on_published_shared_scope(pg_dsn, tmp_path, monkeypatch) -> None:
    # ── Host A: migrate (primary owns the schema) + publish shared scope.
    _migrate_schema(pg_dsn)

    host_a_config = Config(**tomllib.loads(_HOST_A_CONFIG))
    published_body = shared_scope_dict(host_a_config)

    backend_a = PostgresBackend(dsn=pg_dsn, schema="corpus")
    try:
        # ``shared_config.published_by`` FKs ``hosts`` — register A first.
        backend_a.upsert_host(
            host_id="host-a",
            hostname="host-a",
            os="linux",
            accelerator={"kind": "cpu", "device_name": None, "vram_mb": None},
        )
        published_version = backend_a.put_shared_config(
            published_body,
            expected_version=0,
            published_by="host-a",
        )
    finally:
        backend_a.close()
    assert published_version == 1

    # ── Host B: run the genuine join flow against the same Postgres.
    # Isolate B's config + state file under tmp_path via CORPUS_FORGE_CONFIG
    # (the federation state file lands beside the resolved config path).
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr("socket.gethostname", lambda: "host-b")

    config_path, awaiting = run_join(
        pg_dsn,
        config_dir=tmp_path,
        interactive=False,
        stream_out=io.StringIO(),
    )

    # B's config loads.
    host_b_config = Config(**tomllib.loads(config_path.read_text(encoding="utf-8")))

    # B's backend.dsn is the join DSN (local scope is independent of A's).
    assert host_b_config.backend.dsn == pg_dsn

    # B's shared scope equals A's published body. Datasets land as
    # COMMENTED blocks (sources-less shared datasets would fail Config
    # validation), so the loaded config carries zero datasets — but the
    # NAMES are surfaced in ``awaiting`` and match A's dataset names.
    host_b_shared = shared_scope_dict(host_b_config)
    assert host_b_config.datasets == []
    assert awaiting == ["notes", "chats"]
    # Compare the non-dataset shared scope (the live-merged part) exactly.
    a_minus_datasets = {k: v for k, v in published_body.items() if k != "datasets"}
    b_minus_datasets = {k: v for k, v in host_b_shared.items() if k != "datasets"}
    assert b_minus_datasets == a_minus_datasets

    # B's host row was registered.
    backend_check = PostgresBackend(dsn=pg_dsn, schema="corpus")
    try:
        hosts = backend_check.list_hosts_with_latest_rate()
    finally:
        backend_check.close()
    host_ids = {h.get("host_id") for h in hosts}
    assert "host-b" in host_ids

    # B's federation-state file records the published version.
    from corpus_forge.admin.federation import read_last_pulled_version

    assert read_last_pulled_version() == published_version
