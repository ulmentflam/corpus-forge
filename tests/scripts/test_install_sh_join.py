"""Tests for the ``--join`` / ``-Join`` pass-through in install.sh / install.ps1.

These tests exercise the new join-mode branches added for 0.1.0b17 (RFC
fleet-3 items 6 + 7): the installer parses ``--join <dsn>`` (and
``--join=<dsn>``) or inherits ``CF_JOIN_DSN`` from the caller's env,
skips the question tree (shared scope is pulled from the fleet's
primary), and hands off to ``corpus-forge setup --non-interactive
--join <dsn>`` followed by ``corpus-forge doctor`` — explicitly NOT
``corpus-forge migrate`` (the primary owns schema lifecycle).

The static text-check at the bottom mirrors
``test_install_sh_handoff.py``'s reverse-shield: if the join wiring
gets accidentally removed during a future refactor, a clean grep
fails loudly.

PowerShell counterparts are gated on ``shutil.which('pwsh')`` so the
suite stays offline-safe on macOS / Linux dev boxes without pwsh.
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

PWSH = shutil.which("pwsh")


# ---------------------------------------------------------------------------
# Helpers — install.sh extraction + stubbing
# ---------------------------------------------------------------------------


def _extract_handoff_body() -> str:
    """Pull the ``__cf_post_install_handoff`` body out of ``install.sh``.

    Uses the ``# END __cf_post_install_handoff`` sentinel as the end marker.
    This is the same extraction used by ``test_install_sh.py``; if that
    test's helper diverges (or the function definition style changes) the
    sentinel check below fails loudly.
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
    assert "corpus-forge setup --non-interactive --join" in body, (
        "extracted handoff body lost the `corpus-forge setup --non-interactive "
        "--join` call — the join wiring was either never added or got "
        "removed by a refactor."
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


def _install_join_stub_corpus_forge(
    bin_dir: Path,
    *,
    doctor_exit: int = 0,
    calls_file: Path | None = None,
) -> Path:
    """Drop a stub ``corpus-forge`` that logs full argv per invocation.

    Distinct from ``_install_stub_corpus_forge`` in ``test_install_sh.py``
    because the join path needs to assert ``setup --non-interactive
    --join <dsn>`` — i.e. the full argv, not just ``$1``.
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
            printf '%s\\n' "$*" >> "{log}"
            case "$sub" in
                setup)   exit 0 ;;
                doctor)  exit {doctor_exit} ;;
                migrate) exit 0 ;;
                *)       exit 0 ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return log


def _logged_argvs(calls_file: Path) -> list[str]:
    return [line for line in calls_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _subcommands(calls_file: Path) -> list[str]:
    return [argv.split(" ", 1)[0] for argv in _logged_argvs(calls_file)]


# ---------------------------------------------------------------------------
# Bash handoff tests — join branch
# ---------------------------------------------------------------------------


def test_handoff_join_runs_setup_join_then_doctor_no_migrate(tmp_path: Path) -> None:
    """In join mode the handoff invokes setup with ``--join <dsn>`` then
    ``doctor``, and explicitly does NOT call ``migrate`` (the primary
    owns schema lifecycle)."""
    bin_dir = tmp_path / "bin"
    log = _install_join_stub_corpus_forge(bin_dir, doctor_exit=0)
    dsn = "postgresql://primary.fleet:5432/corpus"

    result = _run_handoff(bin_dir, extra_env={"CF_JOIN_DSN": dsn})

    assert result.returncode == 0, f"handoff exited {result.returncode}; stderr={result.stderr!r}"
    argvs = _logged_argvs(log)
    assert argvs, f"stub recorded no invocations; stdout={result.stdout!r}"
    assert any(argv.startswith(f"setup --non-interactive --join {dsn}") for argv in argvs), (
        "handoff must call `corpus-forge setup --non-interactive --join <dsn>` "
        f"with the DSN from CF_JOIN_DSN; got {argvs!r}"
    )
    assert any(argv == "doctor" or argv.startswith("doctor ") for argv in argvs), (
        f"handoff must call `corpus-forge doctor` after setup in join mode; got {argvs!r}"
    )
    assert "migrate" not in _subcommands(log), (
        "join mode must NOT run `corpus-forge migrate` — the primary owns the "
        f"schema lifecycle; got {_subcommands(log)!r}"
    )


def test_handoff_join_doctor_failure_does_not_abort_installer(tmp_path: Path) -> None:
    """``doctor`` failures in join mode emit a WARN and the installer
    still exits 0 — mirrors the existing ``migrate`` tolerance so a
    fleet's primary being briefly unreachable doesn't leave the new
    host with a half-installed CLI."""
    bin_dir = tmp_path / "bin"
    log = _install_join_stub_corpus_forge(bin_dir, doctor_exit=1)
    dsn = "postgresql://primary.fleet:5432/corpus"

    result = _run_handoff(bin_dir, extra_env={"CF_JOIN_DSN": dsn})

    assert result.returncode == 0, (
        "installer must exit 0 even if `doctor` fails in join mode "
        f"(network blip, lane mismatch, etc.); got {result.returncode}, "
        f"stderr={result.stderr!r}"
    )
    assert "doctor" in result.stdout.lower(), (
        f"warning must name `doctor` so the user knows what to re-run; stdout={result.stdout!r}"
    )
    assert "WARN" in result.stdout, (
        f"join-mode doctor failure must surface a WARN line; stdout={result.stdout!r}"
    )
    # setup still ran; migrate still did not.
    assert "migrate" not in _subcommands(log)


def test_handoff_join_missing_binary_warns(tmp_path: Path) -> None:
    """When ``corpus-forge`` is absent from PATH the handoff warns and
    exits 0 — same shape as the non-join not-on-PATH branch today."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    # Deliberately do NOT install the stub.
    dsn = "postgresql://primary.fleet:5432/corpus"

    result = _run_handoff(bin_dir, extra_env={"CF_JOIN_DSN": dsn})

    assert result.returncode == 0
    assert "corpus-forge not on PATH" in result.stdout, (
        f"missing-binary branch must surface a hint; stdout={result.stdout!r}"
    )


def test_handoff_non_join_still_runs_setup_then_migrate(tmp_path: Path) -> None:
    """Regression gate: with NEITHER ``--join`` NOR ``CF_JOIN_DSN``, the
    handoff is byte-equivalent to today — setup then migrate, doctor
    NOT called.

    This is a finer-grained dual of ``test_install_sh.py``'s
    ``test_happy_path_invokes_setup_and_migrate`` that also pins the
    NEGATIVE (doctor must not fire in non-join mode)."""
    bin_dir = tmp_path / "bin"
    log = _install_join_stub_corpus_forge(bin_dir, doctor_exit=0)

    result = _run_handoff(bin_dir)  # no CF_JOIN_DSN

    assert result.returncode == 0
    subs = _subcommands(log)
    assert "setup" in subs and "migrate" in subs, (
        f"non-join path must invoke setup + migrate; got {subs!r}"
    )
    assert "doctor" not in subs, (
        f"non-join handoff must NOT call `corpus-forge doctor` (that's the join-mode "
        f"smoke check); got {subs!r}"
    )


# ---------------------------------------------------------------------------
# Bash full-script tests — arg-parsing + question-tree skip
# ---------------------------------------------------------------------------


def _run_install_sh(
    bin_dir: Path,
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    questions_toml: Path | None = None,
    cf_install_from: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``install.sh`` end-to-end with a stub ``corpus-forge`` and a
    stub ``uv`` on PATH so the script never hits the network or installs
    anything real.

    The stub ``uv`` accepts any args and exits 0; the stub
    ``corpus-forge`` logs full argv to ``$bin_dir/calls.log``.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / "calls.log"
    # Real-ish corpus-forge stub.
    _install_join_stub_corpus_forge(bin_dir, calls_file=log)
    # Stub uv so uv tool install is a no-op and the script never tries to
    # network out.
    uv_stub = bin_dir / "uv"
    uv_stub.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # Pretend to be uv.
            case "${1:-}" in
                --version) echo "uv 0.0.0 (stub)" ;;
                tool)      ;;
                *)         ;;
            esac
            exit 0
            """
        ),
        encoding="utf-8",
    )
    uv_stub.chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir.parent),
        # Force NO colour to keep output assertions easier.
        "NO_COLOR": "1",
        # Tell install.sh not to try to fetch questions.toml over HTTP.
        # Point it at the in-repo file so the path always resolves.
        "CF_QUESTIONS_URL": "file:///dev/null",
    }
    if questions_toml is not None:
        # install.sh prefers $SCRIPT_DIR/corpus_forge/setup/questions.toml.
        # We instead let it fall through to CF_QUESTIONS_URL — but `curl
        # file://` works with no network.
        env["CF_QUESTIONS_URL"] = f"file://{questions_toml}"
    if cf_install_from is not None:
        env["CF_INSTALL_FROM"] = cf_install_from
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_install_sh_join_flag_skips_question_tree(tmp_path: Path) -> None:
    """``install.sh --join <dsn>`` must skip the question-tree walk
    entirely and hand off to ``setup --non-interactive --join <dsn>``.

    The script-top usage comment promises "Join mode — skipping question
    tree (shared scope comes from primary)." The stub corpus-forge log
    is the source of truth: only the join-mode invocations should
    appear, no ``CF_BACKEND``-style prompts.
    """
    bin_dir = tmp_path / "bin"
    dsn = "postgresql://primary.fleet:5432/corpus"
    log = bin_dir / "calls.log"

    result = _run_install_sh(bin_dir, ["--join", dsn])

    assert result.returncode == 0, (
        f"install.sh --join exited {result.returncode}; "
        f"stderr={result.stderr!r}; stdout={result.stdout!r}"
    )
    argvs = _logged_argvs(log)
    assert argvs, f"corpus-forge stub was never called; stdout={result.stdout!r}"
    assert any(argv.startswith(f"setup --non-interactive --join {dsn}") for argv in argvs), (
        f"first setup invocation must carry the join DSN; got {argvs!r}"
    )
    # Question tree should be skipped — surface that via the info line.
    assert "Join mode" in result.stdout or "join mode" in result.stdout, (
        "install.sh should announce join-mode skip so the operator sees what "
        f"happened; stdout={result.stdout!r}"
    )


def test_install_sh_join_equals_form_parses(tmp_path: Path) -> None:
    """``--join=<dsn>`` is parsed identically to the space form."""
    bin_dir = tmp_path / "bin"
    dsn = "postgresql://primary.fleet:5432/corpus"
    log = bin_dir / "calls.log"

    result = _run_install_sh(bin_dir, [f"--join={dsn}"])

    assert result.returncode == 0, (
        f"install.sh --join=<dsn> exited {result.returncode}; stderr={result.stderr!r}"
    )
    argvs = _logged_argvs(log)
    assert any(argv.startswith(f"setup --non-interactive --join {dsn}") for argv in argvs), (
        f"--join=<dsn> form did not parse correctly; got {argvs!r}"
    )


def test_install_sh_env_var_join(tmp_path: Path) -> None:
    """``CF_JOIN_DSN`` in the caller's env (no flag) triggers join
    mode — same precedence as the flag."""
    bin_dir = tmp_path / "bin"
    dsn = "postgresql://primary.fleet:5432/corpus"
    log = bin_dir / "calls.log"

    result = _run_install_sh(bin_dir, [], extra_env={"CF_JOIN_DSN": dsn})

    assert result.returncode == 0, (
        f"install.sh CF_JOIN_DSN= exited {result.returncode}; stderr={result.stderr!r}"
    )
    argvs = _logged_argvs(log)
    assert any(argv.startswith(f"setup --non-interactive --join {dsn}") for argv in argvs), (
        f"CF_JOIN_DSN env var (with no --join flag) must also trigger join mode; got {argvs!r}"
    )
    assert "migrate" not in _subcommands(log), (
        f"env-var join still must NOT run migrate; got {_subcommands(log)!r}"
    )


# ---------------------------------------------------------------------------
# Static text-checks (reverse-shield against regressions)
# ---------------------------------------------------------------------------


def test_install_sh_text_mentions_join_setup_call() -> None:
    """``install.sh`` must contain a literal ``corpus-forge setup
    --non-interactive --join`` so the join wiring can't be silently
    deleted by a future refactor."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "corpus-forge setup --non-interactive --join" in text, (
        "install.sh must invoke `corpus-forge setup --non-interactive --join "
        "<dsn>` in its join-mode handoff."
    )


def test_install_sh_text_mentions_cf_join_dsn_env_var() -> None:
    """The script must reference ``CF_JOIN_DSN`` so both the flag and
    the env-var entry points are visible from a grep."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "CF_JOIN_DSN" in text, (
        "install.sh must reference CF_JOIN_DSN — it's the env-var equivalent "
        "of --join and the way the question-tree-skip predicate is shared "
        "between the arg parser and the handoff."
    )


def test_install_ps1_text_mentions_join_setup_call() -> None:
    """Parallel reverse-shield for install.ps1."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "corpus-forge setup --non-interactive --join" in text, (
        "install.ps1 must invoke `corpus-forge setup --non-interactive --join "
        "<dsn>` in its join-mode handoff (PowerShell mirror of install.sh)."
    )


def test_install_ps1_text_mentions_join_param() -> None:
    """``install.ps1`` must take a ``-Join`` parameter so Windows can be
    onboarded with a single PowerShell line."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "$Join" in text or "[string]$Join" in text or "-Join" in text, (
        "install.ps1 must declare a -Join parameter (param([string]$Join)) "
        "so callers can pass the DSN on the command line."
    )


# ---------------------------------------------------------------------------
# PowerShell handoff test (skipped when pwsh is absent)
# ---------------------------------------------------------------------------


def _extract_ps1_handoff() -> str:
    """Extract the join handoff block from install.ps1 between the
    sentinel markers introduced by T2.

    The PS1 doesn't have a function wrapper (its handoff is inline at
    file scope); we therefore wrap the extracted lines with the
    minimum env scaffolding to make them runnable on their own.
    """
    text = INSTALL_PS1.read_text(encoding="utf-8")
    # Sentinel pair: '# BEGIN __cf_post_install_handoff' / '# END __cf_post_install_handoff'
    match = re.search(
        r"# BEGIN __cf_post_install_handoff\s*\n(.*?)\n\s*# END __cf_post_install_handoff",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, (
        "Failed to extract PS1 post-install handoff. Add "
        "`# BEGIN __cf_post_install_handoff` / "
        "`# END __cf_post_install_handoff` sentinels around the handoff "
        "block in install.ps1 so this test can drive it in isolation."
    )
    return match.group(1)


@pytest.mark.skipif(PWSH is None, reason="pwsh not on PATH")
def test_install_ps1_join_handoff_calls_setup_join_then_doctor(tmp_path: Path) -> None:
    """The PowerShell handoff in join mode mirrors the bash one:
    setup --non-interactive --join <dsn> then doctor, no migrate."""
    assert PWSH is not None  # narrowed for type checker
    body = _extract_ps1_handoff()
    log = tmp_path / "calls.log"
    dsn = "postgresql://primary.fleet:5432/corpus"
    # Stub corpus-forge as a PowerShell function so the extracted block
    # can drive it without putting a real binary on PATH. The stub logs
    # full argv to $log.
    harness = textwrap.dedent(
        f"""\
        $ErrorActionPreference = 'Stop'
        $env:CF_JOIN_DSN = '{dsn}'
        function corpus-forge {{
            param([Parameter(ValueFromRemainingArguments)] $RemainingArgs)
            Add-Content -Path '{log}' -Value ($RemainingArgs -join ' ')
        }}
        function Write-Info($m) {{ Write-Host "INFO: $m" }}
        function Write-Ok($m)   {{ Write-Host "OK:   $m" }}
        function Write-Warn2($m) {{ Write-Host "WARN: $m" }}
        function Write-Fail($m)  {{ Write-Host "FAIL: $m"; exit 1 }}
        $LASTEXITCODE = 0
        """
    )
    script = harness + "\n" + body + "\nexit 0\n"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"pwsh exited {result.returncode}; stderr={result.stderr!r}; stdout={result.stdout!r}"
    )
    argvs = [line.strip() for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(argv.startswith(f"setup --non-interactive --join {dsn}") for argv in argvs), (
        f"PS1 join handoff must call setup --join; got {argvs!r}"
    )
    assert any(argv == "doctor" or argv.startswith("doctor ") for argv in argvs), (
        f"PS1 join handoff must call doctor after setup; got {argvs!r}"
    )
    subs = [argv.split(" ", 1)[0] for argv in argvs]
    assert "migrate" not in subs, f"PS1 join handoff must NOT call migrate; got {subs!r}"


@pytest.mark.skipif(PWSH is None, reason="pwsh not on PATH")
def test_install_ps1_join_handoff_doctor_failure_does_not_abort(tmp_path: Path) -> None:
    """A non-zero doctor exit in PS1 join mode must surface a WARN and
    the script must still exit 0 — mirrors the bash join-mode tolerance
    and the existing PS1 migrate tolerance."""
    assert PWSH is not None
    body = _extract_ps1_handoff()
    log = tmp_path / "calls.log"
    dsn = "postgresql://primary.fleet:5432/corpus"
    harness = textwrap.dedent(
        f"""\
        $ErrorActionPreference = 'Stop'
        $env:CF_JOIN_DSN = '{dsn}'
        function corpus-forge {{
            param([Parameter(ValueFromRemainingArguments)] $RemainingArgs)
            Add-Content -Path '{log}' -Value ($RemainingArgs -join ' ')
            if ($RemainingArgs[0] -eq 'doctor') {{
                $global:LASTEXITCODE = 1
                return
            }}
            $global:LASTEXITCODE = 0
        }}
        function Write-Info($m) {{ Write-Host "INFO: $m" }}
        function Write-Ok($m)   {{ Write-Host "OK:   $m" }}
        function Write-Warn2($m) {{ Write-Host "WARN: $m" }}
        function Write-Fail($m)  {{ Write-Host "FAIL: $m"; exit 1 }}
        $LASTEXITCODE = 0
        """
    )
    script = harness + "\n" + body + '\nWrite-Host "FINAL=$LASTEXITCODE"\nexit 0\n'
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"pwsh handoff must tolerate doctor failure; got rc={result.returncode}, "
        f"stderr={result.stderr!r}, stdout={result.stdout!r}"
    )
    assert "WARN" in result.stdout, f"doctor failure must emit a WARN; stdout={result.stdout!r}"
    assert "doctor" in result.stdout.lower(), (
        f"WARN should name `doctor` so the user knows what to retry; stdout={result.stdout!r}"
    )
