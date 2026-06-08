<#
.SYNOPSIS
    corpus-forge installer for Windows.

.DESCRIPTION
    PowerShell mirror of install.sh. Reads the same
    ``packaging/install/questions.toml`` so the prompts stay in lock-
    step across POSIX and Windows.

.PARAMETER Join
    Join an existing corpus-forge fleet at this Postgres DSN (RFC
    fleet-3). Skips the question tree (shared scope is pulled from the
    fleet's primary), invokes ``corpus-forge setup --non-interactive
    --join <dsn>``, then runs ``corpus-forge doctor`` as a smoke
    check. Explicitly does NOT run ``corpus-forge migrate`` — the
    primary owns schema lifecycle. ``$env:CF_JOIN_DSN`` is the env-var
    equivalent.

.EXAMPLE
    # One-line install — download then call.  Don't use ``iwr | iex``:
    # ``Invoke-Expression`` doesn't reliably parse scripts with a top-
    # level ``param()`` block, which this script has for ``-Join``.
    iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1

.EXAMPLE
    # Non-interactive mode for CI matrices:
    $env:CF_NON_INTERACTIVE = "1"
    $env:CF_BACKEND = "sqlite"
    $env:CF_MULTI_FORMAT = "yes"
    $env:CF_MCP = "yes"
    .\install.ps1

.EXAMPLE
    # Onboard a second machine onto an existing fleet — one-line form:
    .\install.ps1 -Join 'postgresql://primary.fleet:5432/corpus'

.EXAMPLE
    # Or via env (works with streamed download + call):
    $env:CF_JOIN_DSN = 'postgresql://primary.fleet:5432/corpus'; iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1

.NOTES
    All CF_* env vars are documented in ``packaging/install/questions.toml``.
#>

param(
    # RFC fleet-3 item 6 — one-line fleet onboarding. Empty means "no
    # join requested"; we then fall back to ``$env:CF_JOIN_DSN`` so the
    # ``iwr | iex`` form (which can't pass positional params) can drive
    # the same code path via env.
    [string]$Join = ''
)

# RFC fleet-3 item 6 — wire the -Join param through to the env var the
# Python wizard already reads (``envvar='CF_JOIN_DSN'`` on the
# ``--join`` typer option in ``corpus_forge/cli.py``), so flag and env
# entry points are one downstream code path. The flag wins over a
# pre-set env so callers can override.
if (-not [string]::IsNullOrEmpty($Join)) {
    $env:CF_JOIN_DSN = $Join
}
$JoinDsn = $env:CF_JOIN_DSN
$IsJoinMode = -not [string]::IsNullOrEmpty($JoinDsn)

# StrictMode helps surface typos before they trash the user's config.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Colour output (best-effort; PowerShell 7 has $PSStyle, 5.1 has Write-Host).
# ---------------------------------------------------------------------------

function Write-Info($msg) { Write-Host "→ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Fail($msg) {
    Write-Host "✗ $msg" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Locate the question tree (clone-and-run vs iwr-pipe).
# ---------------------------------------------------------------------------

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$LocalQuestions = Join-Path $ScriptDir 'corpus_forge/setup/questions.toml'
$RemoteQuestionsUrl =
    if ($env:CF_QUESTIONS_URL) { $env:CF_QUESTIONS_URL }
    else { 'https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/corpus_forge/setup/questions.toml' }

if (Test-Path $LocalQuestions) {
    $QuestionsPath = $LocalQuestions
    Write-Info "Using local question tree: $QuestionsPath"
} else {
    $QuestionsPath = New-TemporaryFile
    Write-Info "Fetching question tree from $RemoteQuestionsUrl"
    try {
        Invoke-WebRequest -Uri $RemoteQuestionsUrl -OutFile $QuestionsPath -UseBasicParsing
    } catch {
        Write-Fail "Failed to download $RemoteQuestionsUrl — $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# Provision uv.
# ---------------------------------------------------------------------------

function Find-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return 'uv' }
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

$UvCmd = Find-Uv
if ($UvCmd) {
    Write-Ok "uv found ($(& $UvCmd --version))"
} else {
    Write-Info "Installing uv (Astral) — official PowerShell installer"
    try {
        # ``irm | iex`` is Astral's documented one-liner. Wrap in
        # try/catch so a network blip surfaces as a clean error.
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Fail "uv installer failed: $($_.Exception.Message). Install manually: https://docs.astral.sh/uv/"
    }
    $UvCmd = Find-Uv
    if (-not $UvCmd) {
        Write-Fail "uv installer reported success but binary not found. Restart PowerShell and retry."
    }
    Write-Ok "uv installed ($(& $UvCmd --version))"
}

# ---------------------------------------------------------------------------
# Question-tree parser.  Same narrow TOML subset as install.sh's awk
# parser — emits a list of hashtables describing each [[question]].
# ---------------------------------------------------------------------------

function Read-Questions {
    param([string]$Path)

    $result = New-Object System.Collections.ArrayList
    $current = $null

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrEmpty($line) -or $line.StartsWith('#')) { continue }

        if ($line -eq '[[question]]') {
            if ($current) { [void]$result.Add($current) }
            $current = @{
                id = ''; type = ''; default = ''; env = '';
                depends_on = ''; prompt = ''; warn = ''; extras = @()
            }
            continue
        }
        if ($line.StartsWith('[')) {
            if ($current) { [void]$result.Add($current); $current = $null }
            continue
        }
        if (-not $current) { continue }

        # key = value
        $idx = $line.IndexOf('=')
        if ($idx -lt 0) { continue }
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()

        if ($key -eq 'extras') {
            $listContent = $val.Trim('[', ']').Trim()
            $items = $listContent.Split(',') | ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ }
            $current.extras = @($items)
            continue
        }

        # Strip surrounding double-quotes.
        if ($val.StartsWith('"') -and $val.EndsWith('"')) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        if ($current.ContainsKey($key)) {
            $current[$key] = $val
        }
    }
    if ($current) { [void]$result.Add($current) }

    return $result
}

# ---------------------------------------------------------------------------
# Answers + dependency predicate.
# ---------------------------------------------------------------------------

$Answers = @{}

function Test-Dependency {
    param([string]$DependsOn)
    if (-not $DependsOn) { return $true }
    $parts = $DependsOn.Split('=', 2)
    if ($parts.Count -lt 2) { return $true }
    $depId = $parts[0]
    $depVal = $parts[1]
    return ($Answers[$depId] -eq $depVal)
}

function Read-Answer {
    param([hashtable]$Q)

    $hint = ''
    switch ($Q.type) {
        'yes_no' {
            $hint = if ($Q.default -eq 'no') { ' [y/N]' } else { ' [Y/n]' }
        }
    }

    # Non-interactive: pull from env, fall back to default.
    if ($env:CF_NON_INTERACTIVE -eq '1') {
        $envVal = [Environment]::GetEnvironmentVariable($Q.env)
        $answer = if ([string]::IsNullOrEmpty($envVal)) { $Q.default } else { $envVal }
        $Answers[$Q.id] = $answer
        Write-Info "$($Q.id) = $answer (from `$$($Q.env))"
        return
    }

    if ($Q.warn) { Write-Warn2 $Q.warn }

    while ($true) {
        $prompt = "{0}{1} (default: {2}) " -f $Q.prompt, $hint, $Q.default
        $answer = Read-Host $prompt
        if ([string]::IsNullOrEmpty($answer)) { $answer = $Q.default }

        if ($Q.type -eq 'yes_no') {
            switch -Regex ($answer) {
                '^(y|Y|yes|YES|Yes)$' { $answer = 'yes' }
                '^(n|N|no|NO|No)$'    { $answer = 'no' }
                default {
                    Write-Warn2 "Please answer y or n."
                    continue
                }
            }
        }
        $Answers[$Q.id] = $answer
        break
    }
}

# ---------------------------------------------------------------------------
# Walk the question tree.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host 'corpus-forge installer' -ForegroundColor White
Write-Host ''

$questions = Read-Questions -Path $QuestionsPath
$allExtras = New-Object System.Collections.Generic.HashSet[string]

# RFC fleet-3 item 6 — join mode SKIPS the question tree. The fleet's
# primary owns the shared scope (embedders / retrieval / classifier
# chains); the wizard pulls all of that via ``setup --join <dsn>``.
# We install ``corpus-forge`` plain (no extras) — the operator can opt
# in to ``[hf]`` / ``[mcp]`` later.
if ($IsJoinMode) {
    Write-Info "Join mode — skipping question tree (shared scope comes from primary)."
} else {
    foreach ($q in $questions) {
        if (-not (Test-Dependency -DependsOn $q.depends_on)) { continue }
        Read-Answer -Q $q

        $answer = $Answers[$q.id]
        if ($q.extras -and $q.extras.Count -gt 0 -and $answer -ne 'no' -and $answer -ne 'none' -and -not [string]::IsNullOrEmpty($answer)) {
            foreach ($e in $q.extras) { [void]$allExtras.Add($e) }
        }
    }

    # backend=sqlite implies the [sqlite] extra (mirrors install.sh).
    if ($Answers['backend'] -eq 'sqlite') {
        [void]$allExtras.Add('sqlite')
    }
}

$extrasClean = ($allExtras | Sort-Object) -join ','
Write-Host ''
Write-Ok "Selected pip extras: $(if ($extrasClean) { $extrasClean } else { '<none>' })"
Write-Host ''

# ---------------------------------------------------------------------------
# Install via uv tool.
# ---------------------------------------------------------------------------

# ``CF_INSTALL_FROM`` lets the install-smoke E2E workflow point at the
# checked-out source tree so the installer is exercised against the
# current branch (the package isn't on PyPI yet for un-released
# commits).  Default empty → install ``corpus-forge`` from PyPI.
#
# uv's CLI: ``uv tool install '<path>[extras]'`` installs the local
# package with its extras; ``--from`` is for the cross-name case
# (install foo's CLI from bar's package) and conflicts when the
# install spec names a package.
if ($env:CF_INSTALL_FROM) {
    Write-Info "Installing from local source: $($env:CF_INSTALL_FROM)"
    if ($extrasClean) {
        $pkgSpec = "$($env:CF_INSTALL_FROM)[$extrasClean]"
    } else {
        $pkgSpec = $env:CF_INSTALL_FROM
    }
} else {
    if ($extrasClean) {
        $pkgSpec = "corpus-forge[$extrasClean]"
    } else {
        $pkgSpec = 'corpus-forge'
    }
}

# corpus-forge requires Python >=3.11,<3.14. Pin a compatible
# interpreter explicitly. ``CF_PYTHON`` overrides the default if the
# user wants a specific version (e.g. ``3.12``).
$pinPython = if ($env:CF_PYTHON) { $env:CF_PYTHON } else { '3.11' }

Write-Info "Running: $UvCmd tool install --python $pinPython '$pkgSpec' --upgrade"
& $UvCmd tool install --python $pinPython $pkgSpec --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Fail "uv tool install exited with code $LASTEXITCODE"
}
Write-Ok 'corpus-forge installed'

# ---------------------------------------------------------------------------
# Hand off to corpus-forge setup.  Forward every answer as a CF_* env
# var so the Python wizard can re-validate / skip re-prompting.
# ---------------------------------------------------------------------------

# In join mode the question tree was skipped, so there are no answers
# to forward as CF_* env vars — the wizard pulls everything from the
# fleet's published shared scope via ``--join``.
if (-not $IsJoinMode) {
    foreach ($q in $questions) {
        $v = $Answers[$q.id]
        if (-not [string]::IsNullOrEmpty($v)) {
            [Environment]::SetEnvironmentVariable($q.env, $v, 'Process')
        }
    }
}

# ``uv tool`` symlinks into %USERPROFILE%\.local\bin by default. Ensure
# it's on PATH for this shell so the wizard handoff works without a
# restart.
$localBin = Join-Path $env:USERPROFILE '.local\bin'
if (-not ($env:PATH -split ';' | Where-Object { $_ -eq $localBin })) {
    $env:PATH = "$localBin;$env:PATH"
}

# BEGIN __cf_post_install_handoff
Write-Info 'Launching the post-install setup wizard'
if (Get-Command corpus-forge -ErrorAction SilentlyContinue) {
    if ($IsJoinMode) {
        # Join mode — onboarding a new host onto an existing fleet.
        # The wizard connects to the shared Postgres, verifies the
        # corpus schema is present, registers this host in
        # ``corpus.hosts``, and renders a local config pre-loaded
        # with the fleet's published shared scope.
        $LASTEXITCODE = 0
        corpus-forge setup --non-interactive --join $JoinDsn
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "corpus-forge setup --join failed (exit $LASTEXITCODE). Fix the reported error and re-run the installer."
        }

        # Run ``doctor`` as a smoke check (DSN reachability, embedder
        # config sanity, host-id stability). Tolerate failure for the
        # same reason ``migrate`` is tolerated on the non-join path:
        # a transient network blip shouldn't leave the operator with a
        # half-installed CLI.
        $doctorLog = New-TemporaryFile
        $doctorFailed = $false
        $LASTEXITCODE = 0
        try {
            & corpus-forge doctor *>&1 | Out-File -FilePath $doctorLog -Encoding utf8
            if ($LASTEXITCODE -ne 0) { $doctorFailed = $true }
        } catch {
            Add-Content -Path $doctorLog -Value $_.Exception.Message
            $doctorFailed = $true
            $LASTEXITCODE = 0
        }
        if ($doctorFailed) {
            Write-Warn2 "corpus-forge doctor reported issues — see $doctorLog for details. Re-run ``corpus-forge doctor`` once the fleet primary is reachable."
            $LASTEXITCODE = 0
        } else {
            Remove-Item -Path $doctorLog -ErrorAction SilentlyContinue
        }

        Write-Host ''
        Write-Ok "Joined fleet at $JoinDsn."
        Write-Info "Next: ``corpus-forge bench embed --all`` (record this host's throughput), then ``corpus-forge service install`` (run the daemon)."
    } else {
        # ALWAYS pass --non-interactive (mirrors the install.sh fix). The
        # CF_* env vars are already populated above. If we omit
        # --non-interactive the wizard reprompts on stdin this script has
        # already consumed (or was never a TTY when piped via
        # ``iwr | iex``), silently discarding the user's answers.
        $LASTEXITCODE = 0
        corpus-forge setup --non-interactive
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "corpus-forge setup failed (exit $LASTEXITCODE). Fix the reported error and re-run the installer."
        }

        # Run schema migrations so first-run `ingest`/`embed` doesn't fail
        # on an empty DB. Tolerate failure (Postgres unreachable, etc.) —
        # warn and continue so the user isn't left with a half-installed CLI.
        # $ErrorActionPreference = 'Stop' is in force, so we wrap in
        # try/catch AND also inspect $LASTEXITCODE to cover native-exe
        # non-zero returns that don't throw.
        $migrateLog = New-TemporaryFile
        $migrateFailed = $false
        $LASTEXITCODE = 0
        try {
            & corpus-forge migrate *>&1 | Out-File -FilePath $migrateLog -Encoding utf8
            if ($LASTEXITCODE -ne 0) { $migrateFailed = $true }
        } catch {
            Add-Content -Path $migrateLog -Value $_.Exception.Message
            $migrateFailed = $true
            $LASTEXITCODE = 0
        }
        if ($migrateFailed) {
            Write-Warn2 "corpus-forge migrate failed — see $migrateLog for details. Re-run ``corpus-forge migrate`` once your database is reachable."
            $LASTEXITCODE = 0
        } else {
            Remove-Item -Path $migrateLog -ErrorAction SilentlyContinue
        }

        Write-Host ''
        Write-Ok 'Done. Run `corpus-forge --help` to get started.'
    }
} else {
    if ($IsJoinMode) {
        Write-Warn2 "corpus-forge not on PATH yet. Open a new PowerShell and run ``corpus-forge setup --join $JoinDsn``."
    } else {
        Write-Warn2 "corpus-forge not on PATH yet. Open a new PowerShell and run ``corpus-forge setup ; corpus-forge migrate``."
    }
    Write-Host ''
    Write-Ok 'Done. Run `corpus-forge --help` to get started.'
}
# END __cf_post_install_handoff

# Explicit exit 0 — the migrate failure path may leave $LASTEXITCODE
# non-zero on some PS versions; this guarantees `iwr | iex` callers
# don't propagate a stale 1.
exit 0
