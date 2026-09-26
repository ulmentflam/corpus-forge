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


@pytest.mark.parametrize("selection", ["uv_environment", "make_variable", "make_overrides_uv"])
def test_uv_recipes_use_external_environment(tmp_path: Path, selection: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$UV_PROJECT_ENVIRONMENT"\n')
    uv.chmod(0o755)
    external = tmp_path / "external venv"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env.pop("VENV", None)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    args = ["make", "--silent", "-f", str(MAKEFILE), "lint"]
    if selection == "uv_environment":
        env["UV_PROJECT_ENVIRONMENT"] = str(external)
    else:
        args.append(f"VENV={external}")
        if selection == "make_overrides_uv":
            env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "different environment")
    result = subprocess.run(
        args,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(external)
