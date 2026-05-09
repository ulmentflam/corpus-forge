"""Micro-test that gates the INT-01 DSN-fixture refactor.

Red signal: `pg_dsn` fixture does not exist yet in conftest.py.
Green signal: after the coder adds `pg_dsn` to conftest, all four tests pass.

Contract being pinned:
- `pg_dsn` is a str.
- It starts with "postgresql://" (not "postgresql+psycopg2://").
- `psycopg.conninfo.conninfo_to_dict(pg_dsn)` parses without raising.
- `psycopg.connect(pg_dsn)` opens successfully and `SELECT 1` returns 1.
"""

import psycopg
import psycopg.conninfo
import pytest

pytestmark = pytest.mark.integration


class TestPgDsnFixtureShape:
    """Assert structural contract on the `pg_dsn` fixture."""

    def test_pg_dsn_is_str(self, pg_dsn: str) -> None:
        """pg_dsn must be a plain Python str, not bytes or a URL object."""
        assert isinstance(pg_dsn, str), f"Expected str, got {type(pg_dsn)}"

    def test_pg_dsn_starts_with_postgresql_scheme(self, pg_dsn: str) -> None:
        """pg_dsn must use the bare postgresql:// scheme — not postgresql+psycopg2://."""
        assert pg_dsn.startswith("postgresql://"), (
            f"DSN must start with 'postgresql://', got: {pg_dsn!r}"
        )

    def test_pg_dsn_no_sqlalchemy_driver_prefix(self, pg_dsn: str) -> None:
        """Explicitly reject the SQLAlchemy-style dialect+driver prefix."""
        assert "+psycopg2" not in pg_dsn, (
            f"DSN must not contain '+psycopg2'; psycopg.connect() rejects it. Got: {pg_dsn!r}"
        )

    def test_pg_dsn_parses_as_libpq_conninfo(self, pg_dsn: str) -> None:
        """psycopg.conninfo.conninfo_to_dict must parse the DSN without raising."""
        try:
            info = psycopg.conninfo.conninfo_to_dict(pg_dsn)
        except Exception as exc:
            pytest.fail(
                f"psycopg.conninfo.conninfo_to_dict raised {type(exc).__name__}: {exc}\n"
                f"DSN was: {pg_dsn!r}"
            )
        # At minimum, host and dbname keys must be present
        assert "host" in info or "hostaddr" in info, (
            f"Parsed conninfo dict has no 'host' key: {info}"
        )


class TestPgDsnLiveConnect:
    """End-to-end: open a real psycopg connection using pg_dsn and run SELECT 1."""

    def test_connect_and_select_one(self, pg_dsn: str) -> None:
        """psycopg.connect(pg_dsn) must succeed and SELECT 1 must return 1."""
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
        assert row is not None, "SELECT 1 returned no rows"
        assert row[0] == 1, f"Expected SELECT 1 → 1, got {row[0]!r}"
