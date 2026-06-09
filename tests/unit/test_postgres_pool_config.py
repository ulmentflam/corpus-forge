"""Unit tests for PostgresBackend connection-pool tuning (operator-seeded
2026-06-09: "multi-worker might be crippling connections").

Pins the resolution matrix (explicit kwarg → ``CF_PG_POOL_*`` env → default),
typo-safety, sane clamping, and — critically — that ``max_idle`` is passed to
the pool and defaults to 120 s (not psycopg's 600 s) so idle connections
spiked under multi-worker load drain back to Postgres in ~2 minutes.

``ConnectionPool`` is mocked so the tests never touch a real Postgres.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from corpus_forge.backends import postgres as pg
from corpus_forge.backends.postgres import PostgresBackend

_DSN = "postgresql://test@localhost/test"


def _construct(monkeypatch: pytest.MonkeyPatch | None = None, **kwargs: Any) -> dict[str, Any]:
    """Construct a backend with ConnectionPool mocked; return the pool kwargs."""

    with patch("corpus_forge.backends.postgres.ConnectionPool") as mock_pool:
        backend = PostgresBackend(dsn=_DSN, **kwargs)
        assert backend is not None
    assert mock_pool.call_count == 1
    return mock_pool.call_args.kwargs


def test_default_pool_params() -> None:
    kw = _construct()
    assert kw["min_size"] == pg._DEFAULT_POOL_MIN_SIZE == 0
    assert kw["max_size"] == pg._DEFAULT_POOL_MAX_SIZE == 8
    assert kw["timeout"] == pg._DEFAULT_POOL_TIMEOUT == 30.0
    assert kw["num_workers"] == pg._DEFAULT_POOL_NUM_WORKERS == 3


def test_default_max_idle_is_lowered_to_120() -> None:
    # Regression pin: the pool MUST cap idle connections at 120 s, not inherit
    # psycopg-pool's 600 s default (the multi-worker sustained-pressure fix).
    assert pg._DEFAULT_POOL_MAX_IDLE == 120.0
    assert _construct()["max_idle"] == 120.0


def test_env_overrides_all_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_PG_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("CF_PG_POOL_MAX_SIZE", "3")
    monkeypatch.setenv("CF_PG_POOL_MAX_IDLE", "45")
    monkeypatch.setenv("CF_PG_POOL_TIMEOUT", "10")
    monkeypatch.setenv("CF_PG_POOL_NUM_WORKERS", "2")
    kw = _construct()
    assert kw["min_size"] == 1
    assert kw["max_size"] == 3
    assert kw["max_idle"] == 45.0
    assert kw["timeout"] == 10.0
    assert kw["num_workers"] == 2


def test_explicit_kwargs_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_PG_POOL_MAX_SIZE", "99")
    monkeypatch.setenv("CF_PG_POOL_MAX_IDLE", "999")
    kw = _construct(pool_max_size=4, pool_max_idle=30.0)
    assert kw["max_size"] == 4
    assert kw["max_idle"] == 30.0


def test_bad_env_int_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_PG_POOL_MAX_SIZE", "not-a-number")
    assert _construct()["max_size"] == pg._DEFAULT_POOL_MAX_SIZE


def test_bad_env_float_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_PG_POOL_MAX_IDLE", "soon")
    assert _construct()["max_idle"] == pg._DEFAULT_POOL_MAX_IDLE


def test_max_size_clamped_at_least_min_and_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo'd combo (max < min, or max < 1) must be clamped, never handed to
    # ConnectionPool as an invalid combo that crashes every backend.
    kw = _construct(pool_min_size=5, pool_max_size=2)
    assert kw["min_size"] == 5
    assert kw["max_size"] == 5  # clamped up to min_size

    kw0 = _construct(pool_max_size=0)
    assert kw0["max_size"] == 1  # clamped up to 1


def test_negative_min_size_clamped_to_zero() -> None:
    assert _construct(pool_min_size=-3)["min_size"] == 0


def test_num_workers_clamped_to_at_least_one() -> None:
    assert _construct(pool_num_workers=0)["num_workers"] == 1


def test_pool_settings_recorded_on_instance() -> None:
    with patch("corpus_forge.backends.postgres.ConnectionPool"):
        backend = PostgresBackend(dsn=_DSN, pool_max_size=5)
    assert backend._pool_settings["max_size"] == 5
    assert backend._pool_settings["max_idle"] == 120.0


def test_resolve_pool_int_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pg._resolve_pool_int(7, "CF_X", 3) == 7  # explicit wins
    monkeypatch.setenv("CF_X", "5")
    assert pg._resolve_pool_int(None, "CF_X", 3) == 5  # env wins over default
    monkeypatch.setenv("CF_X", "bad")
    assert pg._resolve_pool_int(None, "CF_X", 3) == 3  # bad → default
    monkeypatch.delenv("CF_X", raising=False)
    assert pg._resolve_pool_int(None, "CF_X", 3) == 3  # unset → default


def test_resolve_pool_float_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pg._resolve_pool_float(2.5, "CF_Y", 1.0) == 2.5
    monkeypatch.setenv("CF_Y", "9.5")
    assert pg._resolve_pool_float(None, "CF_Y", 1.0) == 9.5
    monkeypatch.setenv("CF_Y", "nope")
    assert pg._resolve_pool_float(None, "CF_Y", 1.0) == 1.0


def test_pool_opened_with_open_true() -> None:
    # Preserve the existing pre-warm behavior: pool constructed with open=True.
    assert _construct()["open"] is True
