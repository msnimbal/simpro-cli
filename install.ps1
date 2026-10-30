# One-step installer for Windows (PowerShell 5.1 or newer). Run from this folder:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
#   1. installs the `simpro` command (via pipx)
#   2. installs the /simpro skill for Claude Code (works from any folder)
#   3. asks for your simPRO API details and tests the connection
#
# Note: keep this file ASCII-only (Windows PowerShell 5.1 misreads UTF-8 without a BOM).

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Say($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host ""; Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }

# --- Python 3.9+ ------------------------------------------------------------
$PYEXE = $null
$PYARGS = @()
$candidates = @(
    @{ Exe = "py";      Args = @("-3") },
    @{ Exe = "python";  Args = @() },
    @{ Exe = "python3"; Args = @() }
)
foreach ($c in $candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $a = $c.Args
        & $c.Exe @a -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $PYEXE = $c.Exe; $PYARGS = $c.Args; break }
    } catch { }
}
if (-not $PYEXE) {
    Die "Python 3.9 or newer is required. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
}

# --- pipx -------------------------------------------------------------------
& $PYEXE @PYARGS -m pipx --version *> $null
if ($LASTEXITCODE -ne 0) {
    Say "Installing pipx"
    & $PYEXE @PYARGS -m pip install --user pipx
    if ($LASTEXITCODE -ne 0) { Die "Could not install pipx. See https://pipx.pypa.io/stable/installation/" }
}
& $PYEXE @PYARGS -m pipx ensurepath *> $null

Say "Installing the simpro command"
& $PYEXE @PYARGS -m pipx install --force .
if ($LASTEXITCODE -ne 0) { Die "Could not install simpro with pipx." }

# Make `simpro` usable in this window right away.
$binDir = $null
try { $binDir = (& $PYEXE @PYARGS -m pipx environment --value PIPX_BIN_DIR 2>$null | Out-String).Trim() } catch { }
if (-not $binDir) { $binDir = Join-Path $env:USERPROFILE ".local\bin" }
$env:Path = "$binDir;$env:Path"
if (-not (Get-Command simpro -ErrorAction SilentlyContinue)) {
    Die "simpro installed but was not found on PATH. Close this window, open a new PowerShell and run: simpro setup"
}

# --- Claude Code skill ------------------------------------------------------
Say "Installing the /simpro skill for Claude Code"
$skills = Join-Path $env:USERPROFILE ".claude\skills"
New-Item -ItemType Directory -Force -Path $skills | Out-Null
$dest = Join-Path $skills "simpro"
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Copy-Item -Recurse (Join-Path $PSScriptRoot ".claude\skills\simpro") $dest

# --- Credentials ------------------------------------------------------------
if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) {
    Say "Connecting to simPRO"
    simpro setup
    Write-Host ""
    Write-Host "Close this window and open a new one before starting Claude Code, so it can find the simpro command." -ForegroundColor Yellow
} else {
    Say "Done. Open a new PowerShell window and run:  simpro setup"
}
