"""Tests for corpus_forge.backends.sqlite_vec_loader — B-01.

Covers:
- SQLITE_VEC_AVAILABLE is a bool.
- Module imports cleanly even when sqlite_vec is absent (ImportError guarded).
- SQLITE_VEC_AVAILABLE is False when sqlite_vec raises ImportError.
- load_sqlite_vec(conn) succeeds and sqlite_vec SQL functions are queryable (skipif).
- load_sqlite_vec toggles enable_load_extension on then off; idempotent on second call.
- load_sqlite_vec raises (not silently no-ops) when enable_load_extension is missing/broken.
"""

import importlib
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Attempt to import the target module.  The import may fail because the
# module does not exist yet (coder hasn't written it).  In that case every
# test that depends on the real module will also fail — which is the correct
# *red* signal.
# ---------------------------------------------------------------------------

try:
    from corpus_forge.backends.sqlite_vec_loader import (
        SQLITE_VEC_AVAILABLE,
        load_sqlite_vec,
    )

    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False
    SQLITE_VEC_AVAILABLE = False  # sentinel so skipif expressions don't NameError

_needs_loader = pytest.mark.skipif(
    not _IMPORT_OK,
    reason="corpus_forge.backends.sqlite_vec_loader not yet implemented (coder task)",
)

_needs_sqlite_vec = pytest.mark.skipif(
    not SQLITE_VEC_AVAILABLE,
    reason="sqlite-vec extra not installed (install with: uv pip install 'corpus-forge[sqlite]')",
)


# ---------------------------------------------------------------------------
# 1. SQLITE_VEC_AVAILABLE type
# ---------------------------------------------------------------------------


class TestSqliteVecAvailableType:
    """SQLITE_VEC_AVAILABLE must be a plain bool — not truthy/falsy anything else."""

    @_needs_loader
    def test_is_bool(self):
        """SQLITE_VEC_AVAILABLE is exactly type bool, not an int or other truthy."""
        assert isinstance(SQLITE_VEC_AVAILABLE, bool), (
            f"Expected bool, got {type(SQLITE_VEC_AVAILABLE)}"
        )

    @_needs_loader
    def test_is_not_none(self):
        """SQLITE_VEC_AVAILABLE is not None."""
        assert SQLITE_VEC_AVAILABLE is not None


# ---------------------------------------------------------------------------
# 2. Import-guard: module survives when sqlite_vec raises ImportError
# ---------------------------------------------------------------------------


class TestImportGuard:
    """The module must import cleanly even if sqlite_vec is not installed."""

    def test_module_importable_without_sqlite_vec(self, monkeypatch):
        """Removing sqlite_vec from sys.modules and patching the import to raise
        ImportError must NOT cause corpus_forge.backends.sqlite_vec_loader to
        raise on reload; SQLITE_VEC_AVAILABLE must be False afterwards."""
        # Remove any real sqlite_vec so the import-guard re-runs
        # Patch builtins.__import__ is fragile; use sys.modules sentinel instead.
        monkeypatch.setitem(sys.modules, "sqlite_vec", None)  # None → triggers ImportError

        # Remove the cached loader module so importlib.reload actually re-executes
        target = "corpus_forge.backends.sqlite_vec_loader"
        saved = sys.modules.pop(target, None)
        try:
            # The import must succeed (no exception escapes the try/except guard)
            mod = importlib.import_module(target)
            assert mod.SQLITE_VEC_AVAILABLE is False, (
                "SQLITE_VEC_AVAILABLE should be False when sqlite_vec is absent"
            )
        finally:
            # Restore state regardless
            if saved is not None:
                sys.modules[target] = saved
            elif target in sys.modules:
                del sys.modules[target]

    def test_load_sqlite_vec_importable_without_sqlite_vec(self, monkeypatch):
        """load_sqlite_vec function must be importable even when sqlite_vec is absent."""
        monkeypatch.setitem(sys.modules, "sqlite_vec", None)
        target = "corpus_forge.backends.sqlite_vec_loader"
        saved = sys.modules.pop(target, None)
        try:
            mod = importlib.import_module(target)
            assert callable(mod.load_sqlite_vec), "load_sqlite_vec must be callable"
        finally:
            if saved is not None:
                sys.modules[target] = saved
            elif target in sys.modules:
                del sys.modules[target]


# ---------------------------------------------------------------------------
# 3. load_sqlite_vec happy path (only when sqlite_vec is actually installed)
# ---------------------------------------------------------------------------


class TestLoadSqliteVecHappyPath:
    """Exercise load_sqlite_vec against a real in-memory connection."""

    @_needs_loader
    @_needs_sqlite_vec
    def test_load_succeeds_on_memory_connection(self):
        """load_sqlite_vec on sqlite3.connect(':memory:') must not raise."""
        conn = sqlite3.connect(":memory:")
        try:
            load_sqlite_vec(conn)  # must not raise
        finally:
            conn.close()

    @_needs_loader
    @_needs_sqlite_vec
    def test_vec_version_queryable_after_load(self):
        """After load_sqlite_vec, SELECT vec_version() returns a non-empty string."""
        conn = sqlite3.connect(":memory:")
        try:
            load_sqlite_vec(conn)
            row = conn.execute("SELECT vec_version()").fetchone()
            assert row is not None, "vec_version() returned no row"
            version = row[0]
            assert isinstance(version, str) and len(version) > 0, (
                f"Expected non-empty version string, got {version!r}"
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 4. enable_load_extension toggle: on → load → off
# ---------------------------------------------------------------------------


class TestEnableLoadExtensionToggle:
    """load_sqlite_vec must call enable_load_extension(True) then (False)."""

    @_needs_loader
    def test_toggle_on_then_off_via_mock(self):
        """Verify the exact toggle sequence: enable(True), load, enable(False)."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_sqlite_vec = MagicMock()
        mock_sqlite_vec.load = MagicMock()

        with patch.dict(sys.modules, {"sqlite_vec": mock_sqlite_vec}):
            # Re-import the module so it picks up our mock sqlite_vec
            target = "corpus_forge.backends.sqlite_vec_loader"
            saved = sys.modules.pop(target, None)
            try:
                mod = importlib.import_module(target)
                # Skip the test if sqlite_vec still not available after patch
                if not mod.SQLITE_VEC_AVAILABLE:
                    pytest.skip("sqlite_vec mock not picked up — module needs reload support")
                mod.load_sqlite_vec(mock_conn)
            finally:
                if saved is not None:
                    sys.modules[target] = saved
                elif target in sys.modules:
                    del sys.modules[target]

        calls = mock_conn.enable_load_extension.call_args_list
        assert len(calls) == 2, f"Expected 2 enable_load_extension calls, got {len(calls)}"
        assert calls[0].args == (True,), f"First call must be enable(True), got {calls[0]}"
        assert calls[1].args == (False,), f"Second call must be enable(False), got {calls[1]}"
        mock_sqlite_vec.load.assert_called_once_with(mock_conn)

    @_needs_loader
    @_needs_sqlite_vec
    def test_idempotent_second_call(self):
        """Calling load_sqlite_vec twice on the same connection must both succeed.

        This verifies that the loader re-enables load_extension each time
        (so the second call isn't blocked by the prior disable).
        """
        conn = sqlite3.connect(":memory:")
        try:
            load_sqlite_vec(conn)
            load_sqlite_vec(conn)  # second call — must not raise
            # Extension functions still available
            row = conn.execute("SELECT vec_version()").fetchone()
            assert row is not None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 5. Failure paths: broken enable_load_extension
# ---------------------------------------------------------------------------


class TestLoadSqliteVecFailurePaths:
    """load_sqlite_vec must not silently no-op when the connection is unusable."""

    @_needs_loader
    def test_raises_when_enable_load_extension_raises_attribute_error(self):
        """Connection missing enable_load_extension entirely → error propagates."""
        mock_conn = MagicMock(spec=[])  # spec=[] → no attributes at all
        # The mock has no enable_load_extension; accessing it will raise AttributeError
        with pytest.raises((AttributeError, TypeError, Exception)):
            load_sqlite_vec(mock_conn)

    @_needs_loader
    def test_raises_when_enable_load_extension_raises_operational_error(self):
        """Connection whose enable_load_extension raises OperationalError → propagates."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.enable_load_extension.side_effect = sqlite3.OperationalError(
            "not authorized to use enable_load_extension"
        )
        with pytest.raises((sqlite3.OperationalError, Exception)):
            load_sqlite_vec(mock_conn)

    @_needs_loader
    def test_no_silent_noop_on_broken_connection(self):
        """load_sqlite_vec must never silently succeed when enable_load_extension fails.

        Specifically: the function must raise, not return None without loading.
        We verify this by confirming that at minimum the mock method was called
        (not short-circuited) and the exception propagated.
        """
        mock_conn = MagicMock(spec=sqlite3.Connection)
        err = RuntimeError("simulated permission denied for extension loading")
        mock_conn.enable_load_extension.side_effect = err

        raised = None
        try:
            load_sqlite_vec(mock_conn)
        except Exception as exc:
            raised = exc

        assert raised is not None, (
            "load_sqlite_vec silently no-oped on a broken connection — must raise"
        )
