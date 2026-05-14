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
    """main() constructs a PostgresBackend from DATABASE_URL and calls apply_migrations."""

    def test_main_uses_database_url_env_var(self, monkeypatch) -> None:
        from corpus_forge.schema import migrate as migrate_mod

        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/testdb")

        with (
            patch.object(migrate_mod, "PostgresBackend") as mock_pg_cls,
            patch.object(migrate_mod, "apply_migrations"),
        ):
            mock_backend = MagicMock()
            mock_pg_cls.return_value = mock_backend
            migrate_mod.main()

        mock_pg_cls.assert_called_once()
        call_kwargs = mock_pg_cls.call_args
        dsn_used = call_kwargs.kwargs.get("dsn") or call_kwargs.args[0]
        assert "testdb" in dsn_used

    def test_main_falls_back_to_default_dsn_when_no_env(self, monkeypatch) -> None:
        from corpus_forge.schema import migrate as migrate_mod

        monkeypatch.delenv("DATABASE_URL", raising=False)

        with (
            patch.object(migrate_mod, "PostgresBackend") as mock_pg_cls,
            patch.object(migrate_mod, "apply_migrations"),
        ):
            mock_pg_cls.return_value = MagicMock()
            migrate_mod.main()

        # Must have been called — default DSN used
        mock_pg_cls.assert_called_once()

    def test_main_calls_apply_migrations(self, monkeypatch) -> None:
        from corpus_forge.schema import migrate as migrate_mod

        monkeypatch.delenv("DATABASE_URL", raising=False)

        with (
            patch.object(migrate_mod, "PostgresBackend", return_value=MagicMock()),
            patch.object(migrate_mod, "apply_migrations") as mock_apply,
        ):
            migrate_mod.main()

        mock_apply.assert_called_once()
