"""D-01 RED suite: Alembic scaffold presence + revision-chain well-formedness.

Four test functions:

1. test_alembic_ini_exists_and_parses   — FAILS RED (no alembic.ini yet)
2. test_alembic_env_module_imports      — FAILS RED (corpus_forge.alembic.env absent)
3. test_versions_directory_exists       — FAILS RED (corpus_forge/alembic/versions/ absent)
4. test_revision_chain_is_well_formed   — PASSES RED (zero revisions → no assertions)

Tests 1-3 go GREEN when D-01 coder lands the scaffold.
Test 4 becomes live once D-02+ land revision files.
"""

from __future__ import annotations

import configparser
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

# ── Repo-root anchor ─────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
VERSIONS_DIR = REPO_ROOT / "corpus_forge" / "alembic" / "versions"

# ── Helpers ──────────────────────────────────────────────────────────────────


def _discover_revision_modules() -> list[Path]:
    """Return sorted list of revision .py files, excluding __init__.py / .gitkeep."""
    if not VERSIONS_DIR.is_dir():
        return []
    return sorted(p for p in VERSIONS_DIR.glob("*.py") if p.name not in {"__init__.py"})


def _load_revision_module(path: Path):  # type: ignore[return]
    """Dynamically load a revision module from its path without side-effects."""
    spec = importlib.util.spec_from_file_location(f"_revision_{path.stem}", path)
    assert spec is not None, f"Could not create module spec for {path}"
    assert spec.loader is not None, f"No loader for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── Test 1: alembic.ini presence and content ─────────────────────────────────


def test_alembic_ini_exists_and_parses() -> None:
    """alembic.ini must exist at repo root and declare script_location = corpus_forge/alembic.

    FAILS RED: alembic.ini has not been created yet.
    """
    assert ALEMBIC_INI.exists(), (
        f"alembic.ini not found at {ALEMBIC_INI}. D-01 coder must create it at the repo root."
    )

    parser = configparser.ConfigParser()
    parser.read(str(ALEMBIC_INI))

    assert parser.has_section("alembic"), f"alembic.ini at {ALEMBIC_INI} has no [alembic] section"

    script_location = parser.get("alembic", "script_location", fallback=None)
    assert script_location == "corpus_forge/alembic", (
        f"Expected script_location = corpus_forge/alembic, got {script_location!r}"
    )

    # Stderr-discipline: no handler in the ini should route to sys.stdout.
    ini_text = ALEMBIC_INI.read_text()
    assert "sys.stdout" not in ini_text, (
        "alembic.ini must not route any logger to sys.stdout — "
        "all Alembic output must go to stderr."
    )


# ── Test 2: env module importability ─────────────────────────────────────────


def test_alembic_env_module_imports() -> None:
    """corpus_forge.alembic.env must import cleanly and expose both migration callables.

    FAILS RED: corpus_forge/alembic/env.py has not been created yet.
    """
    # Remove any stale cached import so this test is hermetic across reruns.
    for key in list(sys.modules.keys()):
        if "corpus_forge.alembic" in key:
            del sys.modules[key]

    try:
        env_mod = importlib.import_module("corpus_forge.alembic.env")
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"corpus_forge.alembic.env is not importable: {exc}. "
            "D-01 coder must create corpus_forge/alembic/env.py."
        )

    assert callable(getattr(env_mod, "run_migrations_online", None)), (
        "corpus_forge.alembic.env must expose a callable run_migrations_online()"
    )
    assert callable(getattr(env_mod, "run_migrations_offline", None)), (
        "corpus_forge.alembic.env must expose a callable run_migrations_offline()"
    )


# ── Test 3: versions directory presence ──────────────────────────────────────


def test_versions_directory_exists() -> None:
    """corpus_forge/alembic/versions/ must exist as a directory.

    FAILS RED: the alembic scaffold has not been created yet.
    """
    assert VERSIONS_DIR.exists(), (
        f"corpus_forge/alembic/versions/ not found at {VERSIONS_DIR}. "
        "D-01 coder must create the directory (with a .gitkeep placeholder)."
    )
    assert VERSIONS_DIR.is_dir(), f"{VERSIONS_DIR} exists but is not a directory."


# ── Test 4: revision-chain well-formedness ────────────────────────────────────


def test_revision_chain_is_well_formed() -> None:
    """Revision chain must be a valid linear sequence with no duplicates or orphans.

    PASSES RED: zero revisions exist → no chain assertions are made.
    Becomes live once D-02 lands 0001_core.py.

    When revisions ARE present:
    - Each module exposes revision: str and down_revision: str | None.
    - No duplicate revision values.
    - Exactly one root (down_revision is None).
    - Exactly one head (no other revision references it as a down_revision).
    - Head revision value equals the lexicographically-highest filename prefix.
    - No orphans: every non-root down_revision is some revision's revision value.
    - (Gated on alembic import) alembic.command.heads() returns exactly one head.
    """
    revision_files = _discover_revision_modules()

    if not revision_files:
        # Zero revisions — no chain to validate. Clean pass.
        # This branch is expected at D-01 RED time.
        return

    # ── Load all revision modules ─────────────────────────────────────────────
    modules = []
    for path in revision_files:
        mod = _load_revision_module(path)
        modules.append((path, mod))

    # ── Attribute presence ────────────────────────────────────────────────────
    for path, mod in modules:
        assert hasattr(mod, "revision") and isinstance(mod.revision, str), (
            f"{path.name}: missing or non-str 'revision' attribute"
        )
        down_rev: str | None = getattr(mod, "down_revision", _SENTINEL)
        assert down_rev is not _SENTINEL, f"{path.name}: missing 'down_revision' attribute"
        assert down_rev is None or isinstance(down_rev, str), (
            f"{path.name}: 'down_revision' must be str or None, got {type(down_rev)}"
        )

    revision_ids = [mod.revision for _, mod in modules]
    down_revisions = [mod.down_revision for _, mod in modules]

    # ── No duplicate revision values ──────────────────────────────────────────
    assert len(revision_ids) == len(set(revision_ids)), (
        f"Duplicate revision values detected: {revision_ids}"
    )

    # ── Exactly one root ──────────────────────────────────────────────────────
    roots = [rid for rid, dr in zip(revision_ids, down_revisions, strict=True) if dr is None]
    assert len(roots) == 1, (
        f"Expected exactly one root revision (down_revision=None), found {roots}"
    )

    # ── No orphans: every non-root down_revision must reference a known revision ──
    revision_set = set(revision_ids)
    for path, mod in modules:
        dr = mod.down_revision
        if dr is not None:
            assert dr in revision_set, (
                f"{path.name}: down_revision={dr!r} references unknown revision"
            )

    # ── Exactly one head ─────────────────────────────────────────────────────
    referenced_as_down = {dr for dr in down_revisions if dr is not None}
    heads = [rid for rid in revision_ids if rid not in referenced_as_down]
    assert len(heads) == 1, f"Expected exactly one head revision, found {len(heads)}: {heads}"

    head_revision_id = heads[0]

    # ── Head equals lexicographically-highest filename prefix ─────────────────
    highest_prefix_path = max(revision_files, key=lambda p: p.stem.split("_")[0])
    # Load that module to find its revision id
    highest_mod = _load_revision_module(highest_prefix_path)
    assert highest_mod.revision == head_revision_id, (
        f"Head revision {head_revision_id!r} does not match the revision id "
        f"of the lexicographically-highest file ({highest_prefix_path.name}: "
        f"{highest_mod.revision!r})"
    )

    # ── alembic.command.heads gate ────────────────────────────────────────────
    alembic_available = importlib.util.find_spec("alembic") is not None
    if not alembic_available:
        pytest.skip(
            "alembic package not installed — skipping alembic.command.heads() check. "
            "This check becomes active after D-01 coder adds alembic>=1.13 to pyproject.toml."
        )
        return

    import alembic.command
    import alembic.config

    assert ALEMBIC_INI.exists(), (
        "Cannot run alembic.command.heads(): alembic.ini not found. "
        "This branch is only reached when revisions exist, so alembic.ini must exist too."
    )

    cfg = alembic.config.Config(str(ALEMBIC_INI))
    # Suppress stdout/stderr noise from alembic during the test
    import io

    cfg.stdout = io.StringIO()

    # heads() prints to cfg.stdout; we care only about the exit (no exception = 1 head)
    # For a stricter check we patch the output stream and parse it.
    output_buf = io.StringIO()
    cfg.stdout = output_buf

    alembic.command.heads(cfg)

    heads_output = output_buf.getvalue().strip().splitlines()
    # Filter blank lines
    non_blank = [ln for ln in heads_output if ln.strip()]
    assert len(non_blank) == 1, (
        f"alembic.command.heads() reported {len(non_blank)} head(s); "
        f"expected exactly 1. Output:\n{output_buf.getvalue()}"
    )


# ── Sentinel (must be module-level so _load_revision_module can reference it) ──
_SENTINEL = object()


# ── Test 5: revision id length ≤ 32 chars (alembic VARCHAR(32) cap) ────────


# Alembic's default ``alembic_version.version_num`` column is
# ``VARCHAR(32)``. Once that table exists (created on the first
# migration), every subsequent ``alembic upgrade`` ends with an
# ``UPDATE alembic_version SET version_num = '<new_rev_id>'``. If
# any later-added revision uses an id longer than 32 chars, that
# UPDATE raises ``psycopg.errors.StringDataRightTruncation`` AFTER
# the migration's ``upgrade()`` body has already run — and the
# whole step rolls back transactionally, but the operator-visible
# failure is opaque ("value too long for type character varying(32)")
# and the schema is wedged at the previous revision.
#
# This regression test pins ``len(rev_id) <= 32`` for every
# revision so a 37-char id like
# ``0015_halfvec_index_for_wide_embedders`` (the original 0015,
# fixed in the follow-up commit) can never sneak through code
# review again.
_ALEMBIC_VERSION_NUM_VARCHAR_LIMIT = 32


def test_every_revision_id_fits_alembic_version_num_column() -> None:
    revision_files = _discover_revision_modules()
    if not revision_files:
        return  # no revisions yet — D-01 RED-time path

    too_long: list[str] = []
    for path in revision_files:
        mod = _load_revision_module(path)
        rev_id = getattr(mod, "revision", "")
        if not isinstance(rev_id, str):
            continue
        if len(rev_id) > _ALEMBIC_VERSION_NUM_VARCHAR_LIMIT:
            too_long.append(f"{path.name}: {rev_id!r} = {len(rev_id)} chars")

    assert not too_long, (
        "Revision id(s) longer than alembic's VARCHAR(32) "
        "version_num column would break `corpus-forge migrate` "
        "with `StringDataRightTruncation` at the end of the upgrade. "
        "Shorten:\n  " + "\n  ".join(too_long)
    )
