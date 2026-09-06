#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$Repository = 'jman9895/dellprintbridge'
)

$ErrorActionPreference = 'Stop'
$installRoot = Split-Path $PSScriptRoot -Parent
$versionFile = Join-Path $installRoot 'app-version.txt'
$programDataDir = Join-Path $env:ProgramData 'DellPrintBridge'
$logFile = Join-Path $programDataDir 'release-update.log'

New-Item -ItemType Directory -Path $programDataDir -Force | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Convert-ToVersion {
    param([string]$Text)
    $normalized = ($Text -replace '^[vV]', '').Trim()
    try { return [version]$normalized }
    catch { throw "Unable to parse version '$Text'." }
}

$transcriptStarted = $false
try {
    Start-Transcript -Path $logFile -Append | Out-Null
    $transcriptStarted = $true

    if (-not (Test-Path $versionFile)) {
        throw "Installed version file was not found: $versionFile"
    }

    $currentText = (Get-Content $versionFile -Raw).Trim()
    $currentVersion = Convert-ToVersion $currentText

    Write-Step 'Checking GitHub for the latest DellPrintBridge release'
    Write-Host "Installed version: $currentVersion"

    $headers = @{ 'User-Agent' = 'DellPrintBridge-Updater' }
    $release = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/$Repository/releases/latest" `
        -Headers $headers `
        -UseBasicParsing

    if (-not $release.tag_name) {
        throw 'GitHub did not return a release tag.'
    }

    $latestVersion = Convert-ToVersion $release.tag_name
    Write-Host "Latest version:    $latestVersion"

    if ($latestVersion -le $currentVersion) {
        Write-Host ''
        Write-Host 'DellPrintBridge is already up to date.' -ForegroundColor Green
        exit 0
    }

    $asset = @($release.assets) |
        Where-Object { $_.name -match '^DellPrintBridge-Setup-.*\.exe$' } |
        Select-Object -First 1

    if (-not $asset -or -not $asset.browser_download_url) {
        throw "Release $($release.tag_name) does not contain a DellPrintBridge setup executable."
    }

    Write-Step "Downloading DellPrintBridge $latestVersion"
    $tempDir = Join-Path $env:TEMP "DellPrintBridge-Update-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $installerPath = Join-Path $tempDir $asset.name

    Invoke-WebRequest `
        -Uri $asset.browser_download_url `
        -Headers $headers `
        -OutFile $installerPath `
        -UseBasicParsing

    if (-not (Test-Path $installerPath)) {
        throw 'The update installer download did not complete.'
    }

    Write-Step 'Installing update'
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS'
    $process = Start-Process -FilePath $installerPath -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "DellPrintBridge installer exited with code $($process.ExitCode)."
    }

    Write-Host ''
    Write-Host "DellPrintBridge updated successfully to $latestVersion." -ForegroundColor Green
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
