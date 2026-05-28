"""Unit tests pinning the alembic revision chain head to 0017_ingest_runs.

SR-T1 (feat/ingest-resumability).

These tests assert:
1. The revision file for 0017_ingest_runs exists at the expected path.
2. The module declares ``revision = "0017_ingest_runs"`` and
   ``down_revision = "0016_chunk_provenance"``.
3. The revision id fits in alembic's VARCHAR(32) ``version_num`` column.
4. alembic's ScriptDirectory reports ``0017_ingest_runs`` as the single
   current head revision (i.e., nothing depends on 0016 other than 0017,
   and 0017 has no successor).
5. The _expected_head_revision() helper used by
   ``test_apply_migrations_uses_alembic.py`` resolves to ``0017_ingest_runs``.
6. The chain from 0001 to 0017 is linear (no branches or orphans).

All tests FAIL at RED time because the file
``corpus_forge/alembic/versions/0017_ingest_runs.py`` does not yet exist.
Failure modes:
- Tests 1-3, 6: ModuleNotFoundError / FileNotFoundError
- Test 4-5: alembic CommandError or assertion on wrong head string
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_VERSIONS_DIR = _REPO_ROOT / "corpus_forge" / "alembic" / "versions"

_TARGET_REVISION = "0017_ingest_runs"
_PRIOR_REVISION = "0016_chunk_provenance"
_REVISION_FILE = _VERSIONS_DIR / "0017_ingest_runs.py"
_ALEMBIC_VERSION_NUM_MAX_LEN = 32  # alembic_version.version_num is VARCHAR(32)

# ---------------------------------------------------------------------------
# Helper: load a revision module by path (alembic filenames start with digits)
# ---------------------------------------------------------------------------


def _load_revision_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"_revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None, f"Could not create module spec for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: File presence
# ---------------------------------------------------------------------------


def test_revision_file_exists() -> None:
    """0017_ingest_runs.py must exist at the expected versions path.

    Fails RED with FileNotFoundError / AssertionError while the coder hasn't
    created the file yet.
    """
    assert _REVISION_FILE.exists(), (
        f"Migration file not found at {_REVISION_FILE}. "
        "SR-G1 coder must create corpus_forge/alembic/versions/0017_ingest_runs.py."
    )
    assert _REVISION_FILE.is_file(), f"{_REVISION_FILE} exists but is not a regular file."


# ---------------------------------------------------------------------------
# Test 2: Revision chain attributes
# ---------------------------------------------------------------------------


def test_revision_id_is_correct() -> None:
    """Module must declare revision = '0017_ingest_runs'."""
    mod = _load_revision_module(_REVISION_FILE)
    assert mod.revision == _TARGET_REVISION, (
        f"Expected revision={_TARGET_REVISION!r}, got {mod.revision!r}"
    )


def test_down_revision_points_at_0016() -> None:
    """Module must declare down_revision = '0016_chunk_provenance'.

    0016 is the direct predecessor; any other value breaks the alembic
    chain and makes ``corpus-forge migrate`` fail on every deployment.
    """
    mod = _load_revision_module(_REVISION_FILE)
    assert mod.down_revision == _PRIOR_REVISION, (
        f"Expected down_revision={_PRIOR_REVISION!r}, got {mod.down_revision!r}. "
        "Rebase drift would break the linear migration chain."
    )


# ---------------------------------------------------------------------------
# Test 3: Revision id length guard (VARCHAR(32) regression)
# ---------------------------------------------------------------------------


def test_revision_id_fits_varchar32() -> None:
    """Revision id must be <= 32 characters.

    Context: the original 0015 id was 37 chars and caused
    psycopg.errors.StringDataRightTruncation on every ``alembic upgrade``
    because the final UPDATE alembic_version SET version_num tried to store
    37 chars in a VARCHAR(32) column and rolled back the whole migration.
    The regression was caught by test_every_revision_id_fits_alembic_version_num_column
    in test_alembic_revision_chain.py; this test redundantly pins the same
    invariant for the 0017 file specifically.
    """
    mod = _load_revision_module(_REVISION_FILE)
    rev = mod.revision
    assert len(rev) <= _ALEMBIC_VERSION_NUM_MAX_LEN, (
        f"revision id {rev!r} is {len(rev)} chars; must be <= {_ALEMBIC_VERSION_NUM_MAX_LEN} "
        "to fit alembic_version.version_num VARCHAR(32). "
        "See the long note on revision 0015 for regression context."
    )


# ---------------------------------------------------------------------------
# Test 4: ScriptDirectory reports 0017 as the single head
# ---------------------------------------------------------------------------


def test_alembic_script_directory_head_is_0017() -> None:
    """alembic.script.ScriptDirectory must report exactly one head: 0017_ingest_runs.

    This test uses the same ScriptDirectory that ``apply_migrations`` and
    ``_expected_head_revision()`` use, so if this passes, the integration
    test head assertion in test_apply_migrations_uses_alembic.py will too.

    Fails RED: alembic can't locate 0017_ingest_runs, so get_current_head()
    returns 0016_chunk_provenance (or raises CommandError).
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert _ALEMBIC_INI.exists(), (
        f"alembic.ini not found at {_ALEMBIC_INI}; cannot resolve head revision."
    )

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )

    script = ScriptDirectory.from_config(cfg)
    current_head = script.get_current_head()

    assert current_head == _TARGET_REVISION, (
        f"alembic ScriptDirectory reports head={current_head!r}; "
        f"expected {_TARGET_REVISION!r}. "
        "The 0017_ingest_runs.py file must exist and chain onto 0016 before this passes."
    )


# ---------------------------------------------------------------------------
# Test 5: _expected_head_revision() helper agrees with 0017
# ---------------------------------------------------------------------------


def test_expected_head_revision_helper_returns_0017() -> None:
    """The _expected_head_revision() helper used by test_apply_migrations_uses_alembic.py
    must resolve to '0017_ingest_runs'.

    This pins the head used by the apply_migrations integration tests so that
    a coder who only runs the unit tier can see immediately which string they
    need to land.
    """
    from alembic.script import ScriptDirectory

    from corpus_forge.schema.migrate import _build_alembic_config

    head = ScriptDirectory.from_config(_build_alembic_config()).get_current_head()
    assert head == _TARGET_REVISION, (
        f"_expected_head_revision() helper returned {head!r}; "
        f"expected {_TARGET_REVISION!r}. "
        "corpus_forge.schema.migrate._build_alembic_config must point at the "
        "versions directory that contains 0017_ingest_runs.py."
    )


# ---------------------------------------------------------------------------
# Test 6: Full chain linearity (0001 → 0017 with no gaps or branches)
# ---------------------------------------------------------------------------


def test_revision_chain_is_linear_through_0017() -> None:
    """The alembic revision chain from root to 0017 must be strictly linear.

    Walks the ScriptDirectory's revision map and asserts:
    - Exactly one root (down_revision is None).
    - Exactly one head (no revision has two successors).
    - No duplicate revision ids.
    - The head equals 0017_ingest_runs.
    - Every non-root down_revision references a known revision id.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert _ALEMBIC_INI.exists()

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )

    script = ScriptDirectory.from_config(cfg)

    # Collect all revisions from the ScriptDirectory
    all_revisions = list(script.walk_revisions())
    if not all_revisions:
        pytest.skip("No revisions found — cannot validate chain linearity.")

    revision_ids = [rev.revision for rev in all_revisions]
    down_revisions = [
        rev.down_revision for rev in all_revisions
    ]  # None or str (not tuple for linear chains)

    # No duplicate revision ids
    assert len(revision_ids) == len(set(revision_ids)), (
        f"Duplicate revision ids detected: {revision_ids}"
    )

    # Normalize: down_revision may be a tuple for branching chains.
    # We expect a linear chain, so any tuple with len > 1 is a failure.
    for rev in all_revisions:
        dr = rev.down_revision
        if isinstance(dr, (list, tuple)):
            assert len(dr) <= 1, (
                f"Revision {rev.revision!r} has multiple down_revisions {dr!r}; "
                "the chain must be strictly linear (no merge revisions)."
            )

    # Flatten down_revisions to scalars
    {(dr[0] if isinstance(dr, (list, tuple)) and dr else dr) for dr in down_revisions}

    # Exactly one root
    roots = [rev.revision for rev in all_revisions if rev.down_revision is None]
    assert len(roots) == 1, (
        f"Expected exactly one root revision (down_revision=None), found: {roots}"
    )

    # Exactly one head — reported by alembic
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly one head, alembic reports {len(heads)}: {heads}. "
        "Multiple heads mean a branch was introduced."
    )
    assert heads[0] == _TARGET_REVISION, f"Head is {heads[0]!r}, expected {_TARGET_REVISION!r}."

    # Every non-root down_revision references a known revision
    revision_id_set = set(revision_ids)
    for rev in all_revisions:
        dr = rev.down_revision
        if dr is None:
            continue
        if isinstance(dr, (list, tuple)):
            for d in dr:
                if d is not None:
                    assert d in revision_id_set, (
                        f"Revision {rev.revision!r} has down_revision {d!r} "
                        "which is not in the known revision set."
                    )
        else:
            assert dr in revision_id_set, (
                f"Revision {rev.revision!r} has down_revision {dr!r} "
                "which is not in the known revision set."
            )
