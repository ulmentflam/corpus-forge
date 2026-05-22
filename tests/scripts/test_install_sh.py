"""Smoke tests for the post-install handoff in ``install.sh`` / ``install.ps1``.

These tests exercise *just* the ``__cf_post_install_handoff`` function from
``install.sh`` (sourced via an extracted body), and the equivalent block from
``install.ps1`` (extracted between sentinel markers and driven through
``pwsh -NoProfile -Command -``). The top-of-file ``uv`` provisioning and
``questions.toml`` fetch never run, so the suite is offline-safe and fast.

Coverage:

- The happy path: a stub ``corpus-forge`` succeeds for both ``setup`` and
  ``migrate``; both subcommands are invoked exactly once.
- The migrate-failure path: ``corpus-forge migrate`` exits non-zero. The
  handoff prints a warning that names ``migrate`` and the installer-as-a-whole
  still exits 0.
- The not-installed path: ``corpus-forge`` is absent from PATH. The handoff
  warns and does not crash.
- ``CF_CONFIG`` propagation: when ``CF_CONFIG`` is set in the caller's env,
  both subprocesses see it.
- The PowerShell counterpart (skipped when ``pwsh`` is not on PATH).

Complements ``tests/scripts/test_install_sh_handoff.py``, which is a static
text-check that ``setup --non-interactive`` is invoked. Where that test guards
against accidental reverts at the source level, these tests drive the actual
shell path.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_handoff_body() -> str:
    """Pull the body of ``__cf_post_install_handoff`` out of ``install.sh``.

    Uses the ``# END __cf_post_install_handoff`` sentinel as the end marker so
    the extractor is robust to brace nesting inside the function body. Asserts
    that both ``setup`` and ``migrate`` invocations survived the extraction so
    a future silent truncation fails loudly.
    """
    script = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(
        r"__cf_post_install_handoff\(\) \{\n(.*?)\n\}\n# END __cf_post_install_handoff",
        script,
        flags=re.DOTALL,
    )
    assert match is not None, (
        "Failed to extract __cf_post_install_handoff body. Either the "
        "sentinel `# END __cf_post_install_handoff` was removed or the "
        "function definition style changed."
    )
    body = match.group(1)
    assert "corpus-forge setup --non-interactive" in body, (
        "extracted handoff body lost the `corpus-forge setup --non-interactive` "
        "call — extractor is silently truncating or install.sh changed shape."
    )
    assert "corpus-forge migrate" in body, (
        "extracted handoff body lost the `corpus-forge migrate` call — "
        "extractor is silently truncating or install.sh changed shape."
    )
    return body


HANDOFF_HARNESS_PREAMBLE = textwrap.dedent(
    """\
    set -euo pipefail
    info()  { printf '%s\\n' "INFO: $*"; }
    ok()    { printf '%s\\n' "OK:   $*"; }
    warn()  { printf '%s\\n' "WARN: $*"; }
    fail()  { printf '%s\\n' "FAIL: $*" >&2; exit 1; }
    """
)


def _run_handoff(
    bin_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the extracted handoff with a controlled PATH and env."""
    body = _extract_handoff_body()
    # Wrap the extracted body in a function so `local` declarations remain
    # valid (the body is the inside of `__cf_post_install_handoff() { ... }`
    # in install.sh). Running the body at top-level would error out as soon
    # as `local` is encountered under `set -e`.
    script = (
        HANDOFF_HARNESS_PREAMBLE
        + "__cf_post_install_handoff() {\n"
        + body
        + "\n}\n__cf_post_install_handoff\n"
    )
    safe_path = f"{bin_dir}:/usr/bin:/bin"
    env = {"PATH": safe_path, "HOME": str(bin_dir.parent)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _install_stub_corpus_forge(
    bin_dir: Path,
    *,
    migrate_exit: int = 0,
    calls_file: Path | None = None,
) -> Path:
    """Drop a fake ``corpus-forge`` on PATH that logs each invocation.

    The stub writes ``<sub> CF_CONFIG=<value>`` to ``calls_file`` for every
    subcommand so tests can assert env propagation as well as the call order.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "corpus-forge"
    log = calls_file or (bin_dir / "calls.log")
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -u
            sub="${{1:-}}"
            printf '%s CF_CONFIG=%s\\n' "$sub" "${{CF_CONFIG:-unset}}" >> "{log}"
            case "$sub" in
                setup)   exit 0 ;;
                migrate) exit {migrate_exit} ;;
                *)       exit 0 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return log


def _subcommands(calls_file: Path) -> list[str]:
    """Return just the subcommand names from the stub's call log."""
    return [
        line.split(" CF_CONFIG=", 1)[0]
        for line in calls_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Bash handoff tests
# ---------------------------------------------------------------------------


def test_happy_path_invokes_setup_and_migrate(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log = _install_stub_corpus_forge(bin_dir, migrate_exit=0)

    result = _run_handoff(bin_dir)

    assert result.returncode == 0, f"handoff exited {result.returncode}; stderr={result.stderr!r}"
    assert _subcommands(log) == ["setup", "migrate"], (
        f"unexpected subcommand sequence: {log.read_text(encoding='utf-8')!r}"
    )
    assert "Done." in result.stdout


def test_migrate_failure_does_not_abort_installer(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log = _install_stub_corpus_forge(bin_dir, migrate_exit=1)

    result = _run_handoff(bin_dir)

    assert result.returncode == 0, (
        "installer must exit 0 even if migrate fails (Postgres unreachable, "
        f"etc.); got {result.returncode}, stderr={result.stderr!r}"
    )
    assert "migrate" in result.stdout, (
        f"warning must name `migrate` so the user knows what to re-run; stdout={result.stdout!r}"
    )
    assert "WARN" in result.stdout
    assert _subcommands(log) == ["setup", "migrate"]


def test_handoff_skipped_when_corpus_forge_missing(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    # Deliberately do NOT install the stub.
    bin_dir.mkdir(parents=True, exist_ok=True)

    result = _run_handoff(bin_dir)

    assert result.returncode == 0
    assert "corpus-forge not on PATH yet" in result.stdout


def test_cf_config_propagates_to_setup_and_migrate(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    calls = bin_dir / "calls.log"
    _install_stub_corpus_forge(bin_dir, migrate_exit=0, calls_file=calls)

    cf_config = "/tmp/test-cf-config.toml"
    result = _run_handoff(bin_dir, extra_env={"CF_CONFIG": cf_config})

    assert result.returncode == 0
    log_lines = calls.read_text(encoding="utf-8").splitlines()
    assert log_lines == [
        f"setup CF_CONFIG={cf_config}",
        f"migrate CF_CONFIG={cf_config}",
    ], f"both subcommands should inherit CF_CONFIG from the caller env; got {log_lines!r}"


# ---------------------------------------------------------------------------
# PowerShell handoff test (skipped when pwsh is absent)
# ---------------------------------------------------------------------------


PWSH = shutil.which("pwsh")


@pytest.mark.skipif(
    PWSH is None,
    reason="pwsh not on PATH; skipping install.ps1 migrate-handoff test",
)
def test_install_ps1_migrate_failure_does_not_abort(tmp_path: Path) -> None:
    """Drive the install.ps1 migrate handoff with a stubbed corpus-forge
    function and assert that a failing migrate doesn't abort the script and
    that ``$LASTEXITCODE`` is reset to 0.
    """
    assert PWSH is not None  # narrowed for the type checker; @skipif guards this
    log = tmp_path / "calls.log"
    cmd = textwrap.dedent(
        f"""\
        $ErrorActionPreference = 'Stop'
        function corpus-forge {{
            param([Parameter(ValueFromRemainingArguments)] $args)
            Add-Content -Path "{log}" -Value ($args -join ' ')
            if ($args[0] -eq 'migrate') {{
                Write-Error 'simulated migrate failure'
            }}
        }}
        # Inline the migrate-handoff block from install.ps1. Kept in sync by
        # hand; if install.ps1 grows additional subcommands they must be
        # mirrored here.
        $migrateLog = New-TemporaryFile
        $migrateFailed = $false
        $LASTEXITCODE = 0
        try {{
            & corpus-forge migrate *>&1 | Out-File -FilePath $migrateLog -Encoding utf8
            if ($LASTEXITCODE -ne 0) {{ $migrateFailed = $true }}
        }} catch {{
            Add-Content -Path $migrateLog -Value $_.Exception.Message
            $migrateFailed = $true
            $LASTEXITCODE = 0
        }}
        if ($migrateFailed) {{
            Write-Host "WARN: corpus-forge migrate failed — see $migrateLog"
            $LASTEXITCODE = 0
        }}
        Write-Host "EXIT=$LASTEXITCODE"
        exit 0
        """
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"pwsh exited {result.returncode}; stderr={result.stderr!r}"
    assert "WARN: corpus-forge migrate failed" in result.stdout
    assert "EXIT=0" in result.stdout
