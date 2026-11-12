# Pluggin' - one-command Windows build.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Produces dist\PLG.exe (onefile, windowed, G icon). If Inno Setup's ISCC.exe is
# found it also compiles the installer -> dist\installer\Pluggin-Setup-*.exe.
#
# Prereqs: Python deps installed (pip install -r requirements.txt). The React UI
# in web\dist must be built (npm --prefix web run build); this script rebuilds it
# automatically if npm is available and web\dist is missing.
#
# Native tools (python/npm/iscc) are checked via $LASTEXITCODE rather than a
# global ErrorActionPreference=Stop, because redirecting a native command's
# stderr under Stop turns benign output into a terminating error in Win PowerShell.

$root = $PSScriptRoot
Set-Location $root

Write-Host "=== Pluggin' build ===" -ForegroundColor Cyan

# 1. React UI (bundled into the exe by PLG.spec).
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "Building web UI (npm run build)..." -ForegroundColor Yellow
    npm --prefix web ci
    npm --prefix web run build
    if ($LASTEXITCODE -ne 0) { Write-Host "npm build failed." -ForegroundColor Red; exit 1 }
} elseif (-not (Test-Path "$root\web\dist\index.html")) {
    Write-Host "web\dist missing and npm not found. Build the UI first: npm --prefix web run build" -ForegroundColor Red
    exit 1
}

# 2. Regenerate the G app icon from the logo source (idempotent).
if (Test-Path "$root\assets\logo_icon.png") {
    python scripts\make_icon.py
}

# 3. PyInstaller (pip install is idempotent - no fragile import probe).
Write-Host "Ensuring PyInstaller is installed..." -ForegroundColor Yellow
python -m pip install --disable-pip-version-check --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Host "Could not install PyInstaller." -ForegroundColor Red; exit 1 }

Write-Host "Building PLG.exe (this takes a few minutes)..." -ForegroundColor Yellow
python -m PyInstaller --noconfirm PLG.spec
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller build failed (exit $LASTEXITCODE)." -ForegroundColor Red; exit 1 }

if (-not (Test-Path "$root\dist\PLG.exe")) {
    Write-Host "Build failed: dist\PLG.exe not produced." -ForegroundColor Red
    exit 1
}
$size = [math]::Round((Get-Item "$root\dist\PLG.exe").Length / 1MB, 1)
Write-Host "OK: dist\PLG.exe ($size MB)" -ForegroundColor Green

# 4. Installer (optional - needs Inno Setup 6).
$pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$pf = $env:ProgramFiles
$isccCandidates = @()
if ($pf86) { $isccCandidates += (Join-Path $pf86 'Inno Setup 6\ISCC.exe') }
if ($pf) { $isccCandidates += (Join-Path $pf 'Inno Setup 6\ISCC.exe') }
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    Write-Host "Compiling installer with Inno Setup..." -ForegroundColor Yellow
    & $iscc "installer\PLG.iss"
    if ($LASTEXITCODE -ne 0) { Write-Host "Installer compile failed." -ForegroundColor Red; exit 1 }
    Write-Host "OK: dist\installer\Pluggin-Setup-*.exe" -ForegroundColor Green
} else {
    Write-Host "Inno Setup not found - skipping installer." -ForegroundColor DarkYellow
    Write-Host "  Install from https://jrsoftware.org/isdl.php then run ISCC.exe on installer\PLG.iss" -ForegroundColor DarkYellow
}

Write-Host "=== done ===" -ForegroundColor Cyan
