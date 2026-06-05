"""Unit tests pinning the alembic revision chain head to 0019_embed_claims.

rfc-fleet-2 (distributed embedding) — moves the pinned head from
0018_model_telemetry to 0019_embed_claims.

These tests assert:
1. The revision file for 0019_embed_claims exists at the expected path.
2. The module declares ``revision = "0019_embed_claims"`` and
   ``down_revision = "0018_model_telemetry"``.
3. The revision id fits in alembic's VARCHAR(32) ``version_num`` column.
4. alembic's ScriptDirectory reports ``0019_embed_claims`` as the single
   current head revision.
5. The head used by ``apply_migrations`` resolves to ``0019_embed_claims``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_VERSIONS_DIR = _REPO_ROOT / "corpus_forge" / "alembic" / "versions"

_TARGET_REVISION = "0019_embed_claims"
_PRIOR_REVISION = "0018_model_telemetry"
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


def test_alembic_script_directory_head_is_0019() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert _ALEMBIC_INI.exists(), (
        f"alembic.ini not found at {_ALEMBIC_INI}; cannot resolve head revision."
    )

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "corpus_forge" / "alembic"))

    script = ScriptDirectory.from_config(cfg)
    current_head = script.get_current_head()

    assert current_head == _TARGET_REVISION, (
        f"alembic ScriptDirectory reports head={current_head!r}; "
        f"expected {_TARGET_REVISION!r}. "
        "The 0019_embed_claims.py file must exist and chain onto 0018 before this passes."
    )


def test_expected_head_revision_helper_returns_0019() -> None:
    from alembic.script import ScriptDirectory

    from corpus_forge.schema.migrate import _build_alembic_config

    head = ScriptDirectory.from_config(_build_alembic_config()).get_current_head()
    assert head == _TARGET_REVISION, (
        f"_expected_head_revision() helper returned {head!r}; expected {_TARGET_REVISION!r}."
    )
