#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Version = '0.1.0',
    [switch]$SkipPythonInstall
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$venv = Join-Path $repoRoot '.venv-build'
$python = Join-Path $venv 'Scripts\python.exe'
$buildRoot = Join-Path $repoRoot 'build'
$distRoot = Join-Path $buildRoot 'dist'
$outputRoot = Join-Path $buildRoot 'installer'
$versionFile = Join-Path $buildRoot 'app-version.txt'

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Find-Python {
    foreach ($candidate in @('py', 'python')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }

        if ($candidate -eq 'py') {
            try {
                $path = & $cmd.Source -3 -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path $path.Trim())) {
                    return $path.Trim()
                }
            } catch {}
        } elseif (Test-Path $cmd.Source) {
            return $cmd.Source
        }
    }

    return $null
}

function Find-InnoSetupCompiler {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    foreach ($path in @(
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )) {
        if ($path -and (Test-Path $path)) { return $path }
    }

    return $null
}

$systemPython = Find-Python
if (-not $systemPython) {
    throw 'Python 3 was not found. Install Python 3.10+ before building the installer.'
}

if (Test-Path $buildRoot) {
    Remove-Item $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
Set-Content -Path $versionFile -Value $Version -Encoding ASCII

Write-Step "Preparing clean build environment for version $Version"
if (Test-Path $venv) {
    Remove-Item $venv -Recurse -Force
}
& $systemPython -m venv $venv

if (-not (Test-Path $python)) {
    throw "Build virtual environment was not created correctly: $python"
}

if (-not $SkipPythonInstall) {
    Write-Step 'Installing runtime and build dependencies'
    & $python -m pip install --upgrade pip
    & $python -m pip install -r (Join-Path $repoRoot 'requirements.txt')
    & $python -m pip install -r (Join-Path $repoRoot 'requirements-build.txt')
}

Write-Step 'Building backend executable'
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name DellPrintBridge `
    --hidden-import win32timezone `
    --collect-all zeroconf `
    --distpath (Join-Path $distRoot 'backend') `
    --workpath (Join-Path $buildRoot 'pyinstaller\backend') `
    --specpath (Join-Path $buildRoot 'spec') `
    (Join-Path $repoRoot 'dellprintbridge.py')

if ($LASTEXITCODE -ne 0) {
    throw "Backend PyInstaller build failed with exit code $LASTEXITCODE"
}

Write-Step 'Building tray executable'
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name DellPrintBridgeTray `
    --collect-all pystray `
    --distpath (Join-Path $distRoot 'tray') `
    --workpath (Join-Path $buildRoot 'pyinstaller\tray') `
    --specpath (Join-Path $buildRoot 'spec') `
    (Join-Path $repoRoot 'dellprintbridge_tray.py')

if ($LASTEXITCODE -ne 0) {
    throw "Tray PyInstaller build failed with exit code $LASTEXITCODE"
}

$backendExe = Join-Path $distRoot 'backend\DellPrintBridge\DellPrintBridge.exe'
$trayExe = Join-Path $distRoot 'tray\DellPrintBridgeTray\DellPrintBridgeTray.exe'
if (-not (Test-Path $backendExe)) { throw "Backend executable not found after build: $backendExe" }
if (-not (Test-Path $trayExe)) { throw "Tray executable not found after build: $trayExe" }

$inno = Find-InnoSetupCompiler
if (-not $inno) {
    throw 'Inno Setup 6 was not found. Install it with: winget install --id JRSoftware.InnoSetup -e'
}

Write-Step 'Compiling one-click installer'
$iss = Join-Path $repoRoot 'installer\DellPrintBridge.iss'
& $inno "/DMyAppVersion=$Version" "/O$outputRoot" $iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
}

$installer = Get-ChildItem $outputRoot -Filter 'DellPrintBridge-Setup-*.exe' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $installer) {
    throw 'Installer compilation completed but the setup executable was not found.'
}

Write-Host ''
Write-Host 'DellPrintBridge installer build completed successfully.' -ForegroundColor Green
Write-Host "Installer: $($installer.FullName)"
Write-Host "Version:   $Version"
