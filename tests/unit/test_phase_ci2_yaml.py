"""CI-2 workflow YAML pins.

Validates the cross-OS matrix expansion in ``ci.yml``, the new
``integration.yml`` (Linux + macOS), and the new ``nightly.yml`` (full
matrix + ``HYPOTHESIS_PROFILE=nightly`` + cron).  These are structural
tests — they do not execute Actions, only assert the YAML shape matches
the phase contract from the master plan.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INTEGRATION_PATH = REPO_ROOT / ".github" / "workflows" / "integration.yml"
NIGHTLY_PATH = REPO_ROOT / ".github" / "workflows" / "nightly.yml"

# PyYAML deserialises the bareword key ``on:`` to True; accept either rendering.
_ON_KEYS = ("on", True)


def _on(doc: dict) -> dict:
    for k in _ON_KEYS:
        if k in doc:
            v = doc[k]
            assert isinstance(v, dict), f"Expected dict for 'on:', got {type(v)}"
            return v
    raise AssertionError(f"Missing 'on:' triggers block in {list(doc)}")


@pytest.fixture(scope="module")
def ci_yaml() -> dict:
    assert CI_PATH.exists(), f"Missing {CI_PATH}"
    with CI_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def integration_yaml() -> dict:
    assert INTEGRATION_PATH.exists(), f"Missing {INTEGRATION_PATH} — CI-2 must create it"
    with INTEGRATION_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def nightly_yaml() -> dict:
    assert NIGHTLY_PATH.exists(), f"Missing {NIGHTLY_PATH} — CI-2 must create it"
    with NIGHTLY_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ── ci.yml — CI-2 matrix expansion ──────────────────────────────────────────


class TestCIMatrixExpansion:
    """CI-2 expands the test matrix to 3 OS × 3 Python with fail-fast off."""

    def test_test_job_matrix_three_os(self, ci_yaml: dict) -> None:
        test_job = ci_yaml["jobs"]["test"]
        matrix = test_job["strategy"]["matrix"]
        os_axis = matrix["os"]
        assert isinstance(os_axis, list), f"matrix.os must be a list; got {os_axis!r}"
        for required in ("ubuntu-22.04", "macos-14", "windows-2022"):
            assert required in os_axis, f"missing {required} in matrix.os: {os_axis}"

    def test_test_job_matrix_three_python(self, ci_yaml: dict) -> None:
        matrix = ci_yaml["jobs"]["test"]["strategy"]["matrix"]
        py_axis = matrix["python-version"]
        for required in ("3.11", "3.12", "3.13"):
            assert required in py_axis, f"missing python {required} in: {py_axis}"

    def test_fail_fast_false(self, ci_yaml: dict) -> None:
        strategy = ci_yaml["jobs"]["test"]["strategy"]
        assert strategy.get("fail-fast") is False, (
            "CI-2 requires fail-fast: false on the matrix"
        )

    def test_actionlint_job_present(self, ci_yaml: dict) -> None:
        jobs = ci_yaml["jobs"]
        assert "actionlint" in jobs, (
            f"CI-2 must add an 'actionlint' job; jobs: {list(jobs)}"
        )

    def test_actionlint_runs_on_ubuntu(self, ci_yaml: dict) -> None:
        job = ci_yaml["jobs"]["actionlint"]
        runs_on = job.get("runs-on")
        assert runs_on and "ubuntu" in str(runs_on), (
            f"actionlint must run on ubuntu; got {runs_on!r}"
        )

    def test_ci_no_docker_env_on_windows(self, ci_yaml: dict) -> None:
        """The Windows matrix cells need CI_NO_DOCKER=1 so integration tests skip."""
        test_job_text = yaml.safe_dump(ci_yaml["jobs"]["test"])
        assert "CI_NO_DOCKER" in test_job_text, (
            "Expected CI_NO_DOCKER env (per-step or job-level) to skip integration on Windows"
        )

    def test_windows_uses_bash_shell(self, ci_yaml: dict) -> None:
        """Run steps must use bash shell (matrix-wide or windows-specific)."""
        test_job_text = yaml.safe_dump(ci_yaml["jobs"]["test"])
        # Either a defaults.run.shell or per-step shell: bash; either way the
        # token must appear.
        assert "shell: bash" in test_job_text or "shell: 'bash'" in test_job_text, (
            "Windows step needs shell: bash to keep `make` invocations portable"
        )


# ── integration.yml — Linux services + macOS docker ────────────────────────


class TestIntegrationWorkflow:
    def test_yaml_parses(self, integration_yaml: dict) -> None:
        assert isinstance(integration_yaml, dict)

    def test_has_name(self, integration_yaml: dict) -> None:
        assert integration_yaml.get("name")

    def test_triggers_pr_and_dispatch(self, integration_yaml: dict) -> None:
        on = _on(integration_yaml)
        # Reusable from ci.yml or fired on PR — accept several shapes but
        # require either pull_request or workflow_call/dispatch.
        assert any(t in on for t in ("pull_request", "workflow_dispatch", "workflow_call")), (
            f"integration.yml must have a useful trigger; on={list(on)}"
        )

    def test_two_os_matrix(self, integration_yaml: dict) -> None:
        """Integration job runs on Linux + macOS (no Windows)."""
        jobs = integration_yaml.get("jobs", {})
        # Find the job that has a matrix; tolerate single- or multi-job files.
        matrix = None
        for j in jobs.values():
            strategy = (j or {}).get("strategy") or {}
            m = strategy.get("matrix")
            if m:
                matrix = m
                break
        assert matrix is not None, "integration.yml must have a strategy.matrix"
        os_axis = matrix.get("os")
        assert isinstance(os_axis, list), f"matrix.os must be a list; got {os_axis!r}"
        assert "ubuntu-22.04" in os_axis, "integration.yml needs ubuntu-22.04"
        assert "macos-14" in os_axis, "integration.yml needs macos-14"
        assert "windows-2022" not in os_axis, (
            "integration.yml must NOT include Windows (Docker unsupported on Win runners)"
        )

    def test_python_matrix_drops_313_for_now(self, integration_yaml: dict) -> None:
        """3.13 dropped from integration matrix until sentence-transformers wheels stabilize."""
        jobs = integration_yaml.get("jobs", {})
        matrix = None
        for j in jobs.values():
            strategy = (j or {}).get("strategy") or {}
            m = strategy.get("matrix")
            if m:
                matrix = m
                break
        assert matrix is not None
        py_axis = matrix.get("python-version")
        assert isinstance(py_axis, list)
        for required in ("3.11", "3.12"):
            assert required in py_axis, f"missing python {required} in: {py_axis}"

    def test_linux_postgres_service_declared(self, integration_yaml: dict) -> None:
        """A services: block with pgvector image must be declared somewhere in the job."""
        # services: blocks live on the job; they apply to Linux runners only.
        # We require: a services entry whose `image` contains "pgvector".
        jobs = integration_yaml.get("jobs", {})
        text = yaml.safe_dump(jobs)
        assert "pgvector" in text, "integration.yml must reference a pgvector service image"
        assert "services" in text, "integration.yml must declare services: block"

    def test_postgres_healthcheck_present(self, integration_yaml: dict) -> None:
        text = yaml.safe_dump(integration_yaml)
        assert "pg_isready" in text, (
            "Postgres service must have a healthcheck using pg_isready"
        )

    def test_macos_docker_setup(self, integration_yaml: dict) -> None:
        """macOS runner needs docker/setup-docker-action so testcontainers works."""
        text = yaml.safe_dump(integration_yaml)
        assert "setup-docker-action" in text, (
            "macOS runner needs docker/setup-docker-action@v3"
        )


# ── nightly.yml — cron + nightly hypothesis profile ────────────────────────


class TestNightlyWorkflow:
    def test_yaml_parses(self, nightly_yaml: dict) -> None:
        assert isinstance(nightly_yaml, dict)

    def test_has_schedule_cron(self, nightly_yaml: dict) -> None:
        on = _on(nightly_yaml)
        sched = on.get("schedule")
        assert sched, "nightly.yml must have a schedule trigger"
        # schedule is a list of {cron: '...'}
        assert isinstance(sched, list) and sched, "schedule must be a non-empty list"
        crons = [s.get("cron") for s in sched if isinstance(s, dict)]
        assert any(c for c in crons), f"schedule entries need a cron string: {sched}"

    def test_workflow_dispatch_available(self, nightly_yaml: dict) -> None:
        on = _on(nightly_yaml)
        assert "workflow_dispatch" in on, "nightly.yml needs workflow_dispatch for manual fire"

    def test_hypothesis_profile_nightly(self, nightly_yaml: dict) -> None:
        """At least one job step / env must set HYPOTHESIS_PROFILE=nightly."""
        text = yaml.safe_dump(nightly_yaml)
        # The literal env value can be either `nightly` (unquoted) or `'nightly'`.
        assert re.search(r"HYPOTHESIS_PROFILE\s*:\s*['\"]?nightly['\"]?", text), (
            "nightly.yml must set HYPOTHESIS_PROFILE: nightly somewhere"
        )

    def test_full_os_matrix(self, nightly_yaml: dict) -> None:
        """Nightly runs the full 3-OS matrix to catch platform regressions overnight."""
        jobs = nightly_yaml.get("jobs", {})
        text = yaml.safe_dump(jobs)
        for os_name in ("ubuntu-22.04", "macos-14", "windows-2022"):
            assert os_name in text, f"nightly.yml must include {os_name} in some job's matrix"
