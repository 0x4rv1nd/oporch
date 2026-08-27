# oporch installer — https://github.com/0x4rv1nd/oporch
# Usage:
#   Invoke-WebRequest -Uri https://raw.githubusercontent.com/0x4rv1nd/oporch/master/install.ps1 -OutFile install.ps1
#   Unblock-File .\install.ps1
#   .\install.ps1
#
# Or single line:
#   irm https://raw.githubusercontent.com/0x4rv1nd/oporch/master/install.ps1 | iex
param(
    [switch]$Force
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO = "https://github.com/0x4rv1nd/oporch"
$PYPI_NAME = "oporch"

# ── colours ───────────────────────────────────────────────────────────────────
function Info    { param($msg) Write-Host "[oporch] $msg" -ForegroundColor Cyan }
function Success { param($msg) Write-Host "[oporch] ✓ $msg" -ForegroundColor Green }
function Warn    { param($msg) Write-Host "[oporch] ⚠ $msg" -ForegroundColor Yellow }
function Fail    { param($msg) Write-Host "[oporch] ✗ $msg" -ForegroundColor Red; exit 1 }

# ── header ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ⚡ oporch installer" -ForegroundColor White
Write-Host "  Multi-Agent Orchestration System for OpenCode" -ForegroundColor Gray
Write-Host ""

# ── python version check ──────────────────────────────────────────────────────
$python = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -match "^(\d+)\.(\d+)$") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 12) { $python = $candidate; break }
        }
    } catch { continue }
}
if (-not $python) { Fail "Python 3.12+ is required. Download from https://python.org" }
Info "Python $( & $python --version ) ✓"

# ── prerequisite: opencode ────────────────────────────────────────────────────
if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Warn "opencode CLI not found. Install from https://opencode.ai before using oporch."
}

# ── pick installer ────────────────────────────────────────────────────────────
$installer = "pip"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $installer = "uv"
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    $installer = "pipx"
}
Info "Using installer: $installer"

# ── install ───────────────────────────────────────────────────────────────────
switch ($installer) {
    "uv"   { uv tool install $PYPI_NAME }
    "pipx" { pipx install $PYPI_NAME }
    "pip"  {
        Warn "Neither uv nor pipx found. Installing with pip."
        & $python -m pip install --user $PYPI_NAME
    }
}

if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { Fail "Installation failed." }

# ── verify ────────────────────────────────────────────────────────────────────
$opcmd = Get-Command oporch -ErrorAction SilentlyContinue
if ($opcmd) {
    Success "oporch installed at: $($opcmd.Source)"
    Write-Host ""
    Write-Host "  Get started:" -ForegroundColor White
    Write-Host "    cd your-project"
    Write-Host "    oporch"
    Write-Host ""
    Write-Host "  Optional (token compression):" -ForegroundColor White
    Write-Host "    pip install `"headroom-ai[all]`""
    Write-Host "    headroom wrap opencode"
    Write-Host "    oporch"
    Write-Host ""
    Write-Host "  Docs: $REPO" -ForegroundColor Cyan
} else {
    Warn "oporch not found in PATH after install."
    Warn "You may need to add the Python Scripts folder to your PATH:"
    Warn "  `$env:PATH += `";`$env:APPDATA\Python\Scripts`""
    Warn "Or restart your terminal."
}
