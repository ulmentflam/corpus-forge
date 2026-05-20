"""Rot-detector for ``scripts/docker-compose.postgres.yml``.

These tests don't call the Docker daemon — they parse the YAML and assert
the contract (service name, image tag, healthcheck, init-sql mount,
env-file reference) that the deployment docs claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "scripts" / "docker-compose.postgres.yml"
ENV_EXAMPLE = REPO_ROOT / "scripts" / ".env.postgres.example"
INITDB = REPO_ROOT / "scripts" / "postgres-initdb.sql"


@pytest.fixture(autouse=True)
def _require_files() -> None:
    for path in (COMPOSE, ENV_EXAMPLE, INITDB):
        if not path.exists():
            pytest.fail(f"Expected {path}; not found.")


@pytest.fixture
def compose() -> dict:
    with COMPOSE.open() as f:
        return yaml.safe_load(f)


def test_yaml_parses(compose: dict) -> None:
    assert isinstance(compose, dict)


def test_exactly_one_service_named_postgres(compose: dict) -> None:
    services = compose.get("services") or {}
    assert list(services.keys()) == ["postgres"], (
        f"expected exactly one 'postgres' service, got {list(services.keys())}"
    )


def test_image_is_pgvector_pg17(compose: dict) -> None:
    image = compose["services"]["postgres"].get("image")
    assert image == "pgvector/pgvector:pg17", f"image was {image!r}"


def test_named_volume_for_data(compose: dict) -> None:
    svc = compose["services"]["postgres"]
    volumes = svc.get("volumes") or []
    # Either short string "postgres-data:/var/lib/postgresql/data" or
    # the long-form dict; accept both.
    found_data = False
    for vol in volumes:
        is_short_form = (
            isinstance(vol, str)
            and vol.startswith("postgres-data:")
            and "/var/lib/postgresql/data" in vol
        )
        is_long_form = isinstance(vol, dict) and vol.get("source") == "postgres-data"
        if is_short_form or is_long_form:
            found_data = True
    assert found_data, f"no postgres-data named-volume mount in {volumes!r}"

    # And the top-level volumes declaration must register it.
    top = compose.get("volumes") or {}
    assert "postgres-data" in top, f"'postgres-data' missing from top-level volumes: {top!r}"


def test_initdb_sql_mount_is_present(compose: dict) -> None:
    volumes = compose["services"]["postgres"].get("volumes") or []
    found = False
    for vol in volumes:
        if isinstance(vol, str):
            if "00-init.sql" in vol and "docker-entrypoint-initdb.d" in vol:
                found = True
                # Ensure read-only mount.
                assert vol.rstrip().endswith(":ro"), (
                    f"init-sql mount should be :ro for safety, got {vol!r}"
                )
        elif isinstance(vol, dict):
            target = vol.get("target", "")
            source = vol.get("source", "")
            if "docker-entrypoint-initdb.d" in target and "init" in source.lower():
                found = True
    assert found, f"no init-sql mount referencing 00-init.sql in {volumes!r}"


def test_healthcheck_uses_pg_isready(compose: dict) -> None:
    hc = compose["services"]["postgres"].get("healthcheck") or {}
    test = hc.get("test")
    # Either a list (["CMD-SHELL", "..."]) or a string.
    flat = " ".join(test) if isinstance(test, list) else str(test)
    assert "pg_isready" in flat, f"healthcheck doesn't use pg_isready: {test!r}"


def test_env_file_is_env_postgres(compose: dict) -> None:
    env_file = compose["services"]["postgres"].get("env_file")
    # env_file may be a single string or a list.
    if isinstance(env_file, list):
        assert any(".env.postgres" in str(e) for e in env_file), env_file
    else:
        assert ".env.postgres" in str(env_file), env_file


def test_restart_policy_unless_stopped(compose: dict) -> None:
    assert compose["services"]["postgres"].get("restart") == "unless-stopped"


def test_env_example_documents_required_vars() -> None:
    body = ENV_EXAMPLE.read_text()
    for var in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        assert var in body, f"{var} missing from .env.postgres.example"
    # Loud placeholder password (the docs and bootstrap script both shout
    # about this — the example must not ship a usable secret).
    assert "CHANGEME" in body.upper()
    # Cp-before-up hint.
    assert ".env.postgres" in body and "cp" in body.lower()


def test_initdb_creates_vector_extension() -> None:
    body = INITDB.read_text()
    assert "CREATE EXTENSION" in body.upper()
    assert "vector" in body.lower()
