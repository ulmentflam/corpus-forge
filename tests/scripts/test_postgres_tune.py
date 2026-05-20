"""Smoke tests for ``scripts/postgres-tune.sh``.

The tuning script is pure math + a drop-in conf file render. Tests are
exclusively ``--help`` and ``--dry-run`` flows — we never write to a
real Postgres install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "postgres-tune.sh"


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["bash", str(SCRIPT), *args]
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        env=merged_env,
        input="",
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.fixture(autouse=True)
def _require_script() -> None:
    if not SCRIPT.exists():
        pytest.fail(f"Expected tune script at {SCRIPT}; not found.")


def test_help_exits_zero() -> None:
    result = _run(["--help"])
    assert result.returncode == 0, result.stderr
    assert "--ram" in result.stdout
    assert "--dry-run" in result.stdout


def test_ram_16_emits_expected_values() -> None:
    result = _run(["--ram", "16", "--dry-run"])
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    out = result.stdout
    assert "shared_buffers = 4GB" in out
    assert "effective_cache_size = 12GB" in out
    assert "work_mem = 128MB" in out
    assert "maintenance_work_mem = 1GB" in out
    assert "wal_compression = on" in out


def test_ram_32_scales_linearly() -> None:
    result = _run(["--ram", "32", "--dry-run"])
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    out = result.stdout
    assert "shared_buffers = 8GB" in out
    assert "effective_cache_size = 24GB" in out
    assert "work_mem = 256MB" in out
    # 32 GB * 64 MB = 2048 MB = 2 GB. The script may emit either form;
    # accept either.
    assert ("maintenance_work_mem = 2048MB" in out) or ("maintenance_work_mem = 2GB" in out)


def test_output_is_dropin_conf_d_file_not_postgresql_conf_edit() -> None:
    result = _run(["--ram", "16", "--dry-run"])
    assert result.returncode == 0
    out = result.stdout
    # The script writes a corpus-forge.conf drop-in. The dry-run must
    # mention that filename and explicitly NOT edit postgresql.conf.
    assert "corpus-forge.conf" in out
    # No sed/awk inline-edit of the main config.
    assert "sed -i" not in out or "postgresql.conf" not in out.replace("conf.d", "")


def test_small_ram_clamps_work_mem_to_floor() -> None:
    """Below the 16 GB inflection point, ``work_mem`` should clamp to 64MB."""
    result = _run(["--ram", "4", "--dry-run"])
    assert result.returncode == 0
    out = result.stdout
    # 4 GB * 4 MB = 16 MB, below the 64 MB floor → expect 64MB.
    assert "work_mem = 64MB" in out
    # 4 GB * 32 MB = 128 MB, below the 512 MB floor → expect 512MB.
    assert "maintenance_work_mem = 512MB" in out
