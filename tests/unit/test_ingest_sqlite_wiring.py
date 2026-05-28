"""B-13 red suite: tests for SQLite backend wiring in ingest.py.

Covers:
- kind=="sqlite" dispatches to SQLiteBackend with path=dsn, schema=schema
- kind=="postgres" still dispatches to PostgresBackend with dsn=dsn, schema=schema
- Unknown kind raises ValueError containing the kind name
- Lazy-import: SQLiteBackend is imported inside the function (not at module level)
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.ingest import ingest_once


def _make_sqlite_config():
    """Return a minimal mock Config with kind='sqlite'."""
    config = MagicMock()
    config.backend.kind = "sqlite"
    config.backend.dsn = "~/Library/Application Support/corpus-forge/corpus.db"
    config.backend.schema = "corpus"
    config.daemon.log_level = "INFO"
    config.datasets = []
    config.embedders = []
    return config


def _make_postgres_config():
    """Return a minimal mock Config with kind='postgres'."""
    config = MagicMock()
    config.backend.kind = "postgres"
    config.backend.dsn = "postgresql://user:pass@localhost/db"
    config.backend.schema = "corpus"
    config.daemon.log_level = "INFO"
    config.datasets = []
    config.embedders = []
    return config


class TestIngestOnceSQLiteDispatch:
    """ingest_once dispatches to SQLiteBackend when kind=='sqlite'."""

    def test_sqlite_instantiates_sqlite_backend(self):
        """SQLiteBackend is constructed when kind=='sqlite'."""
        config = _make_sqlite_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        mock_cls.assert_called_once()

    def test_sqlite_backend_receives_path_kwarg(self):
        """SQLiteBackend constructor receives path= (not dsn=) set to config.backend.dsn."""
        config = _make_sqlite_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        _kwargs = mock_cls.call_args.kwargs
        assert "path" in _kwargs, (
            f"SQLiteBackend must be constructed with path= keyword arg; got kwargs: {_kwargs}"
        )
        assert _kwargs["path"] == config.backend.dsn, (
            "path= must equal config.backend.dsn (dsn field repurposed as file path)"
        )

    def test_sqlite_backend_receives_schema_kwarg(self):
        """SQLiteBackend constructor receives schema= set to config.backend.schema."""
        config = _make_sqlite_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        _kwargs = mock_cls.call_args.kwargs
        assert "schema" in _kwargs, (
            f"SQLiteBackend must be constructed with schema= keyword arg; got kwargs: {_kwargs}"
        )
        assert _kwargs["schema"] == "corpus"

    def test_sqlite_backend_does_not_receive_dsn_kwarg(self):
        """SQLiteBackend must NOT receive dsn= (that is the Postgres arg)."""
        config = _make_sqlite_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        _kwargs = mock_cls.call_args.kwargs
        assert "dsn" not in _kwargs, (
            "SQLiteBackend must not receive dsn= kwarg; dsn is for PostgresBackend. "
            f"Got kwargs: {_kwargs}"
        )

    def test_migrate_is_called_once_on_sqlite_backend(self):
        """migrate() is called exactly once on the SQLiteBackend instance."""
        config = _make_sqlite_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        mock_instance.migrate.assert_called_once()

    def test_sqlite_dispatch_does_not_call_postgres_backend(self):
        """When kind=='sqlite', PostgresBackend must not be constructed."""
        config = _make_sqlite_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_sqlite_cls,
            patch("corpus_forge.backends.postgres.PostgresBackend") as mock_pg_cls,
        ):
            mock_instance = MagicMock()
            mock_sqlite_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        mock_pg_cls.assert_not_called()


class TestIngestOncePostgresRegressionWiring:
    """Regression: kind=='postgres' still wires to PostgresBackend correctly."""

    def test_postgres_instantiates_postgres_backend(self):
        """PostgresBackend is constructed when kind=='postgres' (no regression)."""
        config = _make_postgres_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.postgres.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        mock_cls.assert_called_once()

    def test_postgres_backend_receives_dsn_kwarg(self):
        """PostgresBackend constructor receives dsn= (not path=)."""
        config = _make_postgres_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.postgres.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        _kwargs = mock_cls.call_args.kwargs
        assert "dsn" in _kwargs, (
            f"PostgresBackend must be constructed with dsn= keyword; got {_kwargs}"
        )
        assert _kwargs["dsn"] == config.backend.dsn

    def test_postgres_backend_receives_schema_kwarg(self):
        """PostgresBackend constructor receives schema= set to config.backend.schema."""
        config = _make_postgres_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.postgres.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        _kwargs = mock_cls.call_args.kwargs
        assert "schema" in _kwargs
        assert _kwargs["schema"] == "corpus"

    def test_postgres_migrate_is_called_once(self):
        """migrate() is still called exactly once on the PostgresBackend instance."""
        config = _make_postgres_config()

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.postgres.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        mock_instance.migrate.assert_called_once()


class TestIngestOnceUnknownKindRaises:
    """Unknown backend kind raises ValueError with informative message."""

    def test_unknown_kind_raises_value_error(self):
        """A completely unknown kind (e.g. 'duckdb') raises ValueError."""
        config = MagicMock()
        config.backend.kind = "duckdb"
        config.backend.dsn = "duckdb://memory"
        config.backend.schema = "corpus"
        config.datasets = []
        config.embedders = []

        with pytest.raises(ValueError):
            ingest_once(config)

    def test_unknown_kind_error_message_contains_kind_name(self):
        """ValueError message must contain the unknown kind for debuggability."""
        config = MagicMock()
        config.backend.kind = "duckdb"
        config.backend.dsn = "duckdb://memory"
        config.backend.schema = "corpus"
        config.datasets = []
        config.embedders = []

        with pytest.raises(ValueError, match="duckdb"):
            ingest_once(config)

    def test_nonsense_kind_raises_value_error(self):
        """A nonsense kind string raises ValueError."""
        config = MagicMock()
        config.backend.kind = "notarealbackend"
        config.backend.dsn = "somedsn"
        config.backend.schema = "corpus"
        config.datasets = []
        config.embedders = []

        with pytest.raises(ValueError, match="notarealbackend"):
            ingest_once(config)


# Temporarily skipped (PR #72): this class verifies that
# `corpus_forge.backends.sqlite` is lazy-imported inside `ingest_once`, but
# its verification strategy pops and re-imports `corpus_forge.ingest` from
# `sys.modules`. Under pytest-xdist `-n auto`, the briefly-distinct in-flight
# `corpus_forge.ingest` module makes `corpus_forge.cli`'s captured reference
# go stale, so downstream tests that patch `corpus_forge.ingest.main` and
# invoke the Typer `app` see patches as uncalled. Cascades dozens of failures
# across `tests/cli/test_ingest_cli_resume_flags.py` +
# `tests/unit/test_cli_ingest_status.py` (QA Advisory 5 on PR #72).
#
# Follow-up: replace the sys.modules-mutation strategy with either a subprocess
# (clean import in a fresh process) or move this check to a dedicated CI step
# that runs after the rest of the suite.
@pytest.mark.skip(reason="Pollutes sys.modules under -n auto; QA Advisory 5 (PR #72)")
class TestIngestOnceSQLiteLazyImport:
    """The SQLiteBackend import happens inside the function, not at module level.

    Verification strategy: patch the name that would be bound inside the function
    via create=True (so the patch targets the local binding, not the module).
    If the function uses a lazy import, patching corpus_forge.ingest.SQLiteBackend
    with create=True intercepts the name at call time.
    """

    def test_sqlite_backend_import_is_not_at_module_level(self):
        """corpus_forge.backends.sqlite should not be imported just by importing ingest.

        We verify by checking that importing corpus_forge.ingest does not pull in
        corpus_forge.backends.sqlite as a side-effect.  (The module may already be
        cached if other tests ran first — in that case we skip the check.)
        """
        # If sqlite module is not yet cached, importing ingest shouldn't add it.
        # Since test order is non-deterministic we cannot guarantee a clean slate,
        # so we use a targeted isolation: remove the sqlite module entry, re-import
        # ingest (which is already loaded), and confirm sqlite stays absent.
        sqlite_key = "corpus_forge.backends.sqlite"
        was_present = sqlite_key in sys.modules

        if was_present:
            # Module already loaded (possibly by a prior test in this session).
            # We can still verify the lazy-import contract by removing the cached
            # entry and checking that a fresh module-level import of ingest does
            # NOT re-import it.
            original = sys.modules.pop(sqlite_key)
            try:
                # Re-evaluate the ingest module's top-level code by removing it too
                ingest_key = "corpus_forge.ingest"
                ingest_mod = sys.modules.pop(ingest_key, None)
                try:
                    importlib.import_module("corpus_forge.ingest")

                    assert sqlite_key not in sys.modules, (
                        "Importing corpus_forge.ingest caused corpus_forge.backends.sqlite "
                        "to be imported at module level. The SQLite branch must use a lazy "
                        "import (inside ingest_once, not at the top of ingest.py)."
                    )
                finally:
                    if ingest_mod is not None:
                        sys.modules[ingest_key] = ingest_mod
            finally:
                sys.modules[sqlite_key] = original
        else:
            # Clean state: verify that importing ingest doesn't pull in sqlite
            ingest_key = "corpus_forge.ingest"
            ingest_mod = sys.modules.pop(ingest_key, None)
            try:
                importlib.import_module("corpus_forge.ingest")

                assert sqlite_key not in sys.modules, (
                    "Importing corpus_forge.ingest caused corpus_forge.backends.sqlite "
                    "to be imported at module level. The SQLite branch must use a lazy "
                    "import (inside ingest_once, not at the top of ingest.py)."
                )
            finally:
                if ingest_mod is not None:
                    sys.modules[ingest_key] = ingest_mod

    def test_sqlite_backend_present_in_sys_modules_after_sqlite_call(self):
        """After calling ingest_once with kind='sqlite', backend.sqlite is in sys.modules."""
        config = _make_sqlite_config()
        sqlite_key = "corpus_forge.backends.sqlite"

        with (
            patch("corpus_forge.ingest.logger"),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None

            ingest_once(config)

        # After the call, the module should be loaded (either it was already
        # present or the lazy import loaded it).
        assert sqlite_key in sys.modules, (
            "corpus_forge.backends.sqlite was not found in sys.modules after "
            "calling ingest_once with kind='sqlite'. "
            "The lazy import inside ingest_once may not be running."
        )
