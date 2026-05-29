"""DR-T5 (RED) — _source_uri_prefix_for logical-name branch.

Contract source: .planning/tdd/tasks.md §DR-T5 and design clause §C1.

Function contract (C1):
  - Read getattr(source, "logical_name", None) first.
  - If truthy (non-empty string): return f"filesystem://logical/{logical_name}".
  - Else preserve existing behavior:
      * If source has .root attr: return f"filesystem://{root.resolve().as_posix()}"
      * Else: return f"{source.name}://{source.identity()}"
  - logical_name is NOT URL-encoded or reformatted — used verbatim.
  - _legacy_source_uri_prefix_for is UNCHANGED: no logical_name branch.

Duck-typing contract (principal decision #2):
  - Tests use SimpleNamespace / inline fake objects, NOT DatasetSourceConfig.
  - This validates the getattr duck-typed path, which is the runtime contract.

RED state: the logical_name branch does not yet exist in _source_uri_prefix_for.
  Tests that pass a truthy logical_name will get the path-based prefix back
  (e.g. "filesystem:///Users/alice/Notes") instead of "filesystem://logical/notes",
  causing AssertionError on the assert_prefix_is lines.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from corpus_forge.ingest import _legacy_source_uri_prefix_for, _source_uri_prefix_for

# ---------------------------------------------------------------------------
# Fake source helpers — duck-typed per principal decision #2
# ---------------------------------------------------------------------------


def _fs_source(root: Path | None, logical_name: str | None = None) -> SimpleNamespace:
    """Return a fake filesystem source with optional logical_name."""
    obj = SimpleNamespace(root=root, logical_name=logical_name)
    return obj


def _fs_source_no_logical(root: Path) -> SimpleNamespace:
    """Return a fake filesystem source that has NO logical_name attribute at all."""
    return SimpleNamespace(root=root)


def _api_source(name: str, identity_val: str) -> SimpleNamespace:
    """Return a fake API source (no .root, no .logical_name)."""

    def identity():
        return identity_val

    return SimpleNamespace(name=name, identity=identity)


# ---------------------------------------------------------------------------
# Happy path: logical_name set → filesystem://logical/<name>
# ---------------------------------------------------------------------------


class TestLogicalNameSet:
    """logical_name wins over root when truthy."""

    def test_logical_name_notes_alice_root(self):
        """Machine A: root=/Users/alice/Notes, logical_name=notes → logical prefix."""
        source = _fs_source(Path("/Users/alice/Notes"), logical_name="notes")
        result = _source_uri_prefix_for(source)
        assert result == "filesystem://logical/notes"

    def test_logical_name_notes_different_root(self):
        """Machine B: root=/data/Notes, same logical_name=notes → same prefix.

        This is the core machine-convergence assertion: two machines with
        divergent paths but the same logical_name must produce identical prefixes.
        """
        source = _fs_source(Path("/data/Notes"), logical_name="notes")
        result = _source_uri_prefix_for(source)
        assert result == "filesystem://logical/notes"

    def test_two_machines_same_logical_name_produces_identical_prefix(self):
        """Explicit two-machine convergence test — both prefixes must be equal."""
        machine_a = _fs_source(Path("/Users/alice/Notes"), logical_name="notes")
        machine_b = _fs_source(Path("/home/bob/Documents/Notes"), logical_name="notes")
        prefix_a = _source_uri_prefix_for(machine_a)
        prefix_b = _source_uri_prefix_for(machine_b)
        assert prefix_a == prefix_b == "filesystem://logical/notes"

    def test_logical_name_with_hyphens(self):
        """Hyphenated names are stored verbatim, not altered."""
        source = _fs_source(Path("/x/y"), logical_name="work-notes")
        assert _source_uri_prefix_for(source) == "filesystem://logical/work-notes"

    def test_logical_name_with_dots(self):
        """Dot-separated names are stored verbatim."""
        source = _fs_source(Path("/x/y"), logical_name="a.b.c")
        assert _source_uri_prefix_for(source) == "filesystem://logical/a.b.c"

    def test_logical_name_with_underscores(self):
        """Underscore names are stored verbatim."""
        source = _fs_source(Path("/x/y"), logical_name="x_y")
        assert _source_uri_prefix_for(source) == "filesystem://logical/x_y"

    def test_logical_name_single_char(self):
        """Single character name is a valid logical_name."""
        source = _fs_source(Path("/x/y"), logical_name="a")
        assert _source_uri_prefix_for(source) == "filesystem://logical/a"

    def test_logical_name_wins_over_root_path(self):
        """When both root and logical_name are present, logical_name wins."""
        source = _fs_source(Path("/very/specific/path/on/machine"), logical_name="vault")
        result = _source_uri_prefix_for(source)
        # Must NOT contain the path
        assert "very" not in result
        assert "specific" not in result
        assert result == "filesystem://logical/vault"

    def test_logical_name_with_root_none(self):
        """logical_name wins even when root is None.

        Pathological case: someone builds a source object dict-style with
        logical_name but no root. logical_name branch runs first so no
        AttributeError from root.resolve() is ever reached.
        """
        source = _fs_source(root=None, logical_name="notes")
        result = _source_uri_prefix_for(source)
        assert result == "filesystem://logical/notes"


# ---------------------------------------------------------------------------
# Back-compat path: logical_name is None → existing path-based prefix
# ---------------------------------------------------------------------------


class TestLogicalNameNoneBackCompat:
    """None logical_name must preserve the existing path-based behavior exactly."""

    def test_none_logical_name_uses_full_path(self):
        """logical_name=None falls through to root.resolve().as_posix()."""
        root = Path("/x/y")
        source = _fs_source(root, logical_name=None)
        result = _source_uri_prefix_for(source)
        assert result == f"filesystem://{root.resolve().as_posix()}"

    def test_no_logical_name_attr_uses_full_path(self):
        """Source object without a logical_name attribute falls through to root path.

        Validates the getattr(source, 'logical_name', None) default=None path.
        """
        root = Path("/a/b/c")
        source = _fs_source_no_logical(root)
        result = _source_uri_prefix_for(source)
        assert result == f"filesystem://{root.resolve().as_posix()}"

    def test_none_logical_name_absolute_path(self):
        """Absolute path is preserved verbatim via resolve().as_posix()."""
        root = Path("/Users/alice/Documents")
        source = _fs_source(root, logical_name=None)
        expected = f"filesystem://{root.resolve().as_posix()}"
        assert _source_uri_prefix_for(source) == expected


# ---------------------------------------------------------------------------
# Defensive: empty string treated as None (fallback to path-based prefix)
# ---------------------------------------------------------------------------


class TestLogicalNameEmptyStringDefensive:
    """Empty string must be treated as None — defensive runtime guard.

    DR-T1/C2 rejects empty string at Pydantic config-load time, so this
    case should never arise in normal operation.  But a corrupt config
    object built via dict() or a patched test fixture might bypass Pydantic,
    so the runtime helper must degrade gracefully.
    """

    def test_empty_string_falls_through_to_path_prefix(self):
        """logical_name='' is falsy → treated as None; path-based prefix used."""
        root = Path("/x/y")
        source = _fs_source(root, logical_name="")
        result = _source_uri_prefix_for(source)
        # Must NOT produce the logical/ prefix
        assert "logical" not in result
        # Must produce the path-based prefix
        assert result == f"filesystem://{root.resolve().as_posix()}"

    def test_empty_string_not_url_encoded_into_logical(self):
        """Specifically: result must not be 'filesystem://logical/'."""
        source = _fs_source(Path("/x/y"), logical_name="")
        result = _source_uri_prefix_for(source)
        assert result != "filesystem://logical/"


# ---------------------------------------------------------------------------
# API source fallback: no root, no logical_name → name://identity()
# ---------------------------------------------------------------------------


class TestApiSourceFallback:
    """Sources with no .root attribute use the name://identity() scheme."""

    def test_api_source_no_logical_name(self):
        """API source falls through to name://identity() when no root or logical_name."""
        source = _api_source("zotero", "user42")
        result = _source_uri_prefix_for(source)
        assert result == "zotero://user42"

    def test_api_source_with_logical_name_set(self):
        """Even an API source uses logical prefix when logical_name is set."""
        source = SimpleNamespace(
            logical_name="my-zotero",
            name="zotero",
            identity=lambda: "user42",
        )
        result = _source_uri_prefix_for(source)
        assert result == "filesystem://logical/my-zotero"


# ---------------------------------------------------------------------------
# Regression: _legacy_source_uri_prefix_for is UNCHANGED
# ---------------------------------------------------------------------------


class TestLegacyHelperUnchanged:
    """_legacy_source_uri_prefix_for must NOT grow a logical_name branch.

    Per C1: legacy reads are keyed off root.name only. logical-name sources
    have no legacy equivalent. Adding a logical branch here would corrupt
    back-compat reads for all existing rows.
    """

    def test_legacy_returns_basename_not_logical_prefix(self):
        """Even with logical_name set, legacy helper returns filesystem://<root.name>."""
        root = Path("/Users/alice/Notes")
        source = _fs_source(root, logical_name="notes")
        result = _legacy_source_uri_prefix_for(source)
        assert result == "filesystem://Notes"
        assert "logical" not in result

    def test_legacy_returns_basename_regardless_of_logical_name(self):
        """Machine B: same logical_name, different root — legacy still uses basename."""
        root = Path("/data/Notes")
        source = _fs_source(root, logical_name="notes")
        result = _legacy_source_uri_prefix_for(source)
        # basename of /data/Notes is "Notes"
        assert result == "filesystem://Notes"

    def test_legacy_none_logical_name_unchanged(self):
        """logical_name=None: legacy returns root.name exactly as before."""
        root = Path("/some/path/vault")
        source = _fs_source(root, logical_name=None)
        result = _legacy_source_uri_prefix_for(source)
        assert result == "filesystem://vault"

    def test_legacy_no_logical_name_attr_unchanged(self):
        """Source without logical_name attribute: legacy unchanged."""
        root = Path("/some/path/vault")
        source = _fs_source_no_logical(root)
        result = _legacy_source_uri_prefix_for(source)
        assert result == "filesystem://vault"

    def test_legacy_api_source_returns_none(self):
        """API source (no .root): legacy returns None as before."""
        source = _api_source("zotero", "user42")
        result = _legacy_source_uri_prefix_for(source)
        assert result is None

    def test_legacy_two_machines_different_prefixes(self):
        """Back-compat proof: legacy DOES NOT converge across different root basenames.

        This documents the deliberate difference between legacy (basename) and
        the new logical_name path. Two sources with the same logical_name but
        different root basenames produce DIFFERENT legacy prefixes.
        """
        source_a = _fs_source(Path("/Users/alice/Notes"), logical_name="notes")
        source_b = _fs_source(Path("/data/work-notes"), logical_name="notes")
        legacy_a = _legacy_source_uri_prefix_for(source_a)
        legacy_b = _legacy_source_uri_prefix_for(source_b)
        # They differ: "filesystem://Notes" vs "filesystem://work-notes"
        assert legacy_a != legacy_b

    def test_legacy_two_machines_same_basename_same_legacy_prefix(self):
        """Back-compat scenario: same basename → same legacy prefix (intended).

        This documents that iCloud-style setups where both machines have the
        same directory basename (e.g. /Users/alice/Notes and /home/bob/Notes)
        happen to share the legacy prefix. This is the pre-existing behavior
        and must remain unchanged.
        """
        source_a = _fs_source(Path("/Users/alice/Notes"), logical_name=None)
        source_b = _fs_source(Path("/home/bob/Notes"), logical_name=None)
        assert _legacy_source_uri_prefix_for(source_a) == _legacy_source_uri_prefix_for(source_b)
        assert _legacy_source_uri_prefix_for(source_a) == "filesystem://Notes"
