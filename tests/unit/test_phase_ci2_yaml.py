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
    """CI-2 expanded the test matrix to multiple OSes with fail-fast off.

    Windows was dropped from the v0.1.0b1 beta matrix (see ci.yml comment);
    these tests now pin the "Linux + macOS, 3 Python versions" shape.
    """

    def test_test_job_matrix_required_oses(self, ci_yaml: dict) -> None:
        test_job = ci_yaml["jobs"]["test"]
        matrix = test_job["strategy"]["matrix"]
        os_axis = matrix["os"]
        assert isinstance(os_axis, list), f"matrix.os must be a list; got {os_axis!r}"
        for required in ("ubuntu-22.04", "macos-14"):
            assert required in os_axis, f"missing {required} in matrix.os: {os_axis}"

    def test_test_job_matrix_three_python(self, ci_yaml: dict) -> None:
        matrix = ci_yaml["jobs"]["test"]["strategy"]["matrix"]
        py_axis = matrix["python-version"]
        for required in ("3.11", "3.12", "3.13"):
            assert required in py_axis, f"missing python {required} in: {py_axis}"

    def test_fail_fast_false(self, ci_yaml: dict) -> None:
        strategy = ci_yaml["jobs"]["test"]["strategy"]
        assert strategy.get("fail-fast") is False, "CI-2 requires fail-fast: false on the matrix"

    def test_actionlint_job_present(self, ci_yaml: dict) -> None:
        jobs = ci_yaml["jobs"]
        assert "actionlint" in jobs, f"CI-2 must add an 'actionlint' job; jobs: {list(jobs)}"

    def test_actionlint_runs_on_ubuntu(self, ci_yaml: dict) -> None:
        job = ci_yaml["jobs"]["actionlint"]
        runs_on = job.get("runs-on")
        assert runs_on and "ubuntu" in str(runs_on), (
            f"actionlint must run on ubuntu; got {runs_on!r}"
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
        """Integration runs cover Linux + macOS (no Windows).

        The OS axis may live in a single matrix or be split across two jobs
        (e.g. ``integration-linux`` + ``integration-macos``).  Either shape
        is valid; we union all OS values found across all jobs.
        """
        jobs = integration_yaml.get("jobs", {})
        os_union: set[str] = set()
        for j in jobs.values():
            strategy = (j or {}).get("strategy") or {}
            matrix = strategy.get("matrix") or {}
            os_axis = matrix.get("os")
            if isinstance(os_axis, list):
                os_union.update(os_axis)
            elif isinstance(os_axis, str):
                os_union.add(os_axis)
            # Also accept a runs-on hard-coded value when the job has no matrix.
            runs_on = (j or {}).get("runs-on")
            if isinstance(runs_on, str) and not os_axis:
                os_union.add(runs_on)

        assert os_union, "integration.yml must expose at least one OS in some matrix or runs-on"
        assert "ubuntu-22.04" in os_union, f"integration.yml needs ubuntu-22.04; got {os_union}"
        assert "macos-14" in os_union, f"integration.yml needs macos-14; got {os_union}"
        assert "windows-2022" not in os_union, (
            "integration.yml must NOT include Windows (Docker unsupported on Win runners)"
        )

    def test_python_matrix_drops_313_for_now(self, integration_yaml: dict) -> None:
        """3.13 dropped from integration matrix until sentence-transformers wheels stabilize.

        Union the python-version axis across all jobs in the workflow so a
        Linux-only vs macOS-only split still satisfies the contract.
        """
        py_union: set[str] = set()
        for j in integration_yaml.get("jobs", {}).values():
            strategy = (j or {}).get("strategy") or {}
            py = (strategy.get("matrix") or {}).get("python-version")
            if isinstance(py, list):
                py_union.update(str(v) for v in py)
            elif py is not None:
                py_union.add(str(py))
        assert py_union, "integration.yml must declare a python-version matrix"
        for required in ("3.11", "3.12"):
            assert required in py_union, f"missing python {required}; got {py_union}"

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
        assert "pg_isready" in text, "Postgres service must have a healthcheck using pg_isready"

    def test_macos_docker_setup(self, integration_yaml: dict) -> None:
        """macOS runner needs a docker-setup step so testcontainers works.

        History: pinned first to ``docker/setup-docker-action@v3`` (does
        not exist), then to ``crazy-max/ghaction-setup-docker@v3``
        (404s on docker CDN for new arm64 binaries). Now installs
        Colima + docker CLI via brew directly. Contract: some step in
        the macOS path must invoke ``colima`` (or fall back to a known
        docker-setup action).
        """
        text = yaml.safe_dump(integration_yaml)
        assert (
            "colima" in text or "ghaction-setup-docker" in text or "setup-docker-action" in text
        ), "macOS runner needs Colima or a docker-setup action for testcontainers"


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
        """Nightly runs across all OSes we support to catch platform regressions overnight.

        Windows was dropped from v0.1.0b1's primary matrix; nightly mirrors
        the same Linux + macOS shape until post-beta Windows work lands.
        """
        jobs = nightly_yaml.get("jobs", {})
        text = yaml.safe_dump(jobs)
        for os_name in ("ubuntu-22.04", "macos-14"):
            assert os_name in text, f"nightly.yml must include {os_name} in some job's matrix"
