<#
.SYNOPSIS
    corpus-forge installer for Windows.

.DESCRIPTION
    PowerShell mirror of install.sh. Reads the same
    ``packaging/install/questions.toml`` so the prompts stay in lock-
    step across POSIX and Windows.

.EXAMPLE
    iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 | iex

.EXAMPLE
    # Non-interactive mode for CI matrices:
    $env:CF_NON_INTERACTIVE = "1"
    $env:CF_BACKEND = "sqlite"
    $env:CF_MULTI_FORMAT = "yes"
    $env:CF_MCP = "yes"
    .\install.ps1

.NOTES
    All CF_* env vars are documented in ``packaging/install/questions.toml``.
#>

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
$LocalQuestions = Join-Path $ScriptDir 'packaging/install/questions.toml'
$RemoteQuestionsUrl =
    if ($env:CF_QUESTIONS_URL) { $env:CF_QUESTIONS_URL }
    else { 'https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/packaging/install/questions.toml' }

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

$extrasClean = ($allExtras | Sort-Object) -join ','
Write-Host ''
Write-Ok "Selected pip extras: $(if ($extrasClean) { $extrasClean } else { '<none>' })"
Write-Host ''

# ---------------------------------------------------------------------------
# Install via uv tool.
# ---------------------------------------------------------------------------

$pkgSpec = 'corpus-forge'
if ($extrasClean) { $pkgSpec = "corpus-forge[$extrasClean]" }

Write-Info "Running: $UvCmd tool install '$pkgSpec' --upgrade"
& $UvCmd tool install $pkgSpec --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Fail "uv tool install exited with code $LASTEXITCODE"
}
Write-Ok 'corpus-forge installed'

# ---------------------------------------------------------------------------
# Hand off to corpus-forge setup.  Forward every answer as a CF_* env
# var so the Python wizard can re-validate / skip re-prompting.
# ---------------------------------------------------------------------------

foreach ($q in $questions) {
    $v = $Answers[$q.id]
    if (-not [string]::IsNullOrEmpty($v)) {
        [Environment]::SetEnvironmentVariable($q.env, $v, 'Process')
    }
}

# ``uv tool`` symlinks into %USERPROFILE%\.local\bin by default. Ensure
# it's on PATH for this shell so the wizard handoff works without a
# restart.
$localBin = Join-Path $env:USERPROFILE '.local\bin'
if (-not ($env:PATH -split ';' | Where-Object { $_ -eq $localBin })) {
    $env:PATH = "$localBin;$env:PATH"
}

Write-Info 'Launching the post-install setup wizard'
if (Get-Command corpus-forge -ErrorAction SilentlyContinue) {
    if ($env:CF_NON_INTERACTIVE -eq '1') {
        corpus-forge setup --non-interactive
    } else {
        corpus-forge setup
    }
} else {
    Write-Warn2 "corpus-forge not on PATH yet. Open a new PowerShell and run ``corpus-forge setup``."
}

Write-Host ''
Write-Ok 'Done. Run `corpus-forge --help` to get started.'
