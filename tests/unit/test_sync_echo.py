"""Unit tests for EchoSuppressor — the duplicate-write suppression cache."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# The class does not exist yet — these tests must fail red.
from corpus_forge.sync.echo import EchoSuppressor


# ── helpers ──────────────────────────────────────────────────────────────

def _make_suppressor(clock_base: float = 1000.0):
    """Create an EchoSuppressor backed by an injectable monotonic clock."""
    current = clock_base
    def clock():
        return current
    return EchoSuppressor(default_ttl_s=5.0, clock=clock), clock


def _advance(clock, seconds: float):
    """Advance the injectable clock by *seconds*."""
    clock.__wrapped__ = _make_suppressor(0)[1].__code__  # noqa: F841
    # We'll just use a mutable list to mutate the clock value.
    # Actually, simpler: use a dict.
    pass


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def clock_state():
    """Mutable clock state we can advance between operations."""
    return {"t": 1000.0}


def clock_fn(state):
    """Return current time from state dict."""
    return state["t"]


def advance(state, seconds: float):
    """Advance the clock by *seconds*."""
    state["t"] += seconds


# ── Tests ────────────────────────────────────────────────────────────────

class TestEchoSuppressorHappyPath:
    """Happy-path tests: register → was_just_written returns True."""

    def test_register_then_was_just_written_returns_true(self, tmp_path, clock_state):
        """Register a path+hash, then was_just_written should return True."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        p.write_text("hello")
        suppressor.register(p, content_hash="abc123")
        assert suppressor.was_just_written(p, "abc123") is True

    def test_register_with_custom_ttl(self, tmp_path, clock_state):
        """Register with an explicit TTL that overrides the default."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="xyz", ttl_s=30.0)
        assert suppressor.was_just_written(p, "xyz") is True

    def test_register_with_none_ttl_uses_default(self, tmp_path, clock_state):
        """Register with ttl_s=None should use the default_ttl_s."""
        suppressor = EchoSuppressor(default_ttl_s=10.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="ttl_default", ttl_s=None)
        assert suppressor.was_just_written(p, "ttl_default") is True


class TestEchoSuppressorConsumption:
    """was_just_written is a *one-time* match — it clears the entry."""

    def test_second_was_just_written_returns_false(self, tmp_path, clock_state):
        """After a successful was_just_written, the entry is consumed."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="once")
        assert suppressor.was_just_written(p, "once") is True
        # Second call should return False (consumed)
        assert suppressor.was_just_written(p, "once") is False

    def test_was_just_written_does_not_affect_other_entries(self, tmp_path, clock_state):
        """Consuming one entry must not affect other registered entries."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p1 = tmp_path / "doc1.md"
        p2 = tmp_path / "doc2.md"
        suppressor.register(p1, content_hash="hash1")
        suppressor.register(p2, content_hash="hash2")
        assert suppressor.was_just_written(p1, "hash1") is True
        # p2 should still be available
        assert suppressor.was_just_written(p2, "hash2") is True


class TestEchoSuppressorMismatchedHash:
    """Mismatched content_hash → False."""

    def test_wrong_hash_returns_false(self, tmp_path, clock_state):
        """Registering with hash A, querying with hash B → False."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="correct")
        assert suppressor.was_just_written(p, "wrong") is False

    def test_mismatch_does_not_consume_entry(self, tmp_path, clock_state):
        """A mismatched hash should NOT consume the entry."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="correct")
        assert suppressor.was_just_written(p, "wrong") is False
        # Should still be matchable with the correct hash
        assert suppressor.was_just_written(p, "correct") is True


class TestEchoSuppressorTTLExpiry:
    """After TTL elapses, was_just_written → False."""

    def test_was_just_written_returns_false_after_ttl(self, tmp_path, clock_state):
        """When the clock advances past the expiry, was_just_written → False."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="ttl_test")
        # Should match before expiry
        assert suppressor.was_just_written(p, "ttl_test") is True

    def test_expired_entry_not_matched(self, tmp_path, clock_state):
        """Advance clock past TTL → was_just_written returns False (entry expired)."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="expires_soon")
        # Advance past the 5-second TTL
        advance(clock_state, 6.0)
        assert suppressor.was_just_written(p, "expires_soon") is False

    def test_gc_removes_expired_entries(self, tmp_path, clock_state):
        """gc() should evict expired entries from the internal cache."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="will_expire")
        advance(clock_state, 6.0)
        # Before gc, the entry is still in the dict (but expired)
        suppressor.gc()
        # After gc, the entry should be gone entirely
        assert suppressor.was_just_written(p, "will_expire") is False

    def test_gc_preserves_non_expired_entries(self, tmp_path, clock_state):
        """gc() must NOT remove entries that haven't expired."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="still_fresh")
        # Advance only 1 second — not enough to expire
        advance(clock_state, 1.0)
        suppressor.gc()
        assert suppressor.was_just_written(p, "still_fresh") is True

    def test_gc_with_no_argument_uses_clock(self, tmp_path, clock_state):
        """gc() with no argument should use the injectable clock."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="clock_gc")
        advance(clock_state, 6.0)
        suppressor.gc()  # no explicit now — uses clock()
        assert suppressor.was_just_written(p, "clock_gc") is False

    def test_gc_with_explicit_now_argument(self, tmp_path):
        """gc(now=X) should use the provided timestamp instead of the clock."""
        fake_clock = lambda: 2000.0  # never used when gc(now=...) is called
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=fake_clock)
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="explicit_gc")
        # The clock says 2000 (register time), expires_at=2005, we pass now=2006 (after expiry)
        suppressor.gc(now=2006.0)
        assert suppressor.was_just_written(p, "explicit_gc") is False

    def test_gc_with_explicit_now_preserves_fresh(self, tmp_path):
        """gc(now=X) must not evict entries that are still valid at *now*."""
        fake_clock = lambda: 2000.0
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=fake_clock)
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="fresh_at_now")
        # Entries registered at clock 1000 with ttl 5 expire at 1005.
        # gc(now=1003) should NOT evict them.
        suppressor.gc(now=1003.0)
        assert suppressor.was_just_written(p, "fresh_at_now") is True


class TestEchoSuppressorPathNormalization:
    """Keys are str(path.resolve()). Relative/symlinked paths must match."""

    def test_relative_path_matches_resolved_path(self, tmp_path, clock_state):
        """Register with absolute path, query with relative path → True."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        p.write_text("content")
        suppressor.register(p, content_hash="rel_test")
        # Query with a relative path that resolves to the same file
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            relative = Path("doc.md")
            assert suppressor.was_just_written(relative, "rel_test") is True
        finally:
            os.chdir(cwd)

    def test_symlinked_path_matches_original(self, tmp_path, clock_state):
        """Register with original path, query with symlink → True."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        original = tmp_path / "doc.md"
        symlink = tmp_path / "link.md"
        original.write_text("content")
        symlink.symlink_to(original)
        suppressor.register(original, content_hash="symlink_test")
        assert suppressor.was_just_written(symlink, "symlink_test") is True

    def test_resolve_normalizes_dot_components(self, tmp_path, clock_state):
        """Paths with ./ or ../ should resolve to the same key."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "sub" / "doc.md"
        p.parent.mkdir(parents=True)
        p.write_text("content")
        suppressor.register(p, content_hash="normalize_test")
        # Query with a path containing ..
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            relative = Path("./sub/../sub/doc.md")
            assert suppressor.was_just_written(relative, "normalize_test") is True
        finally:
            os.chdir(cwd)


class TestEchoSuppressorEdgeCases:
    """Boundary conditions and edge cases."""

    def test_zero_ttl_expires_immediately(self, tmp_path, clock_state):
        """Register with ttl_s=0 should expire immediately."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="zero_ttl", ttl_s=0.0)
        # Even before advancing the clock, TTL=0 means expires_at == now,
        # which should already be expired.
        assert suppressor.was_just_written(p, "zero_ttl") is False

    def test_multiple_registers_same_path_overwrite(self, tmp_path, clock_state):
        """Re-registering the same path should update the hash and TTL."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="first")
        suppressor.register(p, content_hash="second")
        # First hash should be gone
        assert suppressor.was_just_written(p, "first") is False
        # Second hash should match
        assert suppressor.was_just_written(p, "second") is True

    def test_gc_with_no_entries_does_not_raise(self, tmp_path):
        """gc() on an empty suppressor should be a no-op, not raise."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: 1000.0)
        suppressor.gc()  # Should not raise

    def test_gc_with_all_expired_removes_all(self, tmp_path, clock_state):
        """When all entries are expired, gc() removes every one."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p1 = tmp_path / "a.md"
        p2 = tmp_path / "b.md"
        p3 = tmp_path / "c.md"
        suppressor.register(p1, content_hash="a")
        suppressor.register(p2, content_hash="b")
        suppressor.register(p3, content_hash="c")
        advance(clock_state, 10.0)
        suppressor.gc()
        assert suppressor.was_just_written(p1, "a") is False
        assert suppressor.was_just_written(p2, "b") is False
        assert suppressor.was_just_written(p3, "c") is False

    def test_default_ttl_constructor_applied(self, tmp_path, clock_state):
        """The default_ttl_s parameter controls the TTL for registrations without explicit ttl_s."""
        suppressor = EchoSuppressor(default_ttl_s=2.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="short_ttl")
        advance(clock_state, 1.5)
        assert suppressor.was_just_written(p, "short_ttl") is True
        advance(clock_state, 1.0)
        assert suppressor.was_just_written(p, "short_ttl") is False

    def test_injectable_clock_is_used(self, tmp_path):
        """The clock parameter controls all time reads."""
        custom_time = {"t": 5000.0}
        suppressor = EchoSuppressor(
            default_ttl_s=5.0,
            clock=lambda: custom_time["t"],
        )
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="clocked")
        custom_time["t"] = 5006.0  # past expiry
        assert suppressor.was_just_written(p, "clocked") is False

    def test_non_string_path_raises_or_handles(self, tmp_path, clock_state):
        """Passing a non-Path-like object should raise TypeError or ValueError."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        # Path object is fine
        suppressor.register(p, content_hash="ok")
        # Non-path argument — should raise
        with pytest.raises((TypeError, ValueError)):
            suppressor.register("not_a_path", content_hash="bad")

    def test_non_string_content_hash_raises(self, tmp_path, clock_state):
        """Passing a non-string content_hash should raise TypeError or ValueError."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        with pytest.raises((TypeError, ValueError)):
            suppressor.register(p, content_hash=12345)

    def test_empty_content_hash(self, tmp_path, clock_state):
        """Empty string content_hash should be accepted (valid hash)."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "doc.md"
        suppressor.register(p, content_hash="")
        assert suppressor.was_just_written(p, "") is True

    def test_unicode_path(self, tmp_path, clock_state):
        """Paths with non-ASCII characters should resolve correctly."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        p = tmp_path / "café.md"
        p.write_text("content")
        suppressor.register(p, content_hash="unicode_path")
        # Query with the same path object
        assert suppressor.was_just_written(p, "unicode_path") is True

    def test_large_number_of_entries(self, tmp_path, clock_state):
        """Suppressor should handle many entries without issues."""
        suppressor = EchoSuppressor(default_ttl_s=5.0, clock=lambda: clock_fn(clock_state))
        paths = []
        for i in range(200):
            p = tmp_path / f"file_{i:04d}.md"
            p.write_text(f"content {i}")
            suppressor.register(p, content_hash=f"hash_{i}")
            paths.append(p)
        # All should match
        for i, p in enumerate(paths[:50]):
            assert suppressor.was_just_written(p, f"hash_{i}") is True
        # Consume all and verify second call returns False
        for i, p in enumerate(paths[:50]):
            assert suppressor.was_just_written(p, f"hash_{i}") is False
