"""Tests for the dialect-aware migration loader — B-02.

Asserts that corpus_forge.schema.migrate:
- get_migration_files() accepts a 'dialect' parameter (default "postgres").
- get_migration_files(dialect="postgres") returns the top-level Postgres SQL files
  in numeric order (existing behaviour preserved — no regression).
- get_migration_files(dialect="sqlite") returns the sqlite/ subdir SQL files
  in numeric order.
- apply_migrations() accepts a 'dialect' parameter that dispatches to the
  correct schema subdirectory (schema/sqlite/ for dialect="sqlite").
- The Postgres default is backwards-compatible: calling without dialect still
  reads the top-level schema files.

These tests are RED until the coder extends migrate.py to accept the dialect param.
"""

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.schema import migrate

pytestmark = pytest.mark.skip(
    reason="legacy migration test — pins pre-Alembic file-globbing; deleted in D-10"
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
SQLITE_SCHEMA_DIR = SCHEMA_DIR / "sqlite"


# ── 1. get_migration_files dialect parameter signature ───────────────────


class TestGetMigrationFilesSignature:
    """get_migration_files must accept a dialect parameter."""

    def test_accepts_dialect_parameter(self):
        """get_migration_files() must have a 'dialect' parameter."""
        sig = inspect.signature(migrate.get_migration_files)
        params = sig.parameters
        assert "dialect" in params, (
            "get_migration_files() is missing the 'dialect' parameter. "
            "Add: def get_migration_files(schema_dir: Path, dialect: str = 'postgres')"
            " -> list[Path]"
        )

    def test_dialect_has_default_of_postgres(self):
        """The 'dialect' parameter must default to 'postgres'."""
        sig = inspect.signature(migrate.get_migration_files)
        param = sig.parameters.get("dialect")
        assert param is not None, "No 'dialect' parameter found"
        assert param.default == "postgres", f"Expected default 'postgres', got {param.default!r}"

    def test_accepts_sqlite_as_dialect_value(self):
        """Calling get_migration_files with dialect='sqlite' must not raise TypeError."""
        # This will raise FileNotFoundError or return empty list if sqlite dir doesn't exist,
        # but must not raise TypeError (which would mean the param isn't accepted).
        try:
            migrate.get_migration_files(SCHEMA_DIR, dialect="sqlite")
        except TypeError as exc:
            pytest.fail(f"get_migration_files raised TypeError with dialect='sqlite': {exc}")
        except Exception:
            # FileNotFoundError, etc. are acceptable until the coder creates the files
            pass


# ── 2. get_migration_files postgres dispatch (regression) ────────────────


class TestGetMigrationFilesPostgresDispatch:
    """Existing Postgres dispatch must still work (no regression)."""

    def test_postgres_default_returns_top_level_files(self):
        """dialect='postgres' (default) returns top-level .sql files."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="postgres")
        assert isinstance(files, list), "Expected list of Path objects"
        # At least 001_core.sql should be in the result
        names = {f.name for f in files}
        assert "001_core.sql" in names, (
            f"001_core.sql not found in postgres migration files: {names}"
        )

    def test_postgres_files_sorted_numerically(self):
        """Postgres migration files must be sorted by numeric prefix."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="postgres")
        prefixes = [int(f.stem.split("_")[0]) for f in files]
        assert prefixes == sorted(prefixes), (
            f"Files not sorted numerically: {[f.name for f in files]}"
        )

    def test_no_dialect_uses_postgres_default(self):
        """Calling without dialect must behave the same as dialect='postgres'."""
        # get_migration_files currently takes only schema_dir; the new version adds dialect.
        # After the coder's change, calling with just schema_dir must still work.
        try:
            files_default = migrate.get_migration_files(SCHEMA_DIR)
            files_explicit = migrate.get_migration_files(SCHEMA_DIR, dialect="postgres")
            assert [f.name for f in files_default] == [f.name for f in files_explicit], (
                "Calling without dialect must return same files as dialect='postgres'"
            )
        except TypeError:
            pytest.fail(
                "get_migration_files(SCHEMA_DIR) raised TypeError — "
                "the dialect parameter must have a default value"
            )

    def test_postgres_files_are_from_top_level_dir(self):
        """Postgres migration files must come from SCHEMA_DIR, not from sqlite/ subdir."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="postgres")
        for f in files:
            assert f.parent == SCHEMA_DIR, (
                f"Expected Postgres migration {f.name} to be in {SCHEMA_DIR}, "
                f"but found it in {f.parent}"
            )


# ── 3. get_migration_files sqlite dispatch ───────────────────────────────


class TestGetMigrationFilesSQLiteDispatch:
    """dialect='sqlite' must read from the sqlite/ subdirectory."""

    def test_sqlite_dialect_returns_sqlite_subdir_files(self):
        """dialect='sqlite' returns files from corpus_forge/schema/sqlite/."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="sqlite")
        assert isinstance(files, list), "Expected list of Path objects"
        for f in files:
            assert f.parent == SQLITE_SCHEMA_DIR, (
                f"Expected SQLite migration {f.name} to be in {SQLITE_SCHEMA_DIR}, "
                f"found in {f.parent}"
            )

    def test_sqlite_files_sorted_numerically(self):
        """SQLite migration files must be sorted by numeric prefix."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="sqlite")
        if not files:
            pytest.skip("No SQLite migration files yet — coder must create them")
        prefixes = [int(f.stem.split("_")[0]) for f in files]
        assert prefixes == sorted(prefixes), (
            f"SQLite files not sorted numerically: {[f.name for f in files]}"
        )

    def test_sqlite_files_include_all_three_migrations(self):
        """sqlite/ subdir must contain 001_core.sql, 002_chunk_content_hash.sql, 003_sync.sql."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="sqlite")
        names = {f.name for f in files}
        expected = {"001_core.sql", "002_chunk_content_hash.sql", "003_sync.sql"}
        assert expected.issubset(names), (
            f"Expected {expected} in SQLite migration files, found: {names}"
        )

    def test_sqlite_files_do_not_include_postgres_only_files(self):
        """SQLite migration files must not include Postgres-only files like 002_views.sql."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="sqlite")
        names = {f.name for f in files}
        assert "002_views.sql" not in names, (
            "002_views.sql (Postgres-only view) must not appear in SQLite migrations"
        )


# ── 4. apply_migrations dialect parameter signature ──────────────────────


class TestApplyMigrationsSignature:
    """apply_migrations() must accept a 'dialect' parameter."""

    def test_accepts_dialect_parameter(self):
        """apply_migrations() must have a 'dialect' parameter."""
        sig = inspect.signature(migrate.apply_migrations)
        params = sig.parameters
        assert "dialect" in params, (
            "apply_migrations() is missing the 'dialect' parameter. "
            "Add: def apply_migrations(backend, schema_dir: Path, dialect: str = 'postgres')"
        )

    def test_dialect_has_default_of_postgres(self):
        """The 'dialect' parameter must default to 'postgres'."""
        sig = inspect.signature(migrate.apply_migrations)
        param = sig.parameters.get("dialect")
        assert param is not None, "No 'dialect' parameter found in apply_migrations"
        assert param.default == "postgres", f"Expected default 'postgres', got {param.default!r}"


# ── 5. apply_migrations sqlite dispatch (file routing) ──────────────────


class TestApplyMigrationsSQLiteDispatch:
    """apply_migrations(backend, schema_dir, dialect='sqlite') reads from sqlite/ subdir."""

    def test_sqlite_dialect_calls_get_migration_files_with_sqlite_subdir(self):
        """apply_migrations with dialect='sqlite' must call get_migration_files
        in a way that resolves to the sqlite/ subdirectory."""
        # Patch get_migration_files to capture what path was used
        mock_backend = MagicMock()
        mock_backend._execute = MagicMock()

        captured_paths = []

        def capturing_get(schema_dir, dialect="postgres"):
            captured_paths.append((schema_dir, dialect))
            # Return empty list so apply_migrations doesn't try to read missing files
            return []

        with patch.object(migrate, "get_migration_files", side_effect=capturing_get):
            migrate.apply_migrations(mock_backend, SCHEMA_DIR, dialect="sqlite")

        assert len(captured_paths) >= 1, "apply_migrations did not call get_migration_files"
        _, used_dialect = captured_paths[0]
        assert used_dialect == "sqlite", (
            f"apply_migrations with dialect='sqlite' called get_migration_files "
            f"with dialect={used_dialect!r}, expected 'sqlite'"
        )

    def test_postgres_default_dispatch_unchanged(self):
        """apply_migrations without dialect still uses Postgres dispatch."""
        mock_backend = MagicMock()
        mock_backend._execute = MagicMock()

        captured_paths = []

        def capturing_get(schema_dir, dialect="postgres"):
            captured_paths.append((schema_dir, dialect))
            return []

        with patch.object(migrate, "get_migration_files", side_effect=capturing_get):
            # Call without dialect — should default to postgres
            try:
                migrate.apply_migrations(mock_backend, SCHEMA_DIR)
            except TypeError:
                pytest.fail(
                    "apply_migrations(backend, schema_dir) raised TypeError — "
                    "dialect must have a default value"
                )

        if captured_paths:
            _, used_dialect = captured_paths[0]
            assert used_dialect == "postgres", (
                f"Default dialect should be 'postgres', got {used_dialect!r}"
            )


# ── 6. get_migration_files with unknown dialect ──────────────────────────


class TestGetMigrationFilesUnknownDialect:
    """Unknown dialect values should raise or return empty list — not silently corrupt."""

    def test_unknown_dialect_raises_or_returns_empty(self):
        """dialect='mysql' is unknown — raise ValueError or return [] (not Postgres files)."""
        try:
            files = migrate.get_migration_files(SCHEMA_DIR, dialect="mysql")
            # If it doesn't raise, it must not silently return Postgres files
            postgres_names = {
                "001_core.sql",
                "002_chunk_content_hash.sql",
                "003_sync.sql",
                "002_views.sql",
            }
            file_names = {f.name for f in files}
            assert not file_names.intersection(postgres_names), (
                f"Unknown dialect 'mysql' silently returned Postgres files: {file_names}. "
                "Expected either ValueError or empty list."
            )
        except (ValueError, FileNotFoundError, NotImplementedError):
            pass  # Any of these are acceptable for an unknown dialect


# ── 7. Numeric sort key stability ────────────────────────────────────────


class TestNumericSortKey:
    """The sort-key extraction must be stable across both dialects."""

    def test_postgres_sort_key_stable(self):
        """Postgres files: stem.split('_')[0] converts to int cleanly."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="postgres")
        for f in files:
            number_part = f.stem.split("_")[0]
            assert number_part.isdigit(), (
                f"Postgres file {f.name} has non-numeric prefix: {number_part!r}"
            )
            int(number_part)  # must not raise

    def test_sqlite_sort_key_stable(self):
        """SQLite files: stem.split('_')[0] converts to int cleanly."""
        files = migrate.get_migration_files(SCHEMA_DIR, dialect="sqlite")
        for f in files:
            number_part = f.stem.split("_")[0]
            assert number_part.isdigit(), (
                f"SQLite file {f.name} has non-numeric prefix: {number_part!r}"
            )
            int(number_part)  # must not raise
