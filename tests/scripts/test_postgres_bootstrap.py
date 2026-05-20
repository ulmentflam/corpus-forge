"""Smoke tests for ``scripts/postgres-bootstrap.sh``.

The script itself is bash, but its surface is the CLI / env-var contract,
which we exercise via subprocess. We never let it run any real apt or
systemctl command — every test exercises a code path that is either
``--help``, ``--dry-run``, or an early-exit (unsupported distro / missing
required var).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "postgres-bootstrap.sh"

REQUIRED_ENV = {
    "CF_PG_DB": "corpus_forge",
    "CF_PG_USER": "corpus_forge",
    "CF_PG_PASSWORD": "s3cret",
    "CF_PG_CIDR": "192.168.1.0/24",
}


@pytest.fixture
def debian_os_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Path to a Debian-style /etc/os-release fixture.

    macOS hosts have no /etc/os-release; the bootstrap script reads
    ``CF_OS_RELEASE`` so tests can inject one. ``VERSION_CODENAME`` is
    required — the PGDG sources line embeds the codename literally and
    apt rejects it with 404 if the field is empty.
    """
    path = tmp_path_factory.mktemp("os-release") / "os-release"
    path.write_text(
        'ID="debian"\n'
        'VERSION_ID="12"\n'
        'VERSION_CODENAME="bookworm"\n'
        'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'
    )
    return path


ALL_FLAGS = (
    "--help",
    "--dry-run",
    "--db",
    "--user",
    "--password",
    "--cidr",
    "--pg-version",
    "--no-listen",
    "--quiet",
)


def _run(
    args: list[str],
    env: dict[str, str] | None = None,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run the bootstrap script with stdin piped (non-TTY)."""
    cmd = ["bash", str(SCRIPT), *args]
    base_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CF_PG_") and key != "CF_OS_RELEASE"
    }
    merged_env = {**base_env, **(env or {})}
    # Force the "non-TTY" path: subprocess.PIPE on stdin guarantees
    # ``[ -t 0 ]`` is false inside the script.
    return subprocess.run(
        cmd,
        env=merged_env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture(autouse=True)
def _require_script() -> None:
    if not SCRIPT.exists():
        pytest.fail(f"Expected bootstrap script at {SCRIPT}; not found.")


def test_help_exits_zero_and_lists_every_flag() -> None:
    result = _run(["--help"])
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ALL_FLAGS:
        assert flag in out, f"--help output missing {flag!r}: {out!r}"


def test_dry_run_emits_expected_command_sequence(debian_os_release: Path) -> None:
    # Filter env so the script picks values up from CF_PG_* and is
    # deterministic regardless of the host's locale.
    env = {**REQUIRED_ENV, "CF_OS_RELEASE": str(debian_os_release)}
    result = _run(["--dry-run"], env=env)
    assert result.returncode == 0, f"stderr={result.stderr!r} stdout={result.stdout!r}"
    out = result.stdout

    # Step ordering: PGDG repo add → Postgres install → role/db SQL →
    # CREATE EXTENSION → conf edit → reload. We assert presence + ordering
    # by checking the .find() index of marker strings unique to each step.
    markers = [
        "apt.postgresql.org",  # PGDG repo line / keyring
        "postgresql-17-pgvector",  # Postgres install step
        "CREATE ROLE",  # role SQL (inside DO $$)
        "CREATE DATABASE",  # db creation
        "CREATE EXTENSION IF NOT EXISTS vector",
        "listen_addresses",  # conf edit
        "pg_hba.conf",  # pg_hba append
        "systemctl reload postgresql",  # reload
    ]
    positions = []
    for marker in markers:
        idx = out.find(marker)
        assert idx >= 0, f"missing marker {marker!r} in dry-run output:\n{out}"
        positions.append(idx)
    assert positions == sorted(positions), (
        f"dry-run markers out of order: {list(zip(markers, positions, strict=True))}"
    )


def test_dry_run_does_not_execute_apt_or_systemctl(debian_os_release: Path) -> None:
    """A dry-run must not invoke real apt/systemctl — it just prints."""
    env = {**REQUIRED_ENV, "CF_OS_RELEASE": str(debian_os_release)}
    result = _run(["--dry-run"], env=env)
    assert result.returncode == 0
    # The script announces itself; the literal command strings appear in
    # the printed plan but the actual processes don't run. We can't fully
    # prove non-execution from outside, but we can assert the script
    # signals dry-run mode in its output.
    assert "dry-run" in result.stdout.lower() or "DRY-RUN" in result.stdout


def test_missing_required_env_var_on_non_tty_exits_2(debian_os_release: Path) -> None:
    env = {**REQUIRED_ENV, "CF_OS_RELEASE": str(debian_os_release)}
    env.pop("CF_PG_PASSWORD")
    result = _run(["--dry-run"], env=env)
    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # Error message names the missing var (case-insensitive).
    combined = (result.stderr + result.stdout).lower()
    assert "cf_pg_password" in combined or "--password" in combined


def test_dry_run_is_idempotent_byte_for_byte(debian_os_release: Path) -> None:
    env = {**REQUIRED_ENV, "CF_OS_RELEASE": str(debian_os_release)}
    first = _run(["--dry-run"], env=env)
    second = _run(["--dry-run"], env=env)
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout, (
        "two consecutive dry-runs produced different output — command list is not deterministic."
    )


def test_unsupported_distro_exits_3_and_points_at_docs(tmp_path: Path) -> None:
    """Mock /etc/os-release to a RHEL-style file and confirm the
    Debian/Ubuntu guard kicks in."""
    fake_os_release = tmp_path / "os-release"
    fake_os_release.write_text(
        'ID="rhel"\nID_LIKE="fedora"\nPRETTY_NAME="Red Hat Enterprise Linux 9"\n'
    )
    env = {**REQUIRED_ENV, "CF_OS_RELEASE": str(fake_os_release)}
    result = _run(["--dry-run"], env=env)
    assert result.returncode == 3, (
        f"expected exit 3 for RHEL; got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = (result.stderr + result.stdout).lower()
    assert "debian" in combined or "ubuntu" in combined or "docs/deployment/postgres.md" in combined


def test_pgdg_sources_line_uses_resolved_codename(debian_os_release: Path) -> None:
    """The PGDG sources line must embed the codename literally, not as
    `$(lsb_release -cs)`.

    Earlier revisions wrote the line in single quotes assuming apt would
    shell-expand it later (it does not). The result was a
    ``404 Not Found`` on every ``apt update``. This test pins the fix:
    the dry-run output must contain ``bookworm-pgdg`` (our fixture's
    VERSION_CODENAME) and must NOT contain the unexpanded form.
    """
    env = {**REQUIRED_ENV, "CF_OS_RELEASE": str(debian_os_release)}
    result = _run(["--dry-run"], env=env)
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    out = result.stdout
    assert "bookworm-pgdg" in out, f"PGDG line missing resolved codename 'bookworm-pgdg':\n{out}"
    assert "$(lsb_release" not in out, (
        "PGDG line still contains the unexpanded $(lsb_release -cs) literal; "
        "the resolved codename must be embedded in the sources.list line:\n"
        f"{out}"
    )


def test_password_confirmation_helper_is_defined_and_wired() -> None:
    """Static check that the password-confirm helper exists and is
    actually called for the PASSWORD resolution step.

    A real-TTY interaction test would require ``pty`` plumbing; this
    static check catches accidental regressions where someone reverts
    the helper or rewires the call site back to ``prompt_if_tty`` (which
    asks only once).
    """
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "prompt_password_with_confirm()" in script_text, (
        "password confirmation helper not defined"
    )
    assert "Confirm password:" in script_text, (
        "password confirmation helper does not prompt for confirmation"
    )
    assert "Passwords don't match" in script_text, (
        "password confirmation helper does not surface a mismatch message"
    )
    # The PASSWORD resolution line must use the confirm helper, not the
    # one-shot prompt_if_tty.
    pw_call = 'PASSWORD="$(prompt_password_with_confirm "${PASSWORD}"'
    assert pw_call in script_text, (
        "PASSWORD resolution is not wired to prompt_password_with_confirm"
    )


def test_pgdg_pre_existing_repo_skip_logic_is_present() -> None:
    """Static check that Step 1 detects pre-existing PGDG sources.

    Background: an LXC may already have apt.postgresql.org configured
    via a different sources.list file with a different ``Signed-By``
    keyring path (e.g. ``/etc/apt/keyrings/pgdg.gpg``, the modern Debian
    convention). Writing OUR own pgdg.list on top triggers

      E: Conflicting values set for option Signed-By regarding source
         https://apt.postgresql.org/pub/repos/apt/ trixie-pgdg:
         /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
         != /etc/apt/keyrings/pgdg.gpg

    and breaks apt update.

    Fix: before writing our own sources line, grep anywhere under
    /etc/apt/sources.list.d/ AND /etc/apt/sources.list for any
    apt.postgresql.org reference. If found, skip Step 1's write
    entirely — the host already knows how to fetch from PGDG.

    Static check because exercising it end-to-end would require a
    writable /etc/apt mounted into the test container.
    """
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "PGDG_EXISTING=" in script_text, "Step 1 missing pre-existing PGDG detection"
    assert "grep -rlI 'apt\\.postgresql\\.org'" in script_text, (
        "Step 1 doesn't scan /etc/apt for existing PGDG entries"
    )
    assert "/etc/apt/sources.list.d/" in script_text, (
        "Step 1 doesn't reference sources.list.d in its detection scan"
    )
    assert "skipping apt-source write" in script_text, "Step 1 doesn't log the skip path"
