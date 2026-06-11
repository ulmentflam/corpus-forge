"""Unit tests pinning 0020_shared_config's place in the alembic chain.

rfc-fleet-3 (federated config + setup) added 0020_shared_config on top of
0019_embed_claims. ``rfc-bench-embed-progress`` (stretch) then stacked
0021_benchmark_cold_start on top of 0020, so 0020 is no longer the head —
the head identity is pinned in ``test_alembic_head_pins_0021.py``. These
tests pin the still-true invariants for 0020 (mirroring the 0019→0020
head transition recorded in ``test_alembic_head_pins_0019.py``):

1. The revision file for 0020_shared_config exists at the expected path.
2. The module declares ``revision = "0020_shared_config"`` and
   ``down_revision = "0019_embed_claims"``.
3. The revision id fits in alembic's VARCHAR(32) ``version_num`` column.
4. alembic's ScriptDirectory can resolve 0020 and it chains onto 0019.
5. 0020 has exactly one successor (0021_benchmark_cold_start) — chain
   stayed linear.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_VERSIONS_DIR = _REPO_ROOT / "corpus_forge" / "alembic" / "versions"

_TARGET_REVISION = "0020_shared_config"
_PRIOR_REVISION = "0019_embed_claims"
_SUCCESSOR_REVISION = "0021_benchmark_cold_start"
_REVISION_FILE = _VERSIONS_DIR / "0020_shared_config.py"
_ALEMBIC_VERSION_NUM_MAX_LEN = 32  # alembic_version.version_num is VARCHAR(32)


def _load_revision_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"_revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None, f"Could not create module spec for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_file_exists() -> None:
    assert _REVISION_FILE.exists(), (
        f"Migration file not found at {_REVISION_FILE}. "
        "The coder must create corpus_forge/alembic/versions/0020_shared_config.py."
    )
    assert _REVISION_FILE.is_file(), f"{_REVISION_FILE} exists but is not a regular file."


def test_revision_id_is_correct() -> None:
    mod = _load_revision_module(_REVISION_FILE)
    assert mod.revision == _TARGET_REVISION, (
        f"Expected revision={_TARGET_REVISION!r}, got {mod.revision!r}"
    )


def test_down_revision_points_at_0019() -> None:
    mod = _load_revision_module(_REVISION_FILE)
    assert mod.down_revision == _PRIOR_REVISION, (
        f"Expected down_revision={_PRIOR_REVISION!r}, got {mod.down_revision!r}. "
        "Rebase drift would break the linear migration chain."
    )


def test_revision_id_fits_varchar32() -> None:
    mod = _load_revision_module(_REVISION_FILE)
    rev = mod.revision
    assert len(rev) <= _ALEMBIC_VERSION_NUM_MAX_LEN, (
        f"revision id {rev!r} is {len(rev)} chars; must be <= {_ALEMBIC_VERSION_NUM_MAX_LEN} "
        "to fit alembic_version.version_num VARCHAR(32)."
    )


def test_alembic_script_directory_knows_0020() -> None:
    """alembic.script.ScriptDirectory must know about 0020_shared_config.

    0020 is no longer the head (the bench-embed-progress stretch's
    0021_benchmark_cold_start stacks on top — see
    test_alembic_head_pins_0021.py), but it must remain a resolvable
    revision in the chain that ``apply_migrations`` walks.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert _ALEMBIC_INI.exists(), (
        f"alembic.ini not found at {_ALEMBIC_INI}; cannot resolve revisions."
    )

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))

    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(_TARGET_REVISION)
    assert rev is not None and rev.revision == _TARGET_REVISION, (
        f"alembic ScriptDirectory cannot resolve {_TARGET_REVISION!r}; "
        "the 0020_shared_config.py file must exist and chain onto 0019."
    )
    assert rev.down_revision == _PRIOR_REVISION


def test_0020_has_single_successor() -> None:
    """0020 must chain forward to exactly one successor revision.

    The bench-embed-progress stretch added 0021_benchmark_cold_start on top
    of 0020; this pins that the chain stayed linear (0020 has exactly one
    child, not zero and not two).
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    successors = [
        rev.revision for rev in script.walk_revisions() if rev.down_revision == _TARGET_REVISION
    ]
    assert successors == [_SUCCESSOR_REVISION], (
        f"Expected 0020 to have exactly one successor {_SUCCESSOR_REVISION!r}; got {successors!r}."
    )
