"""Keep the development environment outside the checkout without losing files."""

import os
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


@pytest.mark.parametrize("existing", ["absent", "stale", "correct"])
def test_venv_link_targets_external_environment(tmp_path: Path, existing: str) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    external = tmp_path / "local venvs" / "corpus-forge"
    external.mkdir(parents=True)
    sentinel = external / "keep"
    sentinel.write_text("installed packages")
    link = checkout / ".venv"
    if existing != "absent":
        link.symlink_to(external if existing == "correct" else tmp_path / "missing")
    result = subprocess.run(
        ["make", "-f", str(MAKEFILE), "_venv-link", f"VENV={external}"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert link.is_symlink()
    assert link.resolve() == external
    assert sentinel.read_text() == "installed packages"


def test_venv_link_preserves_existing_directory(tmp_path: Path) -> None:
    local = tmp_path / ".venv"
    local.mkdir()
    sentinel = local / "keep"
    sentinel.write_text("existing environment")
    result = subprocess.run(
        ["make", "-f", str(MAKEFILE), "_venv-link", f"VENV={tmp_path / 'external'}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Refusing to replace .venv" in result.stderr
    assert not local.is_symlink()
    assert sentinel.read_text() == "existing environment"


def test_uv_recipes_use_external_environment(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$UV_PROJECT_ENVIRONMENT"\n')
    uv.chmod(0o755)
    external = tmp_path / "external venv"
    result = subprocess.run(
        ["make", "--silent", "-f", str(MAKEFILE), "lint", f"VENV={external}"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(external)
