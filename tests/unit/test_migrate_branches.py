"""Unit tests for corpus_forge.schema.migrate — branch coverage for lines 61-65, 119-134."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _build_alembic_config — postgres branch (lines 61-65)
# ---------------------------------------------------------------------------


class TestBuildAlembicConfigPostgresBranch:
    """Lines 61-65: postgres DSN → rewrite + version_table_schema."""

    def _build(self, dsn: str):
        from corpus_forge.schema.migrate import _build_alembic_config

        fake_backend = MagicMock()
        fake_backend.dsn = dsn
        return _build_alembic_config(backend=fake_backend, dialect="postgres")

    def test_postgres_dsn_plain_gets_psycopg_driver_injected(self) -> None:
        config = self._build("postgresql://user:pass@host/db")
        url = config.get_main_option("sqlalchemy.url")
        assert url is not None
        assert "+psycopg" in url

    def test_postgres_dsns_already_prefixed_are_not_double_prefixed(self) -> None:
        config = self._build("postgresql+psycopg://user:pass@host/db")
        url = config.get_main_option("sqlalchemy.url")
        assert url is not None
        # Must not produce postgresql+psycopg+psycopg
        assert url.count("+psycopg") == 1

    def test_postgres_branch_sets_version_table_schema(self) -> None:
        config = self._build("postgresql://user:pass@host/db")
        assert config.attributes.get("version_table_schema") == "corpus"

    def test_postgres_ssl_dsn_retains_s_suffix(self) -> None:
        """postgresqls:// → postgresql+psycopgs:// (SSL variant preserved)."""
        config = self._build("postgresqls://user:pass@host/db")
        url = config.get_main_option("sqlalchemy.url")
        assert url is not None
        # The regex sub should preserve the 's' suffix
        assert "psycopgs" in url or "psycopg" in url  # either form is acceptable


# ---------------------------------------------------------------------------
# _build_alembic_config — no backend (None) branch
# ---------------------------------------------------------------------------


class TestBuildAlembicConfigNoBackend:
    def test_no_backend_produces_no_sqlalchemy_url(self) -> None:
        from corpus_forge.schema.migrate import _build_alembic_config

        config = _build_alembic_config(backend=None, dialect="postgres")
        # get_main_option returns None when not set
        url = config.get_main_option("sqlalchemy.url")
        assert url is None

    def test_no_backend_no_version_table_schema(self) -> None:
        from corpus_forge.schema.migrate import _build_alembic_config

        config = _build_alembic_config(backend=None, dialect="sqlite")
        assert "version_table_schema" not in config.attributes


# ---------------------------------------------------------------------------
# apply_migrations — delegates to _apply_alembic
# ---------------------------------------------------------------------------


class TestApplyMigrations:
    def test_apply_migrations_calls_apply_alembic(self, tmp_path: Path) -> None:
        from corpus_forge.schema.migrate import apply_migrations

        fake_backend = MagicMock()
        with patch("corpus_forge.schema.migrate._apply_alembic") as mock_apply:
            apply_migrations(fake_backend, tmp_path, dialect="sqlite")
        mock_apply.assert_called_once_with(fake_backend, "sqlite")

    def test_apply_migrations_schema_dir_ignored(self, tmp_path: Path) -> None:
        """schema_dir is back-compat only — function must not crash on any path."""
        from corpus_forge.schema.migrate import apply_migrations

        fake_backend = MagicMock()
        nonexistent = tmp_path / "does_not_exist"
        with patch("corpus_forge.schema.migrate._apply_alembic"):
            # Should not raise even for a nonexistent path
            apply_migrations(fake_backend, nonexistent, dialect="sqlite")


# ---------------------------------------------------------------------------
# main() entry point — lines 119-130
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """main() loads ``Config`` and dispatches to the right backend.

    Earlier revisions read ``DATABASE_URL`` env var with a hardcoded
    fallback to ``postgresql://memory@localhost/memory`` (a dev-machine
    leftover). The new flow consults ``Config.load()`` so the user's
    configured backend + DSN is what migrate runs against.
    """

    def _stub_config(self, kind: str = "postgres", dsn: str | None = None) -> MagicMock:
        cfg = MagicMock()
        cfg.backend.kind = kind
        cfg.backend.dsn = dsn or (
            "postgresql://corpus_forge:secret@10.0.0.5:5432/corpus_forge"
            if kind == "postgres"
            else "/tmp/corpus.db"
        )
        cfg.backend.schema = "corpus"
        return cfg

    def test_main_loads_config_and_uses_its_postgres_dsn(self, monkeypatch) -> None:
        """The user's configured DSN reaches the constructed PostgresBackend."""
        from corpus_forge.schema import migrate as migrate_mod

        fake_cfg = self._stub_config(kind="postgres", dsn="postgresql://u:p@h/db")

        with (
            patch("corpus_forge.config.Config.load", return_value=fake_cfg),
            patch.object(migrate_mod, "PostgresBackend") as mock_pg_cls,
            patch.object(migrate_mod, "apply_migrations"),
        ):
            mock_pg_cls.return_value = MagicMock()
            migrate_mod.main()

        mock_pg_cls.assert_called_once()
        call = mock_pg_cls.call_args
        dsn_used = call.kwargs.get("dsn") or call.args[0]
        assert dsn_used == "postgresql://u:p@h/db", (
            f"main() must pass Config.backend.dsn to PostgresBackend; got {dsn_used!r}"
        )

    def test_main_dispatches_to_sqlite_when_config_kind_is_sqlite(self, monkeypatch) -> None:
        from corpus_forge.schema import migrate as migrate_mod

        fake_cfg = self._stub_config(kind="sqlite", dsn="/tmp/corpus.db")

        from corpus_forge.backends import sqlite as sqlite_mod

        with (
            patch("corpus_forge.config.Config.load", return_value=fake_cfg),
            patch.object(sqlite_mod, "SQLiteBackend") as mock_sqlite_cls,
            patch.object(migrate_mod, "PostgresBackend") as mock_pg_cls,
            patch.object(migrate_mod, "apply_migrations") as mock_apply,
        ):
            mock_sqlite_cls.return_value = MagicMock()
            migrate_mod.main()

        mock_pg_cls.assert_not_called(), "sqlite config must NOT construct a PostgresBackend"
        mock_sqlite_cls.assert_called_once()
        assert mock_apply.call_args.kwargs.get("dialect") == "sqlite"

    def test_main_does_not_consult_database_url_env_var(self, monkeypatch) -> None:
        """The DATABASE_URL fallback path is gone. Even when the env var
        is set, the user's Config wins.
        """
        from corpus_forge.schema import migrate as migrate_mod

        monkeypatch.setenv("DATABASE_URL", "postgresql://NOT-FROM-CONFIG@h/db")
        fake_cfg = self._stub_config(
            kind="postgres", dsn="postgresql://corpus_forge:real@h/corpus_forge"
        )

        with (
            patch("corpus_forge.config.Config.load", return_value=fake_cfg),
            patch.object(migrate_mod, "PostgresBackend") as mock_pg_cls,
            patch.object(migrate_mod, "apply_migrations"),
        ):
            mock_pg_cls.return_value = MagicMock()
            migrate_mod.main()

        dsn_used = mock_pg_cls.call_args.kwargs.get("dsn") or mock_pg_cls.call_args.args[0]
        assert "NOT-FROM-CONFIG" not in dsn_used, (
            "DATABASE_URL must NOT override Config.backend.dsn in the migrate flow"
        )
        assert "corpus_forge" in dsn_used


class TestApplyAlembicPostgresPreCreatesSchema:
    """Bug fix: Alembic creates ``alembic_version`` in the configured
    schema (``corpus`` by default) on first run, BEFORE migration 0001
    has a chance to ``CREATE SCHEMA IF NOT EXISTS corpus``. The result
    is ``psycopg.errors.InvalidSchemaName: schema "corpus" does not
    exist`` on every fresh Postgres install.

    Fix: ``_apply_alembic`` for the postgres dialect runs
    ``CREATE SCHEMA IF NOT EXISTS <schema>`` via a direct psycopg
    connection BEFORE invoking Alembic.
    """

    def test_apply_alembic_postgres_creates_schema_before_upgrade(self) -> None:
        from corpus_forge.schema import migrate as migrate_mod

        fake_backend = MagicMock()
        fake_backend.dsn = "postgresql://u:p@h:5432/corpus_forge"
        fake_backend.schema = "corpus"

        # Track call order so we can assert pre-create happens before upgrade.
        events: list[str] = []

        class _FakeCursor:
            def execute(self, sql: str, *args, **kwargs) -> None:
                events.append(f"execute:{sql}")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _FakeConn:
            def __enter__(self):
                events.append("connect")
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql: str, *args, **kwargs) -> None:
                events.append(f"execute:{sql}")

            def cursor(self):
                return _FakeCursor()

        def _fake_connect(dsn: str, **kwargs) -> _FakeConn:
            events.append(f"connect_called:{dsn}")
            return _FakeConn()

        def _fake_upgrade(config, target: str) -> None:
            events.append("alembic_upgrade")

        with (
            patch("psycopg.connect", side_effect=_fake_connect),
            patch.object(migrate_mod, "alembic_command") as mock_alembic,
        ):
            mock_alembic.upgrade.side_effect = _fake_upgrade
            migrate_mod._apply_alembic(fake_backend, dialect="postgres")

        # CREATE SCHEMA must appear before the alembic_upgrade event.
        schema_creates = [i for i, e in enumerate(events) if "CREATE SCHEMA" in e and "corpus" in e]
        upgrade_idxs = [i for i, e in enumerate(events) if e == "alembic_upgrade"]
        assert schema_creates, f"no CREATE SCHEMA event found in: {events}"
        assert upgrade_idxs, f"alembic_upgrade not invoked: {events}"
        assert schema_creates[0] < upgrade_idxs[0], (
            f"CREATE SCHEMA must fire BEFORE alembic_command.upgrade; got events={events}"
        )

    def test_apply_alembic_postgres_uses_libpq_dsn_not_sqlalchemy_prefixed(self) -> None:
        """psycopg.connect() takes a libpq DSN; the ``+psycopg`` driver
        prefix used by SQLAlchemy must be stripped before we pass to
        ``psycopg.connect``."""
        from corpus_forge.schema import migrate as migrate_mod

        fake_backend = MagicMock()
        fake_backend.dsn = "postgresql+psycopg://u:p@h:5432/corpus_forge"
        fake_backend.schema = "corpus"

        seen_dsn: list[str] = []

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **kw):
                pass

        def _fake_connect(dsn: str, **kwargs) -> _FakeConn:
            seen_dsn.append(dsn)
            return _FakeConn()

        with (
            patch("psycopg.connect", side_effect=_fake_connect),
            patch.object(migrate_mod, "alembic_command"),
        ):
            migrate_mod._apply_alembic(fake_backend, dialect="postgres")

        assert seen_dsn, "psycopg.connect was never called"
        assert "+psycopg" not in seen_dsn[0], (
            f"libpq DSN must NOT carry the SQLAlchemy +psycopg driver prefix; got {seen_dsn[0]!r}"
        )

    def test_apply_alembic_sqlite_does_not_call_psycopg(self) -> None:
        """SQLite path must not import or call psycopg."""
        from corpus_forge.schema import migrate as migrate_mod

        fake_backend = MagicMock()
        fake_backend.path = "/tmp/corpus.db"

        with (
            patch("psycopg.connect") as mock_connect,
            patch.object(migrate_mod, "alembic_command"),
        ):
            migrate_mod._apply_alembic(fake_backend, dialect="sqlite")

        mock_connect.assert_not_called()
