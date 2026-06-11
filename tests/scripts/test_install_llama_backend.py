"""Tests for the llama-cpp-python accelerator-wheel selection in the
installers (RFC fleet-7 items 1-3).

Both ``install.sh`` and ``install.ps1`` gained a step that detects the host
accelerator (the same signals ``corpus_forge/acceleration.py`` uses) and
points ``uv tool install`` at the matching prebuilt-wheel extra-index
(``cpu`` / ``metal`` / ``cuXXX``), with a ``--llama-backend`` /
``-LlamaBackend`` override and a CPU fallback. These tests drive the bash
path end-to-end with stubbed ``uv`` / ``corpus-forge`` (offline, fast) and
the pure CUDA-version → wheel-variant mapping in isolation; PowerShell
counterparts are gated on ``shutil.which('pwsh')``.

What's asserted:

- The pure ``__cf_cuda_variant`` / ``Get-CudaVariant`` mapping clamps a
  detected CUDA version onto the published abetlen wheel variants.
- ``CF_EMBEDDER=auto`` (the recommended → llama-cpp lane) installs the
  ``[llama-cpp]`` extra with the CPU wheel on a no-accelerator box — and
  ``CF_EMBEDDER=st`` does NOT (no surprise heavy extra on every install).
- An explicit ``--llama-backend cudaNNN`` selects the ``cuNNN`` index and,
  when that fetch fails, falls back to the CPU wheel + WARN without
  aborting the installer.
- ``--llama-backend none`` drops the extra and skips index selection.
- Reverse-shield static text checks so the flag/env wiring can't be
  silently deleted by a future refactor.
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
# Helpers — stub uv + corpus-forge, run install.sh end-to-end offline
# ---------------------------------------------------------------------------


def _install_stubs(
    bin_dir: Path,
    *,
    uv_fails_on_accel: bool = False,
    nvidia_smi: str | None = "absent",
) -> tuple[Path, Path]:
    """Drop stub ``uv`` + ``corpus-forge`` (+ a controlled ``nvidia-smi``)
    on PATH.

    The stub ``uv`` logs every ``tool install`` argv to ``uv.log``; when
    ``uv_fails_on_accel`` is set it exits non-zero for any invocation whose
    ``--extra-index-url`` points at a ``cuXXX`` / ``metal`` variant (so the
    CPU-fallback branch can be exercised) and 0 for the CPU index. The stub
    ``corpus-forge`` succeeds for every subcommand.

    ``nvidia_smi`` makes accelerator detection deterministic regardless of
    the host (this repo's CI / dev boxes may have a real GPU):
    ``"absent"`` → a stub that exits non-zero (no CUDA); ``"cuda:<ver>"`` →
    a stub that prints a ``CUDA Version: <ver>`` header and exits 0;
    ``None`` → no stub (use the host's real nvidia-smi).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    if nvidia_smi is not None:
        smi = bin_dir / "nvidia-smi"
        if nvidia_smi == "absent":
            smi.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        elif nvidia_smi.startswith("cuda:"):
            ver = nvidia_smi.split(":", 1)[1]
            smi.write_text(
                f"#!/usr/bin/env bash\necho 'CUDA Version: {ver}'\nexit 0\n",
                encoding="utf-8",
            )
        else:  # pragma: no cover - guards against a typo'd test arg
            raise ValueError(f"unexpected nvidia_smi stub spec: {nvidia_smi!r}")
        smi.chmod(0o755)
    uv_log = bin_dir / "uv.log"
    fail_branch = (
        textwrap.dedent(
            """\
            for a in "$@"; do
                case "$a" in
                    */cu1*|*/metal) echo "stub: accelerated index unreachable" >&2; exit 1 ;;
                esac
            done
            """
        )
        if uv_fails_on_accel
        else ""
    )
    uv = bin_dir / "uv"
    uv.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [ "${{1:-}}" = "--version" ]; then echo "uv 0.0.0 (stub)"; exit 0; fi
            if [ "${{1:-}}" = "tool" ]; then printf '%s\\n' "$*" >> "{uv_log}"; fi
            {fail_branch}exit 0
            """
        ),
        encoding="utf-8",
    )
    uv.chmod(0o755)

    cf = bin_dir / "corpus-forge"
    cf.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    cf.chmod(0o755)
    return uv_log, cf


def _run_install_sh(
    bin_dir: Path,
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    uv_fails_on_accel: bool = False,
    nvidia_smi: str | None = "absent",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run ``install.sh`` from the repo root (so it finds the in-repo
    ``questions.toml`` and never networks out) with stub uv/corpus-forge.

    ``bin_dir`` is first on PATH so the stub ``nvidia-smi`` shadows any real
    one on the host, keeping accelerator detection deterministic.
    """
    uv_log, _ = _install_stubs(bin_dir, uv_fails_on_accel=uv_fails_on_accel, nvidia_smi=nvidia_smi)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir.parent),
        "NO_COLOR": "1",
        "CF_NON_INTERACTIVE": "1",
        "CF_BACKEND": "sqlite",
    }
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, uv_log


def _tool_install_lines(uv_log: Path) -> list[str]:
    if not uv_log.exists():
        return []
    return [ln for ln in uv_log.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Pure CUDA-version → wheel-variant mapping (bash)
# ---------------------------------------------------------------------------


def _extract_sh_helpers() -> str:
    """Pull the ``__cf_llama_backend_helpers`` block out of install.sh."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(
        r"# BEGIN __cf_llama_backend_helpers\n(.*?)\n# END __cf_llama_backend_helpers",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, (
        "Failed to extract __cf_llama_backend_helpers from install.sh — the "
        "BEGIN/END sentinels were removed or renamed."
    )
    body = match.group(1)
    assert "__cf_cuda_variant" in body, "helper block lost __cf_cuda_variant"
    return body


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("", "cu121"),
        ("11.8", "cu118"),
        ("12.0", "cu121"),
        ("12.1", "cu121"),
        ("12.2", "cu122"),
        ("12.3", "cu123"),
        ("12.4", "cu124"),
        ("12.5", "cu125"),
        ("12.6", "cu125"),  # 12.6+ clamps to the newest published
        ("13.0", "cu121"),  # unknown major → safe broad default
    ],
)
def test_sh_cuda_variant_mapping(version: str, expected: str) -> None:
    body = _extract_sh_helpers()
    script = body + f'\n__cf_cuda_variant "{version}"\n'
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip() == expected, (
        f"__cf_cuda_variant {version!r} → {result.stdout.strip()!r}, want {expected!r}"
    )


# ---------------------------------------------------------------------------
# Full-script behaviour (bash)
# ---------------------------------------------------------------------------


def test_auto_embedder_installs_cpu_llama_on_no_accel_box(tmp_path: Path) -> None:
    """``CF_EMBEDDER=auto`` recommends a llama-cpp lane, so the installer
    adds ``[llama-cpp]`` and points uv at the CPU wheel index (the test
    box has no nvidia-smi / is not Apple-Silicon arm64)."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(bin_dir, [], extra_env={"CF_EMBEDDER": "auto"})

    assert result.returncode == 0, f"stderr={result.stderr!r}; stdout={result.stdout!r}"
    lines = _tool_install_lines(uv_log)
    assert lines, f"uv tool install never ran; stdout={result.stdout!r}"
    install_line = lines[0]
    assert "llama-cpp" in install_line, (
        f"auto embedder must pull the [llama-cpp] extra; got {install_line!r}"
    )
    assert "/whl/cpu" in install_line and "--extra-index-url" in install_line, (
        f"no-accelerator auto install must select the CPU wheel index; got {install_line!r}"
    )
    # CPU path must never trigger the accel→cpu fallback (only one install).
    assert len(lines) == 1, f"CPU path should install once, not retry; got {lines!r}"


def test_auto_embedder_detects_cuda_and_selects_cuda_index(tmp_path: Path) -> None:
    """When ``nvidia-smi`` reports a CUDA version, ``CF_EMBEDDER=auto``
    selects the matching ``cuXXX`` wheel index (the silent-CPU-on-a-GPU-box
    trap the RFC fixes)."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(
        bin_dir, [], extra_env={"CF_EMBEDDER": "auto"}, nvidia_smi="cuda:12.4"
    )

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lines = _tool_install_lines(uv_log)
    assert lines and "llama-cpp" in lines[0], f"auto must pull llama-cpp; got {lines!r}"
    assert "/whl/cu124" in lines[0], (
        f"CUDA 12.4 must select the cu124 wheel index; got {lines[0]!r}"
    )
    assert "CUDA-enabled" in result.stdout, (
        f"the CUDA choice must be announced; stdout={result.stdout!r}"
    )


def test_st_embedder_does_not_force_llama(tmp_path: Path) -> None:
    """Regression guard: ``CF_EMBEDDER=st`` (not auto, no override, not
    join) must NOT pull llama-cpp — no surprise heavy extra on every
    install, and the existing install-smoke matrix (which pins
    ``CF_EMBEDDER=st``) stays byte-equivalent."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(bin_dir, [], extra_env={"CF_EMBEDDER": "st"})

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lines = _tool_install_lines(uv_log)
    assert lines, "uv tool install never ran"
    assert "llama-cpp" not in lines[0], f"st embedder must not pull llama-cpp; got {lines[0]!r}"
    assert "--extra-index-url" not in lines[0], (
        f"st embedder install must not add an extra-index; got {lines[0]!r}"
    )


def test_explicit_cuda_selects_index_then_falls_back_to_cpu(tmp_path: Path) -> None:
    """``--llama-backend cuda124`` selects the ``cu124`` index; when that
    fetch fails the installer WARNs and retries the CPU wheel without
    aborting (install never hard-fails on the accelerator step)."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(
        bin_dir,
        ["--llama-backend", "cuda124"],
        uv_fails_on_accel=True,
    )

    assert result.returncode == 0, (
        f"installer must exit 0 even when the accelerated wheel fetch fails; "
        f"rc={result.returncode}, stderr={result.stderr!r}, stdout={result.stdout!r}"
    )
    lines = _tool_install_lines(uv_log)
    assert any("/whl/cu124" in ln for ln in lines), (
        f"first attempt must target the cu124 index; got {lines!r}"
    )
    assert any("/whl/cpu" in ln for ln in lines), (
        f"fallback must retry against the CPU index; got {lines!r}"
    )
    assert "retrying with the CPU wheel" in result.stdout, (
        f"accelerated-fetch failure must surface the CPU-fallback warning; stdout={result.stdout!r}"
    )


def test_llama_backend_none_drops_extra_and_skips_index(tmp_path: Path) -> None:
    """``--llama-backend none`` removes llama-cpp even when the embedder
    choice would otherwise pull it, and never adds an extra-index."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(
        bin_dir,
        ["--llama-backend=none"],
        extra_env={"CF_EMBEDDER": "auto"},
    )

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lines = _tool_install_lines(uv_log)
    assert lines, "uv tool install never ran"
    assert "llama-cpp" not in lines[0], (
        f"--llama-backend none must drop the extra; got {lines[0]!r}"
    )
    assert "--extra-index-url" not in lines[0], (
        f"--llama-backend none must not add an extra-index; got {lines[0]!r}"
    )
    assert "skipped" in result.stdout, f"none should announce the skip; stdout={result.stdout!r}"


def test_llama_backend_cpu_flag_adds_extra_with_cpu_index(tmp_path: Path) -> None:
    """An explicit ``--llama-backend cpu`` forces the extra in (even with a
    non-auto embedder) and selects the CPU index."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(
        bin_dir,
        ["--llama-backend", "cpu"],
        extra_env={"CF_EMBEDDER": "st"},
    )

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lines = _tool_install_lines(uv_log)
    assert lines and "llama-cpp" in lines[0], (
        f"explicit cpu backend must force the extra in; got {lines!r}"
    )
    assert "/whl/cpu" in lines[0], f"must target the CPU index; got {lines[0]!r}"


# ---------------------------------------------------------------------------
# Static reverse-shields
# ---------------------------------------------------------------------------


def test_install_sh_text_mentions_llama_backend_flag_and_env() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "--llama-backend" in text, "install.sh must parse --llama-backend"
    assert "CF_LLAMA_BACKEND" in text, "install.sh must reference CF_LLAMA_BACKEND"
    assert "extra-index-url" in text, (
        "install.sh must thread an --extra-index-url for the accelerator wheel"
    )


def test_install_ps1_text_mentions_llama_backend_param_and_env() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "LlamaBackend" in text, "install.ps1 must declare -LlamaBackend"
    assert "CF_LLAMA_BACKEND" in text, "install.ps1 must reference CF_LLAMA_BACKEND"
    assert "extra-index-url" in text, (
        "install.ps1 must thread an --extra-index-url for the accelerator wheel"
    )


# ---------------------------------------------------------------------------
# PowerShell pure-mapping test (skipped when pwsh is absent)
# ---------------------------------------------------------------------------


def _extract_ps1_helpers() -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    match = re.search(
        r"# BEGIN __cf_llama_backend_helpers\s*\n(.*?)\n\s*# END __cf_llama_backend_helpers",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, (
        "Failed to extract __cf_llama_backend_helpers from install.ps1 — the "
        "BEGIN/END sentinels were removed or renamed."
    )
    return match.group(1)


@pytest.mark.skipif(PWSH is None, reason="pwsh not on PATH")
@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("", "cu121"),
        ("11.8", "cu118"),
        ("12.1", "cu121"),
        ("12.4", "cu124"),
        ("12.6", "cu125"),
        ("13.0", "cu121"),
    ],
)
def test_ps1_cuda_variant_mapping(version: str, expected: str) -> None:
    assert PWSH is not None  # narrowed for the type checker
    body = _extract_ps1_helpers()
    script = body + f"\nGet-CudaVariant -Ver '{version}'\n"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip() == expected, (
        f"Get-CudaVariant {version!r} → {result.stdout.strip()!r}, want {expected!r}"
    )


# ---------------------------------------------------------------------------
# Install smoke-matrix — accelerator detection → wheel index, and the
# "never source-builds" invariant (RFC fleet-7, the smoke-matrix item).
# ---------------------------------------------------------------------------

# Flags that would force uv to compile llama-cpp-python from an sdist
# instead of resolving the prebuilt accelerator wheel. The whole point of
# RFC fleet-7 is that a fresh install never pays the (slow, toolchain-
# fragile) CMAKE source build — so none of these may appear on the install
# line, in any accelerator scenario.
_SOURCE_BUILD_MARKERS = ("--no-binary", "--no-build-isolation", "--no-binary-package")


@pytest.mark.parametrize(
    ("scenario", "nvidia_smi", "args", "extra_env", "expect_llama", "expect_index"),
    [
        # CUDA-detected → CUDA wheel index.
        ("auto_cuda", "cuda:12.4", [], {"CF_EMBEDDER": "auto"}, True, "/whl/cu124"),
        # No accelerator → CPU wheel index (and never a source build).
        ("auto_cpu", "absent", [], {"CF_EMBEDDER": "auto"}, True, "/whl/cpu"),
        # Explicit --llama-backend cpu is honored even for a non-auto embedder.
        ("flag_cpu", "absent", ["--llama-backend", "cpu"], {"CF_EMBEDDER": "st"}, True, "/whl/cpu"),
        # Explicit --llama-backend none drops the extra entirely.
        ("flag_none", "absent", ["--llama-backend=none"], {"CF_EMBEDDER": "auto"}, False, None),
    ],
)
def test_install_sh_smoke_matrix(
    tmp_path: Path,
    scenario: str,
    nvidia_smi: str,
    args: list[str],
    extra_env: dict[str, str],
    expect_llama: bool,
    expect_index: str | None,
) -> None:
    """The accelerator-selection matrix in one place, with the cross-cutting
    never-source-build invariant asserted on every row.

    Individual branches have their own focused tests above; this matrix
    pins the *contract* the RFC item names — (detected accelerator | flag)
    → the right prebuilt wheel index, CPU never source-builds, and
    ``cpu`` / ``none`` overrides honored — so a refactor can't quietly
    regress one cell of it.
    """
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(bin_dir, args, extra_env=extra_env, nvidia_smi=nvidia_smi)

    assert result.returncode == 0, f"[{scenario}] stderr={result.stderr!r}"
    lines = _tool_install_lines(uv_log)
    assert lines, f"[{scenario}] uv tool install never ran; stdout={result.stdout!r}"
    install_line = lines[0]

    if expect_llama:
        assert "llama-cpp" in install_line, (
            f"[{scenario}] expected the [llama-cpp] extra; got {install_line!r}"
        )
        assert expect_index is not None and expect_index in install_line, (
            f"[{scenario}] expected wheel index {expect_index!r}; got {install_line!r}"
        )
        # The never-source-build guarantee: a prebuilt-wheel extra-index plus
        # the unsafe-best-match strategy that makes uv prefer that wheel over
        # a PyPI sdist. Without these, uv would compile from source.
        assert "--extra-index-url" in install_line, (
            f"[{scenario}] wheel index must be threaded as an --extra-index-url; "
            f"got {install_line!r}"
        )
        assert "--index-strategy" in install_line and "unsafe-best-match" in install_line, (
            f"[{scenario}] must pin --index-strategy unsafe-best-match so uv resolves the "
            f"prebuilt wheel rather than source-build; got {install_line!r}"
        )
    else:
        assert "llama-cpp" not in install_line, (
            f"[{scenario}] llama-cpp must be dropped; got {install_line!r}"
        )
        assert "--extra-index-url" not in install_line, (
            f"[{scenario}] no extra-index when llama-cpp is dropped; got {install_line!r}"
        )

    # No accelerator scenario, in any row, may force a source build.
    for marker in _SOURCE_BUILD_MARKERS:
        assert marker not in install_line, (
            f"[{scenario}] installer must never force a source build ({marker}); "
            f"got {install_line!r}"
        )


def test_install_sh_no_accelerator_installs_once_no_source_fallback(tmp_path: Path) -> None:
    """The no-accelerator CPU path resolves on the first try — it must not
    retry (the accel→CPU fallback is for a *failed* accelerated fetch) and
    must not source-build. Pins that the common laptop install is one clean
    prebuilt-wheel install."""
    bin_dir = tmp_path / "bin"
    result, uv_log = _run_install_sh(
        bin_dir, [], extra_env={"CF_EMBEDDER": "auto"}, nvidia_smi="absent"
    )
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lines = _tool_install_lines(uv_log)
    assert len(lines) == 1, f"CPU path must install exactly once; got {lines!r}"
    assert "/whl/cpu" in lines[0], f"CPU wheel index expected; got {lines[0]!r}"
    for marker in _SOURCE_BUILD_MARKERS:
        assert marker not in lines[0]
