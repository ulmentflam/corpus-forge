"""Phase BR-04 release.yml workflow pins.

Validates ``.github/workflows/release.yml`` — the tag-triggered release
pipeline. Three jobs are expected:

1. ``gate`` — reuses ``ci.yml`` via ``workflow_call``.
2. ``build`` — needs gate; runs ``uv build``, computes
   ``sha256sum dist/* > dist/SHA256SUMS``, uploads ``dist/`` as an
   artifact.
3. ``publish`` — needs build; uses ``softprops/action-gh-release@v2``,
   passes ``files: dist/*``, derives ``prerelease`` from the tag (any
   tag containing ``b`` or ``rc`` is a prerelease), and
   ``generate_release_notes: true``.

Trigger: ``on.push.tags: ['v*']``.

These are structural pins — the workflow is not executed here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# PyYAML deserialises bare ``on:`` to True; accept either rendering.
_ON_KEYS = ("on", True)


def _on(doc: dict) -> dict:
    for k in _ON_KEYS:
        if k in doc:
            v = doc[k]
            assert isinstance(v, dict), f"Expected dict for 'on:', got {type(v)}"
            return v
    raise AssertionError(f"Missing 'on:' triggers block in {list(doc)}")


@pytest.fixture(scope="module")
def release_yaml() -> dict:
    assert RELEASE_PATH.exists(), f"Missing {RELEASE_PATH.relative_to(REPO_ROOT)}"
    text = RELEASE_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(text)


@pytest.fixture(scope="module")
def release_text() -> str:
    assert RELEASE_PATH.exists()
    return RELEASE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def jobs(release_yaml: dict) -> dict:
    j = release_yaml.get("jobs", {})
    assert isinstance(j, dict) and j, "release.yml must declare a non-empty jobs block"
    return j


# ── trigger ──────────────────────────────────────────────────────────────


def test_release_triggers_on_push_tags(release_yaml: dict) -> None:
    triggers = _on(release_yaml)
    push = triggers.get("push", {})
    assert isinstance(push, dict), "release.yml must have on.push as a dict"
    tags = push.get("tags")
    assert isinstance(tags, list) and tags, "on.push.tags must be a non-empty list"
    assert any(re.fullmatch(r"v\*|v\*\*|v\d.*", str(t)) for t in tags), (
        f"on.push.tags must include a 'v*' glob (got {tags})"
    )


def test_release_has_workflow_dispatch_or_call(release_yaml: dict) -> None:
    """At minimum the tag trigger; manual dispatch is optional but encouraged."""
    triggers = _on(release_yaml)
    # we don't require dispatch, but if it exists it must be a dict or null
    if "workflow_dispatch" in triggers:
        assert triggers["workflow_dispatch"] is None or isinstance(
            triggers["workflow_dispatch"], dict
        )


# ── gate job — reuses ci.yml via workflow_call ───────────────────────────


def test_gate_job_exists(jobs: dict) -> None:
    assert "gate" in jobs, "release.yml must define a `gate` job"


def test_gate_job_uses_ci_workflow_call(jobs: dict) -> None:
    gate = jobs["gate"]
    assert "uses" in gate, "gate job must use `uses:` to reuse ci.yml (workflow_call)"
    uses = gate["uses"]
    assert "ci.yml" in uses, f"gate must reuse ci.yml; got uses={uses!r}"
    assert uses.startswith("./.github/workflows/") or uses.startswith("ulmentflam/corpus-forge"), (
        f"gate `uses:` must point at the local ci.yml; got {uses!r}"
    )


def test_ci_yml_is_workflow_callable() -> None:
    """Pre-requisite: ci.yml must declare workflow_call. (CI-1 invariant.)"""
    text = CI_PATH.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    triggers = _on(doc)
    assert "workflow_call" in triggers, (
        "ci.yml must declare `workflow_call:` so release.yml can reuse it"
    )


# ── build job — uv build, sha256sums, artifact upload ────────────────────


def test_build_job_exists_and_needs_gate(jobs: dict) -> None:
    assert "build" in jobs, "release.yml must define a `build` job"
    build = jobs["build"]
    needs = build.get("needs")
    assert needs == "gate" or (isinstance(needs, list) and "gate" in needs), (
        f"build job must `needs: gate`; got needs={needs!r}"
    )


def test_build_job_runs_uv_build_and_shasums(jobs: dict) -> None:
    build = jobs["build"]
    steps = build.get("steps", [])
    assert isinstance(steps, list) and steps, "build must declare steps"
    blob = " ".join(str(s) for s in steps)
    assert "uv build" in blob, "build job must run `uv build`"
    assert "sha256sum" in blob, "build job must compute sha256sums for dist/"
    assert "SHA256SUMS" in blob, "build job must write dist/SHA256SUMS"


def test_build_job_uploads_artifact(jobs: dict) -> None:
    build = jobs["build"]
    steps = build.get("steps", [])
    upload = [
        s
        for s in steps
        if isinstance(s, dict) and "actions/upload-artifact" in str(s.get("uses", ""))
    ]
    assert upload, "build job must upload dist/ via actions/upload-artifact"


# ── publish job — softprops/action-gh-release, prerelease, files ─────────


def test_publish_job_exists_and_needs_build(jobs: dict) -> None:
    assert "publish" in jobs, "release.yml must define a `publish` job"
    pub = jobs["publish"]
    needs = pub.get("needs")
    assert needs == "build" or (isinstance(needs, list) and "build" in needs), (
        f"publish job must `needs: build`; got needs={needs!r}"
    )


def test_publish_job_downloads_artifact(jobs: dict) -> None:
    pub = jobs["publish"]
    steps = pub.get("steps", [])
    download = [
        s
        for s in steps
        if isinstance(s, dict) and "actions/download-artifact" in str(s.get("uses", ""))
    ]
    assert download, "publish job must download the dist/ artifact"


def test_publish_uses_softprops_action_gh_release(jobs: dict) -> None:
    pub = jobs["publish"]
    steps = pub.get("steps", [])
    release_step = None
    for s in steps:
        if isinstance(s, dict) and "softprops/action-gh-release" in str(s.get("uses", "")):
            release_step = s
            break
    assert release_step is not None, "publish must use softprops/action-gh-release@v2"
    assert "@v2" in release_step["uses"], (
        f"softprops/action-gh-release must be pinned to @v2 (got {release_step['uses']!r})"
    )


def test_publish_passes_files_and_prerelease(jobs: dict) -> None:
    pub = jobs["publish"]
    steps = pub.get("steps", [])
    release_step = next(
        s
        for s in steps
        if isinstance(s, dict) and "softprops/action-gh-release" in str(s.get("uses", ""))
    )
    with_ = release_step.get("with", {})
    files = str(with_.get("files", ""))
    assert "dist/" in files, f"`files:` must include dist/*; got {files!r}"
    prerelease = str(with_.get("prerelease", ""))
    # Derived from tag ref: contains 'b' or 'rc'.
    assert "contains(github.ref" in prerelease, (
        f"`prerelease:` must derive from github.ref; got {prerelease!r}"
    )
    assert "'b'" in prerelease or '"b"' in prerelease, (
        "`prerelease:` must detect beta tags ('b' in ref)"
    )
    assert "'rc'" in prerelease or '"rc"' in prerelease, (
        "`prerelease:` must detect release-candidate tags ('rc' in ref)"
    )
    notes = with_.get("generate_release_notes")
    assert notes is True, "publish must pass generate_release_notes: true"


def test_publish_job_has_contents_write_permission(jobs: dict, release_text: str) -> None:
    """publish must explicitly grant `contents: write` (top-level or job-level)."""
    # Accept either job-level or workflow-level grant.
    pub_perms = jobs["publish"].get("permissions", {})
    if isinstance(pub_perms, dict) and pub_perms.get("contents") == "write":
        return
    # Fall back to a YAML-text search for the top-level permissions block.
    assert re.search(r"^permissions:\s*\n\s*contents:\s*write", release_text, re.MULTILINE), (
        "release.yml must grant `contents: write` (top-level or on the publish job)"
    )
