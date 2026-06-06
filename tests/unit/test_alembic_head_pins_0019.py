"""Unit tests pinning 0019_embed_claims's place in the alembic chain.

rfc-fleet-2 (distributed embedding) added 0019_embed_claims on top of
0018_model_telemetry. Fleet-3 (rfc-fleet-3-federated-config-and-setup)
then stacked 0020_shared_config on top of 0019, so 0019 is no longer the
head — the head identity is pinned in ``test_alembic_head_pins_0020.py``.
These tests pin the still-true invariants for 0019:

1. The revision file for 0019_embed_claims exists at the expected path.
2. The module declares ``revision = "0019_embed_claims"`` and
   ``down_revision = "0018_model_telemetry"``.
3. The revision id fits in alembic's VARCHAR(32) ``version_num`` column.
4. alembic's ScriptDirectory can resolve 0019 and it chains onto 0018.
5. 0019 has exactly one successor (0020_shared_config) — chain stayed linear.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_VERSIONS_DIR = _REPO_ROOT / "corpus_forge" / "alembic" / "versions"

_TARGET_REVISION = "0019_embed_claims"
_PRIOR_REVISION = "0018_model_telemetry"
_SUCCESSOR_REVISION = "0020_shared_config"
_REVISION_FILE = _VERSIONS_DIR / "0019_embed_claims.py"
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
        "The coder must create corpus_forge/alembic/versions/0019_embed_claims.py."
    )
    assert _REVISION_FILE.is_file(), f"{_REVISION_FILE} exists but is not a regular file."


def test_revision_id_is_correct() -> None:
    mod = _load_revision_module(_REVISION_FILE)
    assert mod.revision == _TARGET_REVISION, (
        f"Expected revision={_TARGET_REVISION!r}, got {mod.revision!r}"
    )


def test_down_revision_points_at_0018() -> None:
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


def test_alembic_script_directory_knows_0019() -> None:
    """alembic.script.ScriptDirectory must know about 0019_embed_claims.

    0019 is no longer the head (fleet-3's 0020_shared_config stacks on top —
    see test_alembic_head_pins_0020.py), but it must remain a resolvable
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
        "the 0019_embed_claims.py file must exist and chain onto 0018."
    )
    assert rev.down_revision == _PRIOR_REVISION


def test_0019_has_single_successor() -> None:
    """0019 must chain forward to exactly one successor revision.

    Fleet-3 added 0020_shared_config on top of 0019; this pins that the
    chain stayed linear (0019 has exactly one child, not zero and not two).
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
        f"Expected 0019 to have exactly one successor {_SUCCESSOR_REVISION!r}; got {successors!r}."
    )
