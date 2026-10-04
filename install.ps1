[CmdletBinding()]
param(
    [string]$RepoUrl = $env:ACKSTREET_REPO_URL,
    [string]$InstallDir = $env:ACKSTREET_INSTALL_DIR,
    [string]$VenvDir = $env:ACKSTREET_VENV_DIR,
    [switch]$NonInteractive,
    [switch]$Yes,
    [switch]$SkipOnboarding,
    [string]$Provider,
    [string]$Model,
    [string]$ApiKeyEnv,
    [string]$ApiKey,
    [string]$Gateway,
    [string]$TelegramToken,
    [string]$TelegramUserId,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  ok $msg" -ForegroundColor Green }
function Fail($msg) { throw $msg }

if (-not $RepoUrl) { $RepoUrl = "https://github.com/ObadiaNgenoh/ackstreet-agent.git" }
if (-not $InstallDir) { $InstallDir = Join-Path $HOME ".ackstreet\src" }
if (-not $VenvDir) { $VenvDir = Join-Path $InstallDir ".venv" }

try {
    Write-Info "Checking Python 3.9+"
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $pythonExe = & py -3 -c "import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
        if ($LASTEXITCODE -ne 0) { $pythonExe = $null }
    }
    if (-not $pythonExe) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) { Fail "Python 3.9+ is required. Install from https://www.python.org/downloads/windows/" }
        $pythonExe = & python -c "import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
        if ($LASTEXITCODE -ne 0) { Fail "Python 3.9+ is required." }
    }
    $pythonExe = $pythonExe.Trim()
    Write-Ok "python: $pythonExe"

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail "Git is required. Install Git for Windows: https://git-scm.com/download/win"
    }
    Write-Ok "git found"

    $localCheckout = Test-Path (Join-Path $PSScriptRoot "pyproject.toml")
    if ($localCheckout) {
        Write-Info "Using local checkout: $PSScriptRoot"
        $InstallDir = $PSScriptRoot
        if (-not $env:ACKSTREET_VENV_DIR) { $VenvDir = Join-Path $InstallDir ".venv" }
    } else {
        if (Test-Path (Join-Path $InstallDir ".git")) {
            Write-Info "Updating existing checkout at $InstallDir"
            git -C $InstallDir pull --ff-only | Out-Null
        } else {
            Write-Info "Cloning repository to $InstallDir"
            New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
            git clone --depth 1 $RepoUrl $InstallDir | Out-Null
        }
    }

    if (-not (Test-Path $VenvDir)) {
        Write-Info "Creating virtualenv at $VenvDir"
        & $pythonExe -m venv $VenvDir
    } else {
        Write-Info "Reusing virtualenv at $VenvDir"
    }

    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) { Fail "Virtualenv is missing python at $venvPython" }

    Write-Info "Installing package"
    & $venvPython -m pip install --upgrade pip setuptools wheel | Out-Null
    & $venvPython -m pip install -e $InstallDir | Out-Null

    $ackstreetExe = Join-Path $VenvDir "Scripts\ackstreet.exe"
    if (-not (Test-Path $ackstreetExe)) { Fail "ackstreet entry point not found" }
    Write-Ok "installed: $ackstreetExe"

    Write-Info "Initializing state"
    & $ackstreetExe init | Out-Null

    if ($SkipOnboarding -or $env:ACKSTREET_SKIP_ONBOARDING -eq "1") {
        Write-Host "Skipping onboarding. Run later: ackstreet-install"
        exit 0
    }

    $args = @("-m", "ackstreet.installer.cli", "--os", "windows")
    if ($NonInteractive -or $env:CI -eq "true") { $args += "--non-interactive" }
    if ($Yes) { $args += "--yes" }
    if ($SkipDoctor) { $args += "--skip-doctor" }
    if ($Provider) { $args += @("--provider", $Provider) }
    if ($Model) { $args += @("--model", $Model) }
    if ($ApiKeyEnv) { $args += @("--api-key-env", $ApiKeyEnv) }
    if ($ApiKey) { $args += @("--api-key", $ApiKey) }
    if ($Gateway) { $args += @("--gateway", $Gateway) }
    if ($TelegramToken) { $args += @("--telegram-token", $TelegramToken) }
    if ($TelegramUserId) { $args += @("--telegram-user-id", $TelegramUserId) }

    Write-Info "Launching onboarding wizard"
    & $venvPython @args
    if ($LASTEXITCODE -ne 0) { Fail "Onboarding did not complete." }

    Write-Host ""
    Write-Host "Done. Next commands:" -ForegroundColor Green
    Write-Host "  ackstreet doctor"
    Write-Host "  ackstreet chat"
    Write-Host "  ackstreet run \"your task here\""
    Write-Host ""
    Write-Host "Optional background task:" -ForegroundColor Cyan
    Write-Host "  Use Task Scheduler to run: $ackstreetExe serve telegram"
} catch {
    Write-Error $_
    exit 1
}
