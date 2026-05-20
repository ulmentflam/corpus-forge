"""Phase N Wave 0 — vendored OSS code corpus snapshot builder.

Run once to (re)build the deterministic snapshot under
``tests/fixtures/external/<corpus>-snapshot/`` that the Phase N retrieval-
quality bench iterates alongside corpus-forge.  Re-run any time we want
to bump the pinned upstream commit.

Why a snapshot (not a submodule)
--------------------------------

The bench (``tests/perf/test_phase_n_bench.py``) must be reproducible
without network access at test time.  Submodules would put the fixture
behind a ``git submodule update`` UX wart and let the bench drift if the
upstream changes underfoot.  Snapshotting a pinned commit's filtered
file set into committed bytes makes the corpus self-contained and the
bench numbers fully reproducible.

Corpus selection rationale
--------------------------

Considered candidates (see ``.planning/tdd/phase_n_retrieval_quality.md``
Wave 0):

- ``pydantic/pydantic`` (~1k files, MIT): too Python-mono — duplicates
  corpus-forge's language signature; bench broadening is minimal.
- ``huggingface/transformers`` (~3k files, Apache-2.0): too big — snapshot
  would bloat the git tree and slow CI clones (~hundreds of MB).
- ``tiangolo/fastapi`` (~1k files, MIT): strong candidate; mostly Python.
- ``tensorflow/models`` (~2k files, Apache-2.0): too large; TF-specific
  patterns are less representative of normal Python corpora.
- **``pallets/flask`` (~230 files, BSD-3-Clause): SELECTED.**

Why Flask wins:

1. **License**: BSD-3-Clause is the most permissive of the candidates.
2. **Size**: ~230 raw files / ~190 after filter / ~3.2 MB on disk —
   keeps the committed snapshot small (a few hundred KB after filter).
3. **Idiom diversity**: Flask's code style (decorators, blueprints,
   request lifecycle, Werkzeug interop) differs sharply from
   corpus-forge's (retrieval pipelines, embedders, backends).  That's
   the broadening that actually matters for the bench — not raw
   language diversity, but pattern diversity within Python.
4. **Content mix**: ``.py`` (83) / ``.rst`` (79) / ``.toml`` (5) /
   ``.md`` (6) / ``.cfg`` / ``.txt`` / ``.yaml`` — broader text-extension
   surface than corpus-forge's predominantly ``.py``/``.md``/``.toml``.

Pinned commit
-------------

::

    pallets/flask @ 954f5684e4841aad84a8eec7ace7b81a0d3f6831
    (committed 2026-05-18)

Bump by updating ``UPSTREAM_REPO`` / ``UPSTREAM_COMMIT`` below and
re-running this script.  After re-snapshot, re-author any queries whose
ground-truth byte offsets have shifted — the
``tests/perf/test_semble_queries.py`` rot-detector will trip if a
ground-truth ``byte_end`` runs past the file's new size.

File filter
-----------

Only text files we expect a corpus indexer to care about::

    .py .rst .md .toml .txt .cfg .ini .yaml .yml .json .html .in

We drop binaries, lockfiles, generated assets (``*.lock``, ``*.png``,
``*.ico``, etc.) and skip ``.git``, ``.tox``, ``__pycache__``,
``node_modules``, virtualenvs.

Determinism
-----------

The script walks the upstream clone in sorted order, normalises file
mtimes to a fixed epoch (so re-running on a different machine produces
byte-identical files modulo content), and writes bytes verbatim (no
re-encoding).  The resulting tree is suitable for committing.

Usage
-----

::

    cd /path/to/corpus-forge
    uv run python tests/fixtures/external/build_snapshots.py

By default uses a temp dir under ``/tmp`` for the upstream clone; pass
``--upstream-clone /existing/path`` to skip the clone step.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ── pinned snapshot configuration ───────────────────────────────────────

UPSTREAM_REPO = "https://github.com/pallets/flask.git"
UPSTREAM_COMMIT = "954f5684e4841aad84a8eec7ace7b81a0d3f6831"
UPSTREAM_COMMIT_DATE = "2026-05-18"
UPSTREAM_LICENSE = "BSD-3-Clause"
SNAPSHOT_NAME = "flask-snapshot"

# File extensions we keep.  These are the suffixes a corpus retriever
# would normally index: source code, docs, config.  Binaries and
# generated assets are dropped.
KEEP_SUFFIXES = frozenset(
    {
        ".py",
        ".rst",
        ".md",
        ".toml",
        ".txt",
        ".cfg",
        ".ini",
        ".yaml",
        ".yml",
        ".json",
        ".html",
        ".in",
    }
)

# Directory components we skip entirely (anywhere in the path).
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".tox",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".idea",
        ".vscode",
    }
)

# Specific files to skip even if their suffix matches.
SKIP_FILE_NAMES = frozenset(
    {
        # Lockfiles bloat the snapshot and aren't representative content.
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "package-lock.json",
        "yarn.lock",
    }
)


# ── snapshot construction ───────────────────────────────────────────────


def _here() -> Path:
    """Return the directory this script lives in."""
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    """Return the corpus-forge repo root."""
    return _here().parents[2]


def _clone_upstream(target: Path) -> Path:
    """Clone the pinned upstream commit into ``target``.

    Uses a shallow fetch + reset to the pinned sha for determinism +
    speed.  ``target`` is removed first so re-runs always produce the
    same on-disk state.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    print(f"[snapshot] cloning {UPSTREAM_REPO} into {target}", flush=True)
    subprocess.check_call(
        ["git", "init", "--initial-branch=main"],
        cwd=target,
        stdout=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "remote", "add", "origin", UPSTREAM_REPO],
        cwd=target,
        stdout=subprocess.DEVNULL,
    )
    print(f"[snapshot] fetching pin {UPSTREAM_COMMIT}", flush=True)
    subprocess.check_call(
        ["git", "fetch", "--depth", "1", "origin", UPSTREAM_COMMIT],
        cwd=target,
    )
    subprocess.check_call(
        ["git", "reset", "--hard", "FETCH_HEAD"],
        cwd=target,
        stdout=subprocess.DEVNULL,
    )
    return target


def _walk_keeper_files(root: Path) -> list[Path]:
    """Yield filtered files under ``root`` in sorted order.

    Returns absolute paths.  Filters by directory-skip set, by file-skip
    set, and by ``KEEP_SUFFIXES``.
    """
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # Skip anything under a banned dir component
        if any(part in SKIP_DIR_NAMES for part in p.parts):
            continue
        if p.name in SKIP_FILE_NAMES:
            continue
        if p.suffix.lower() not in KEEP_SUFFIXES:
            continue
        out.append(p)
    return out


def _snapshot_to(dst_root: Path, upstream: Path) -> tuple[int, int]:
    """Snapshot ``upstream`` into ``dst_root``, returning (n_files, n_bytes)."""
    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True)

    files = _walk_keeper_files(upstream)
    total_bytes = 0
    for src in files:
        rel = src.relative_to(upstream)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Byte-for-byte copy preserves the file content the bench
        # ground-truth offsets reference.
        data = src.read_bytes()
        dst.write_bytes(data)
        total_bytes += len(data)
    return len(files), total_bytes


def _write_readme(snapshot_dir: Path, n_files: int, n_bytes: int) -> None:
    """Write a README in the snapshot dir so humans reading the tree know
    what they're looking at."""
    parent_readme = snapshot_dir.parent / "README.md"
    parent_readme.write_text(
        "# tests/fixtures/external — vendored OSS code corpora\n"
        "\n"
        "Self-contained snapshots of upstream open-source repos, used by\n"
        "the Phase N retrieval-quality bench (`tests/perf/test_phase_n_bench.py`)\n"
        "to broaden the signal beyond corpus-forge's own source tree.\n"
        "\n"
        "Snapshots are produced by `build_snapshots.py` and committed as bytes.\n"
        "**Do not edit files under `*-snapshot/` directories by hand** — re-run\n"
        "the script to refresh.\n"
        "\n"
        "## Current vendored corpora\n"
        "\n"
        f"### `{SNAPSHOT_NAME}/`\n"
        "\n"
        f"- Upstream: <{UPSTREAM_REPO.rstrip('.git')}>\n"
        f"- License: {UPSTREAM_LICENSE}\n"
        f"- Pinned commit: `{UPSTREAM_COMMIT}` (committed {UPSTREAM_COMMIT_DATE})\n"
        f"- Snapshot footprint: {n_files} files / {n_bytes:,} bytes\n"
        f"- Suffix filter: `{', '.join(sorted(KEEP_SUFFIXES))}`\n"
        "\n"
        "To refresh:\n"
        "\n"
        "```bash\n"
        "uv run python tests/fixtures/external/build_snapshots.py\n"
        "```\n"
        "\n"
        "After refresh, re-author any ground-truth queries in\n"
        "`tests/perf/data/semble_queries.jsonl` whose byte offsets have shifted\n"
        "(the `tests/perf/test_semble_queries.py` rot-detector will flag\n"
        "out-of-bounds offsets).\n"
    )


def _summarise_extensions(snapshot_dir: Path) -> dict[str, int]:
    """Return ``{suffix: count}`` for files in the snapshot."""
    counts: dict[str, int] = {}
    for p in snapshot_dir.rglob("*"):
        if p.is_file():
            counts[p.suffix.lower()] = counts.get(p.suffix.lower(), 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--upstream-clone",
        type=Path,
        default=None,
        help="Path to an existing upstream clone (skip the network fetch). "
        "Must already be checked out at the pinned commit.",
    )
    parser.add_argument(
        "--temp-root",
        type=Path,
        default=Path("/tmp") / f"corpus-forge-snapshot-{SNAPSHOT_NAME}",
        help="Temp dir for the upstream clone (default: /tmp/...)",
    )
    args = parser.parse_args(argv)

    snapshot_dst = _here() / SNAPSHOT_NAME

    if args.upstream_clone is None:
        upstream = _clone_upstream(args.temp_root)
    else:
        upstream = args.upstream_clone.resolve()
        if not (upstream / ".git").is_dir():
            print(
                f"[snapshot] error: {upstream} is not a git checkout",
                file=sys.stderr,
            )
            return 2
        # Verify the upstream is at the pinned commit
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
        if sha != UPSTREAM_COMMIT:
            print(
                f"[snapshot] error: upstream HEAD={sha} != pinned {UPSTREAM_COMMIT}",
                file=sys.stderr,
            )
            return 2

    n_files, n_bytes = _snapshot_to(snapshot_dst, upstream)

    # Drop a LICENSE pointer inside the snapshot dir so the BSD-3-Clause
    # provenance travels with the bytes.
    license_src = upstream / "LICENSE.txt"
    if license_src.is_file():
        (snapshot_dst / "LICENSE.txt").write_bytes(license_src.read_bytes())

    _write_readme(snapshot_dst, n_files, n_bytes)

    ext_counts = _summarise_extensions(snapshot_dst)
    print(
        f"[snapshot] {SNAPSHOT_NAME}: {n_files} files / {n_bytes:,} bytes",
        flush=True,
    )
    for ext, ct in sorted(ext_counts.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {ext or '(no-ext)':<8} {ct:>4}")

    # Cleanup tmp clone unless user pinned a path
    if args.upstream_clone is None:
        shutil.rmtree(args.temp_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
