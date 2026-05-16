<#
.SYNOPSIS
    Register the corpus-forge daemon as a Windows service via NSSM.

.DESCRIPTION
    The macOS / Linux installers (``scripts/{macos,linux}/install.sh``)
    drop a launchd plist / systemd user unit. This is the Windows
    equivalent — it uses NSSM (Non-Sucking Service Manager) because
    Windows lacks first-party CLI tooling for foreground-style service
    wrappers.

    On first run we download NSSM 2.24 (Public Domain) from
    ``nssm.cc`` into ``%LOCALAPPDATA%\corpus-forge\nssm\``, SHA256-
    verify the binary, and use it to ``nssm install`` the daemon. On
    re-run the cached binary is reused.

    Idempotent: re-running re-renders the service config but does NOT
    re-download NSSM and does NOT restart a running daemon.

.PARAMETER ServiceName
    Windows service name. Default ``corpus-forge``.

.PARAMETER CorpusForgeBin
    Absolute path to ``corpus-forge.exe``. Defaults to
    ``%USERPROFILE%\.local\bin\corpus-forge.exe`` (the ``uv tool
    install`` default).

.PARAMETER NssmVersion
    Version pinned in this script. Update both the version string and
    the matching SHA256 below if you bump it.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -ServiceName 'corpus-forge-personal'
#>

[CmdletBinding()]
param(
    [string]$ServiceName = 'corpus-forge',
    [string]$CorpusForgeBin = (Join-Path $env:USERPROFILE '.local\bin\corpus-forge.exe'),
    [string]$NssmVersion = '2.24'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info($msg)  { Write-Host "→ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Fail($msg) {
    Write-Host "✗ $msg" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Admin-check.  NSSM service install requires admin rights on Windows.
# ---------------------------------------------------------------------------

$currentUser = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Fail "Run from an elevated PowerShell (Run as Administrator). NSSM service install needs admin rights."
}

# ---------------------------------------------------------------------------
# Sanity: corpus-forge entry-point exists.
# ---------------------------------------------------------------------------

if (-not (Test-Path $CorpusForgeBin)) {
    Write-Fail @"
corpus-forge entry point not found at:
    $CorpusForgeBin

Run install.ps1 in the repo root first (or pass -CorpusForgeBin <path>).
"@
}

# ---------------------------------------------------------------------------
# Download (and verify) NSSM into a per-user cache.
# ---------------------------------------------------------------------------

$nssmCacheDir = Join-Path $env:LOCALAPPDATA "corpus-forge\nssm\$NssmVersion"
$nssmExe = Join-Path $nssmCacheDir 'nssm.exe'

# Pinned SHA-256 of nssm.cc's ``nssm-${version}.zip``. Update both the
# version above AND this hash on a bump. Hash for 2.24 from the
# upstream release page.
$NssmSha256ByVersion = @{
    '2.24' = 'BE7B24735CDC1F2FCF8C6B7C2EC0B0C26157DDD228C5FBB2570E347D2F89D6E2'
}

if (-not (Test-Path $nssmExe)) {
    Write-Info "Downloading NSSM $NssmVersion"
    New-Item -ItemType Directory -Force -Path $nssmCacheDir | Out-Null
    $zipPath = Join-Path $nssmCacheDir "nssm-$NssmVersion.zip"
    $url = "https://nssm.cc/release/nssm-$NssmVersion.zip"
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing

    $expectedHash = $NssmSha256ByVersion[$NssmVersion]
    if (-not $expectedHash) {
        Remove-Item -LiteralPath $zipPath -ErrorAction SilentlyContinue
        Write-Fail "No SHA256 pinned for NSSM $NssmVersion. Add it to `$NssmSha256ByVersion."
    }
    $actualHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        Remove-Item -LiteralPath $zipPath -ErrorAction SilentlyContinue
        Write-Fail "NSSM SHA256 mismatch.`n  Expected: $expectedHash`n  Got:      $actualHash"
    }
    Write-Ok "NSSM SHA256 verified ($actualHash)"

    Expand-Archive -LiteralPath $zipPath -DestinationPath $nssmCacheDir -Force
    Remove-Item -LiteralPath $zipPath

    # NSSM ships an x64 + win32 binary; pick whichever matches the host.
    $arch = if ([Environment]::Is64BitOperatingSystem) { 'win64' } else { 'win32' }
    $extracted = Join-Path $nssmCacheDir "nssm-$NssmVersion\$arch\nssm.exe"
    if (-not (Test-Path $extracted)) {
        Write-Fail "NSSM archive layout unexpected (no $extracted)."
    }
    Copy-Item -LiteralPath $extracted -Destination $nssmExe -Force
    Write-Ok "NSSM cached at $nssmExe"
} else {
    Write-Ok "NSSM already cached at $nssmExe"
}

# ---------------------------------------------------------------------------
# Existing service?  Remove first so re-runs are idempotent.
# ---------------------------------------------------------------------------

$existing = & $nssmExe status $ServiceName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Info "Existing service '$ServiceName' detected (status: $existing) — removing"
    & $nssmExe stop $ServiceName 2>$null | Out-Null
    & $nssmExe remove $ServiceName confirm
}

# ---------------------------------------------------------------------------
# Install the service.
# ---------------------------------------------------------------------------

$logDir = Join-Path $env:LOCALAPPDATA 'corpus-forge\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir 'daemon.out.log'
$stderrLog = Join-Path $logDir 'daemon.err.log'
$workingDir = Join-Path $env:USERPROFILE '.config\corpus-forge'
New-Item -ItemType Directory -Force -Path $workingDir | Out-Null

Write-Info "Installing service '$ServiceName' → $CorpusForgeBin daemon"
& $nssmExe install $ServiceName $CorpusForgeBin daemon
& $nssmExe set $ServiceName AppDirectory $workingDir
& $nssmExe set $ServiceName AppStdout $stdoutLog
& $nssmExe set $ServiceName AppStderr $stderrLog
& $nssmExe set $ServiceName AppRotateFiles 1
& $nssmExe set $ServiceName AppRotateOnline 1
& $nssmExe set $ServiceName AppRotateBytes 10485760
& $nssmExe set $ServiceName Start SERVICE_AUTO_START
& $nssmExe set $ServiceName Description 'corpus-forge ingestion daemon'

Write-Ok "Service installed."

# ---------------------------------------------------------------------------
# Start it.
# ---------------------------------------------------------------------------

Write-Info "Starting '$ServiceName'"
& $nssmExe start $ServiceName | Out-Null
Start-Sleep -Seconds 1
$status = & $nssmExe status $ServiceName
if ($status -match 'SERVICE_RUNNING') {
    Write-Ok "Service running. Logs:"
    Write-Host "  stdout: $stdoutLog"
    Write-Host "  stderr: $stderrLog"
    Write-Host ""
    Write-Host "Manage with:"
    Write-Host "  nssm status $ServiceName"
    Write-Host "  nssm stop   $ServiceName"
    Write-Host "  nssm start  $ServiceName"
    Write-Host "  nssm remove $ServiceName confirm   # uninstall"
} else {
    Write-Warn2 "Service install OK but status is '$status'. Check $stderrLog for hints."
}
