"""B-13 red suite: tests for SQLite backend wiring in embed.py.

Covers:
- kind=="sqlite" dispatches to SQLiteBackend with path=dsn, schema=schema
- kind=="postgres" still dispatches to PostgresBackend with dsn=dsn, schema=schema
- Unknown kind raises ValueError containing the kind name
- Lazy-import contract for the SQLite branch
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.embed import backfill_embedder


def _make_embedder_config(name: str = "test-embedder"):
    """Return a minimal mock embedder config."""
    ec = MagicMock()
    ec.name = name
    ec.provider = "sentence_transformers"
    ec.model_id = "test-model"
    ec.dimension = 384
    ec.normalize = True
    ec.distance = "cosine"
    ec.active = True
    ec.batch_size = 32
    ec.device = "auto"
    ec.api_key_env = "OPENAI_API_KEY"
    return ec


def _make_sqlite_config(embedder_name: str = "test-embedder"):
    """Return a minimal mock Config with kind='sqlite'."""
    config = MagicMock()
    config.backend.kind = "sqlite"
    config.backend.dsn = "~/Library/Application Support/corpus-forge/corpus.db"
    config.backend.schema = "corpus"
    config.embedders = [_make_embedder_config(embedder_name)]
    config.datasets = []
    return config


def _make_postgres_config(embedder_name: str = "test-embedder"):
    """Return a minimal mock Config with kind='postgres'."""
    config = MagicMock()
    config.backend.kind = "postgres"
    config.backend.dsn = "postgresql://user:pass@localhost/db"
    config.backend.schema = "corpus"
    config.embedders = [_make_embedder_config(embedder_name)]
    config.datasets = []
    return config


class TestBackfillEmbedderSQLiteDispatch:
    """backfill_embedder dispatches to SQLiteBackend when kind=='sqlite'."""

    def test_sqlite_instantiates_sqlite_backend(self):
        """SQLiteBackend is constructed when kind=='sqlite'."""
        config = _make_sqlite_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        mock_cls.assert_called_once()

    def test_sqlite_backend_receives_path_kwarg(self):
        """SQLiteBackend constructor receives path= (not dsn=) set to config.backend.dsn."""
        config = _make_sqlite_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

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

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        _kwargs = mock_cls.call_args.kwargs
        assert "schema" in _kwargs, (
            f"SQLiteBackend must be constructed with schema= keyword arg; got kwargs: {_kwargs}"
        )
        assert _kwargs["schema"] == "corpus"

    def test_sqlite_backend_does_not_receive_dsn_kwarg(self):
        """SQLiteBackend must NOT receive dsn= (that is the Postgres arg)."""
        config = _make_sqlite_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        _kwargs = mock_cls.call_args.kwargs
        assert "dsn" not in _kwargs, (
            "SQLiteBackend must not receive dsn= kwarg; dsn= is for PostgresBackend. "
            f"Got kwargs: {_kwargs}"
        )

    def test_migrate_called_once_on_sqlite_backend(self):
        """migrate() is called exactly once on the SQLiteBackend instance."""
        config = _make_sqlite_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        mock_instance.migrate.assert_called_once()

    def test_sqlite_dispatch_does_not_call_postgres_backend(self):
        """When kind=='sqlite', PostgresBackend must not be constructed."""
        config = _make_sqlite_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_sqlite_cls,
            patch("corpus_forge.embed.PostgresBackend") as mock_pg_cls,
        ):
            mock_instance = MagicMock()
            mock_sqlite_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        mock_pg_cls.assert_not_called()


class TestBackfillEmbedderPostgresRegressionWiring:
    """Regression: kind=='postgres' still wires to PostgresBackend correctly."""

    def test_postgres_instantiates_postgres_backend(self):
        """PostgresBackend is constructed when kind=='postgres' (no regression)."""
        config = _make_postgres_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.embed.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        mock_cls.assert_called_once()

    def test_postgres_backend_receives_dsn_kwarg(self):
        """PostgresBackend constructor receives dsn= (not path=)."""
        config = _make_postgres_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.embed.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        _kwargs = mock_cls.call_args.kwargs
        assert "dsn" in _kwargs, (
            f"PostgresBackend must be constructed with dsn= keyword; got {_kwargs}"
        )
        assert _kwargs["dsn"] == config.backend.dsn

    def test_postgres_backend_receives_schema_kwarg(self):
        """PostgresBackend constructor receives schema= set to config.backend.schema."""
        config = _make_postgres_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.embed.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        _kwargs = mock_cls.call_args.kwargs
        assert "schema" in _kwargs
        assert _kwargs["schema"] == "corpus"

    def test_postgres_migrate_is_called_once(self):
        """migrate() is still called exactly once on the PostgresBackend instance.

        Note: embed.py does not call migrate() in the current implementation
        (it delegates to the backend's constructor or the coder must add it).
        This test documents the expected post-B-13 behavior — it will fail
        until the coder adds migrate() to the sqlite branch AND the postgres branch.
        """
        config = _make_postgres_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.embed.PostgresBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        mock_instance.migrate.assert_called_once()


class TestBackfillEmbedderUnknownKindRaises:
    """Unknown backend kind raises ValueError with informative message."""

    def test_unknown_kind_raises_value_error(self):
        """A completely unknown kind (e.g. 'duckdb') raises ValueError."""
        config = MagicMock()
        config.backend.kind = "duckdb"
        config.backend.dsn = "duckdb://memory"
        config.backend.schema = "corpus"
        config.embedders = [_make_embedder_config()]
        config.datasets = []

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            pytest.raises(ValueError),
        ):
            backfill_embedder("test-embedder")

    def test_unknown_kind_error_message_contains_kind_name(self):
        """ValueError message must contain the unknown kind for debuggability."""
        config = MagicMock()
        config.backend.kind = "duckdb"
        config.backend.dsn = "duckdb://memory"
        config.backend.schema = "corpus"
        config.embedders = [_make_embedder_config()]
        config.datasets = []

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            pytest.raises(ValueError, match="duckdb"),
        ):
            backfill_embedder("test-embedder")

    def test_nonsense_kind_raises_value_error(self):
        """A nonsense kind string raises ValueError."""
        config = MagicMock()
        config.backend.kind = "notarealbackend"
        config.backend.dsn = "somedsn"
        config.backend.schema = "corpus"
        config.embedders = [_make_embedder_config()]
        config.datasets = []

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            pytest.raises(ValueError, match="notarealbackend"),
        ):
            backfill_embedder("test-embedder")


class TestBackfillEmbedderSQLiteLazyImport:
    """The SQLiteBackend import in embed.py must be lazy (inside the function).

    The current embed.py does NOT import SQLiteBackend at all (it raises ValueError
    for sqlite). After B-13, the import must be lazy (inside backfill_embedder).

    We also document the eager postgres import issue: embed.py currently imports
    PostgresBackend at module level (line 5). B-13's spirit is to make the SQLite
    import lazy. The board notes the postgres import as scope-eligible but not
    required for B-13.
    """

    def test_sqlite_backend_present_in_sys_modules_after_sqlite_call(self):
        """After calling backfill_embedder with kind='sqlite', backends.sqlite is in sys.modules."""
        config = _make_sqlite_config()

        mock_embedder_obj = MagicMock()
        mock_embedder_obj.name = "test-embedder"

        sqlite_key = "corpus_forge.backends.sqlite"

        with (
            patch("corpus_forge.config.Config.load", return_value=config),
            patch("corpus_forge.embed.registry.register", return_value=mock_embedder_obj),
            patch("corpus_forge.backends.sqlite.SQLiteBackend") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_instance.migrate.return_value = None
            mock_instance.register_embedder.return_value = 1
            mock_instance.chunks_missing_embedding.return_value = []

            backfill_embedder("test-embedder")

        assert sqlite_key in sys.modules, (
            "corpus_forge.backends.sqlite was not found in sys.modules after calling "
            "backfill_embedder with kind='sqlite'. The lazy import inside the function "
            "may not be running."
        )

    def test_importing_embed_module_does_not_eagerly_import_sqlite_backend(self):
        """Importing corpus_forge.embed should not pull in corpus_forge.backends.sqlite.

        This test verifies that the SQLite import is lazy (inside the function).
        Since embed.py currently does not import SQLiteBackend at all, this should
        pass today too — it pins the contract that even after adding the lazy import,
        it must not become a module-level import.
        """
        sqlite_key = "corpus_forge.backends.sqlite"
        embed_key = "corpus_forge.embed"

        # Save current module state
        saved_sqlite = sys.modules.pop(sqlite_key, None)
        saved_embed = sys.modules.pop(embed_key, None)

        try:
            # Fresh import of embed module only
            importlib.import_module("corpus_forge.embed")

            assert sqlite_key not in sys.modules, (
                "Importing corpus_forge.embed caused corpus_forge.backends.sqlite to be "
                "imported at module level. The SQLite branch must use a lazy import "
                "(inside backfill_embedder, not at the top of embed.py)."
            )
        finally:
            # Restore module cache
            if saved_embed is not None:
                sys.modules[embed_key] = saved_embed
            if saved_sqlite is not None:
                sys.modules[sqlite_key] = saved_sqlite
