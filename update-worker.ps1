#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory=$true)][string]$PreviousCommit,
    [Parameter(Mandatory=$true)][string]$InstalledCommit,
    [switch]$DidPull
)

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$backendTask = 'DellPrintBridge'
$trayTask = 'DellPrintBridge Tray'
$venv = Join-Path $repoRoot '.venv'
$programDataDir = Join-Path $env:ProgramData 'DellPrintBridge'
$updateLog = Join-Path $programDataDir 'update.log'

New-Item -ItemType Directory -Path $programDataDir -Force | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Stop-BridgeProcesses {
    foreach ($taskName in @($backendTask, $trayTask)) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }

    # Scheduled Task stop is asynchronous. Give the Python hosts a chance to exit,
    # then forcibly clear only processes that belong to this project's venv.
    for ($i = 0; $i -lt 10; $i++) {
        $remaining = Get-Process python, pythonw -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Path -and $_.Path.StartsWith($venv, [System.StringComparison]::OrdinalIgnoreCase)
            }
        if (-not $remaining) { break }
        Start-Sleep -Milliseconds 500
    }

    Get-Process python, pythonw -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Path -and $_.Path.StartsWith($venv, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    Start-Sleep -Milliseconds 500
}

function Start-BridgeTasks {
    foreach ($taskName in @($backendTask, $trayTask)) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Start-ScheduledTask -TaskName $taskName
        }
    }
}

function Test-BridgeHealth {
    param([int]$Attempts = 15)

    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest -Uri 'http://localhost:8631/' -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {}
        Start-Sleep -Seconds 1
    }

    return $false
}

$transcriptStarted = $false

try {
    Start-Transcript -Path $updateLog -Append | Out-Null
    $transcriptStarted = $true
    Set-Location $repoRoot

    Write-Step 'Stopping DellPrintBridge'
    Stop-BridgeProcesses

    Write-Step 'Updating dependencies and scheduled tasks'
    & (Join-Path $repoRoot 'setup-dev.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw "setup-dev.ps1 failed with exit code $LASTEXITCODE"
    }

    Write-Step 'Starting DellPrintBridge'
    Start-BridgeTasks

    Write-Step 'Performing health check'
    if (-not (Test-BridgeHealth)) {
        throw 'DellPrintBridge did not respond on http://localhost:8631/ after the update.'
    }

    Write-Host ''
    Write-Host 'DellPrintBridge update completed successfully.' -ForegroundColor Green
    Write-Host "Previous commit: $PreviousCommit"
    Write-Host "Current commit:  $InstalledCommit"
    Write-Host "Update log:      $updateLog"
}
catch {
    $failure = $_
    Write-Host ''
    Write-Host "Update failed: $($failure.Exception.Message)" -ForegroundColor Red

    if ($DidPull -and $PreviousCommit) {
        Write-Host 'Attempting automatic rollback to the previous commit...' -ForegroundColor Yellow
        try {
            Stop-BridgeProcesses
            Invoke-Git @('reset', '--hard', $PreviousCommit)

            & (Join-Path $repoRoot 'setup-dev.ps1')
            if ($LASTEXITCODE -ne 0) {
                throw "Rollback setup-dev.ps1 failed with exit code $LASTEXITCODE"
            }

            Start-BridgeTasks

            if (Test-BridgeHealth) {
                Write-Host "Rollback succeeded. DellPrintBridge is running at $PreviousCommit." -ForegroundColor Green
            } else {
                Write-Host 'Rollback restored the old files, but the web health check still failed.' -ForegroundColor Red
            }
        }
        catch {
            Write-Host "Automatic rollback also failed: $($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        Start-BridgeTasks
    }

    throw $failure
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
