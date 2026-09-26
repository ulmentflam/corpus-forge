"""CI-1 workflow + composite action YAML validation.

The workflow files are validated structurally — not by spinning up Actions —
to guarantee:

- `.github/workflows/ci.yml` parses as YAML and declares the expected
  triggers (`pull_request`, `push` on `main`, `workflow_call`,
  `workflow_dispatch`).
- The job matrix uses Python 3.11/3.12/3.13 on `ubuntu-22.04` (CI-2 expands
  the OS axis; CI-1 stays single-OS).
- The workflow references real Make targets so a typo doesn't ship to PR
  gate land.
- `.github/actions/setup-uv/action.yml` parses as YAML and declares a
  composite `runs.using == "composite"` action.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

# PyYAML lands transitively via pre-commit / mkdocs-material; the import
# below should succeed in the dev env. If it doesn't, we'll see a clean
# collection error, which is itself a CI-1 signal worth fixing.
yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SETUP_UV_PATH = REPO_ROOT / ".github" / "actions" / "setup-uv" / "action.yml"
MAKEFILE_PATH = REPO_ROOT / "Makefile"


@pytest.fixture(scope="module")
def ci_yaml() -> dict:
    assert CI_PATH.exists(), f"Missing CI workflow at {CI_PATH}"
    with CI_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def setup_uv_yaml() -> dict:
    assert SETUP_UV_PATH.exists(), f"Missing setup-uv composite action at {SETUP_UV_PATH}"
    with SETUP_UV_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def make_targets() -> set[str]:
    """Parse Makefile target names (lines like `target:` or `target: dep`)."""
    targets: set[str] = set()
    pattern = re.compile(r"^([a-zA-Z0-9_-]+):")
    for line in MAKEFILE_PATH.read_text().splitlines():
        m = pattern.match(line)
        if m and not line.startswith("\t"):
            targets.add(m.group(1))
    return targets


# ── ci.yml structural checks ─────────────────────────────────────────────────


class TestCIWorkflow:
    def test_yaml_parses(self, ci_yaml: dict) -> None:
        assert isinstance(ci_yaml, dict)

    def test_has_name(self, ci_yaml: dict) -> None:
        assert "name" in ci_yaml

    def test_has_required_triggers(self, ci_yaml: dict) -> None:
        # In YAML, `on:` is a key; PyYAML deserializes the bareword to True.
        # Accept either rendering.
        on = ci_yaml.get("on") or ci_yaml.get(True)
        assert on is not None, "Missing 'on:' triggers block"
        assert isinstance(on, dict), f"Expected dict for triggers, got {type(on)}"
        for trigger in ("pull_request", "push", "workflow_call", "workflow_dispatch"):
            assert trigger in on, f"Missing trigger '{trigger}' in on: {list(on)}"

    def test_push_targets_main(self, ci_yaml: dict) -> None:
        on = ci_yaml.get("on") or ci_yaml.get(True)
        push = on.get("push")
        assert isinstance(push, dict) and "branches" in push
        branches = push["branches"]
        assert "main" in branches, f"Expected 'main' in push.branches: {branches}"

    def test_concurrency_cancel_in_progress(self, ci_yaml: dict) -> None:
        conc = ci_yaml.get("concurrency")
        assert isinstance(conc, dict), "concurrency block missing"
        assert conc.get("cancel-in-progress") is True

    def test_has_quality_and_test_jobs(self, ci_yaml: dict) -> None:
        jobs = ci_yaml.get("jobs", {})
        assert "quality" in jobs, f"Expected 'quality' job; jobs: {list(jobs)}"
        assert "test" in jobs, f"Expected 'test' job; jobs: {list(jobs)}"

    def test_test_job_matrix(self, ci_yaml: dict) -> None:
        jobs = ci_yaml.get("jobs", {})
        test_job = jobs.get("test", {})
        strategy = test_job.get("strategy", {})
        matrix = strategy.get("matrix", {})
        # OS axis: single-OS in CI-1 (CI-2 expands).
        os_axis = matrix.get("os")
        # Allow either single-string or list-of-one shape.
        if isinstance(os_axis, list):
            assert "ubuntu-22.04" in os_axis
        elif isinstance(os_axis, str):
            assert os_axis == "ubuntu-22.04"
        # Python axis: 3.11/3.12/3.13.
        python_axis = matrix.get("python-version")
        assert isinstance(python_axis, list)
        for v in ("3.11", "3.12", "3.13"):
            assert v in python_axis, f"Missing python {v} in matrix: {python_axis}"

    def test_jobs_reference_existing_make_targets(
        self, ci_yaml: dict, make_targets: set[str]
    ) -> None:
        """Every `make <target>` in any job step must resolve to a real Makefile target."""
        invocations: list[str] = []
        for job in ci_yaml.get("jobs", {}).values():
            for step in job.get("steps", []) or []:
                run = step.get("run") if isinstance(step, dict) else None
                if not run:
                    continue
                for line in str(run).splitlines():
                    for m in re.finditer(r"\bmake\s+([a-zA-Z0-9_-]+)", line):
                        invocations.append(m.group(1))
        assert invocations, "Expected at least one `make <target>` in CI workflow"
        missing = [t for t in invocations if t not in make_targets]
        assert not missing, (
            f"CI references nonexistent Make targets: {missing}; available: {sorted(make_targets)}"
        )


# ── setup-uv composite action ────────────────────────────────────────────────


class TestSetupUvAction:
    @pytest.mark.requires_unix
    def test_setup_and_make_share_environment(self, setup_uv_yaml: dict, tmp_path: Path) -> None:
        """Make must use the environment populated by the composite action."""
        env_file = tmp_path / "github-env"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        uv = bin_dir / "uv"
        uv.write_text('#!/bin/sh\nprintf "%s\\n" "$UV_PROJECT_ENVIRONMENT"\n')
        uv.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path / "runner temp"),
            "GITHUB_ENV": str(env_file),
        }
        env.pop("VENV", None)
        env.pop("UV_PROJECT_ENVIRONMENT", None)
        steps = setup_uv_yaml["runs"]["steps"]
        selection = next(step for step in steps if "GITHUB_ENV" in step.get("run", ""))
        sync = next(step for step in steps if "uv sync" in step.get("run", ""))
        assert steps.index(selection) < steps.index(sync)
        subprocess.run(["bash", "-c", selection["run"]], env=env, check=True)
        env.update(line.split("=", 1) for line in env_file.read_text().splitlines())
        setup_result = subprocess.run(
            ["bash", "-c", sync["run"]], env=env, check=True, capture_output=True, text=True
        )
        make_result = subprocess.run(
            ["make", "--silent", "-f", str(MAKEFILE_PATH), "lint"],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert setup_result.stdout.strip()
        assert make_result.stdout == setup_result.stdout

    def test_yaml_parses(self, setup_uv_yaml: dict) -> None:
        assert isinstance(setup_uv_yaml, dict)

    def test_is_composite(self, setup_uv_yaml: dict) -> None:
        runs = setup_uv_yaml.get("runs", {})
        assert runs.get("using") == "composite", (
            f"setup-uv must be a composite action; got {runs.get('using')!r}"
        )

    def test_has_steps(self, setup_uv_yaml: dict) -> None:
        runs = setup_uv_yaml.get("runs", {})
        steps = runs.get("steps", [])
        assert isinstance(steps, list) and steps, "composite action needs at least one step"

    def test_runs_uv_sync(self, setup_uv_yaml: dict) -> None:
        """At least one step must call `uv sync` so dev deps are installed."""
        runs = setup_uv_yaml.get("runs", {})
        steps = runs.get("steps", [])
        rendered = "\n".join(str(s.get("run", "")) for s in steps if isinstance(s, dict))
        assert "uv sync" in rendered, f"setup-uv must run `uv sync`; got steps: {steps}"
