"""Unit tests pinning 0018_model_telemetry's place in the alembic chain.

rfc-fleet-1 (model telemetry foundation) added 0018_model_telemetry on top
of 0017_ingest_runs.  Fleet-2 (rfc-fleet-2-distributed-embedding) then
stacked 0019_embed_claims on top of 0018, so 0018 is no longer the head —
the head identity is pinned in ``test_alembic_head_pins_0019.py``.  These
tests pin the still-true invariants for 0018:

1. The revision file for 0018_model_telemetry exists at the expected path.
2. The module declares ``revision = "0018_model_telemetry"`` and
   ``down_revision = "0017_ingest_runs"``.
3. The revision id fits in alembic's VARCHAR(32) ``version_num`` column.
4. alembic's ScriptDirectory can resolve 0018 and it chains onto 0017.
5. 0018 has exactly one successor (0019_embed_claims) — chain stayed linear.
6. The overall chain has one root and one head (head identity in 0019 test).
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

_TARGET_REVISION = "0018_model_telemetry"
_PRIOR_REVISION = "0017_ingest_runs"
_REVISION_FILE = _VERSIONS_DIR / "0018_model_telemetry.py"
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
    """0018_model_telemetry.py must exist at the expected versions path.

    Fails RED with FileNotFoundError / AssertionError while the coder hasn't
    created the file yet.
    """
    assert _REVISION_FILE.exists(), (
        f"Migration file not found at {_REVISION_FILE}. "
        "The coder must create corpus_forge/alembic/versions/0018_model_telemetry.py."
    )
    assert _REVISION_FILE.is_file(), f"{_REVISION_FILE} exists but is not a regular file."


# ---------------------------------------------------------------------------
# Test 2: Revision chain attributes
# ---------------------------------------------------------------------------


def test_revision_id_is_correct() -> None:
    """Module must declare revision = '0018_model_telemetry'."""
    mod = _load_revision_module(_REVISION_FILE)
    assert mod.revision == _TARGET_REVISION, (
        f"Expected revision={_TARGET_REVISION!r}, got {mod.revision!r}"
    )


def test_down_revision_points_at_0017() -> None:
    """Module must declare down_revision = '0017_ingest_runs'.

    0017 is the direct predecessor; any other value breaks the alembic
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
    invariant for the 0018 file specifically.
    """
    mod = _load_revision_module(_REVISION_FILE)
    rev = mod.revision
    assert len(rev) <= _ALEMBIC_VERSION_NUM_MAX_LEN, (
        f"revision id {rev!r} is {len(rev)} chars; must be <= {_ALEMBIC_VERSION_NUM_MAX_LEN} "
        "to fit alembic_version.version_num VARCHAR(32). "
        "See the long note on revision 0015 for regression context."
    )


# ---------------------------------------------------------------------------
# Test 4: ScriptDirectory reports 0018 as the single head
# ---------------------------------------------------------------------------


def test_alembic_script_directory_knows_0018() -> None:
    """alembic.script.ScriptDirectory must know about 0018_model_telemetry.

    0018 is no longer the head (fleet-2's 0019_embed_claims stacks on top —
    see test_alembic_head_pins_0019.py), but it must remain a resolvable
    revision in the chain that ``apply_migrations`` walks.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert _ALEMBIC_INI.exists(), (
        f"alembic.ini not found at {_ALEMBIC_INI}; cannot resolve revisions."
    )

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )

    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(_TARGET_REVISION)
    assert rev is not None and rev.revision == _TARGET_REVISION, (
        f"alembic ScriptDirectory cannot resolve {_TARGET_REVISION!r}; "
        "the 0018_model_telemetry.py file must exist and chain onto 0017."
    )
    assert rev.down_revision == _PRIOR_REVISION


# ---------------------------------------------------------------------------
# Test 5: 0018 has exactly one successor (0019), i.e. it is no longer the head
# ---------------------------------------------------------------------------


def test_0018_has_single_successor() -> None:
    """0018 must chain forward to exactly one successor revision.

    Fleet-2 added 0019_embed_claims on top of 0018; this pins that the
    chain stayed linear (0018 has exactly one child, not zero and not two).
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    script = ScriptDirectory.from_config(cfg)

    successors = [
        rev.revision for rev in script.walk_revisions() if rev.down_revision == _TARGET_REVISION
    ]
    assert successors == ["0019_embed_claims"], (
        f"Expected 0018 to have exactly one successor 0019_embed_claims; got {successors!r}."
    )


# ---------------------------------------------------------------------------
# Test 6: Full chain linearity (single root, single head, no duplicates)
# ---------------------------------------------------------------------------


def test_revision_chain_is_linear() -> None:
    """The alembic revision chain from root to head must be strictly linear.

    Walks the ScriptDirectory's revision map and asserts:
    - Exactly one root (down_revision is None).
    - Exactly one head (no revision has two successors).
    - No duplicate revision ids.
    - Every non-root down_revision references a known revision id.

    (The head identity itself is pinned in test_alembic_head_pins_0019.py.)
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
