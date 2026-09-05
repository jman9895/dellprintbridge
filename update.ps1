#Requires -RunAsAdministrator
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

    Start-Sleep -Seconds 1

    Get-Process python, pythonw -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Path -and $_.Path.StartsWith($venv, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

function Start-BridgeTasks {
    foreach ($taskName in @($backendTask, $trayTask)) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Start-ScheduledTask -TaskName $taskName
        }
    }
}

function Test-BridgeHealth {
    param([int]$Attempts = 10)

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
$oldCommit = $null
$updatedCommit = $null
$didPull = $false

try {
    Start-Transcript -Path $updateLog -Append | Out-Null
    $transcriptStarted = $true

    Set-Location $repoRoot

    Write-Step 'Checking update prerequisites'

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git is required for in-place updates but was not found.'
    }

    if (-not (Test-Path (Join-Path $repoRoot '.git'))) {
        throw "DellPrintBridge does not appear to be a Git checkout: $repoRoot"
    }

    $dirtyTracked = (& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to inspect Git working tree state.'
    }
    if ($dirtyTracked) {
        throw 'Tracked files have local modifications. Commit, stash, or discard them before running the updater.'
    }

    $currentBranch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $currentBranch) {
        throw 'Unable to determine the current Git branch. Detached HEAD updates are not supported.'
    }

    $oldCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $oldCommit) {
        throw 'Unable to determine the current DellPrintBridge commit.'
    }

    $upstream = (& git rev-parse --abbrev-ref "$currentBranch@{upstream}" 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $upstream) {
        $upstream = "origin/$currentBranch"
    }

    Write-Host "Repository: $repoRoot"
    Write-Host "Branch:     $currentBranch"
    Write-Host "Current:    $oldCommit"
    Write-Host "Upstream:   $upstream"

    Write-Step 'Fetching updates from GitHub'
    Invoke-Git @('fetch', '--prune', 'origin')

    $remoteCommit = (& git rev-parse $upstream).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $remoteCommit) {
        throw "Unable to resolve upstream commit: $upstream"
    }

    if ($remoteCommit -eq $oldCommit) {
        Write-Host 'Code is already up to date. Dependencies and scheduled tasks will still be verified.' -ForegroundColor Green
    } else {
        Write-Host "Available:  $remoteCommit" -ForegroundColor Yellow
    }

    Write-Step 'Stopping DellPrintBridge'
    Stop-BridgeProcesses

    if ($remoteCommit -ne $oldCommit) {
        Write-Step 'Updating application files'
        Invoke-Git @('pull', '--ff-only')
        $didPull = $true
    }

    $updatedCommit = (& git rev-parse HEAD).Trim()
    Write-Host "Installed:  $updatedCommit" -ForegroundColor Green

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
    Write-Host "Previous commit: $oldCommit"
    Write-Host "Current commit:  $updatedCommit"
    Write-Host "Update log:      $updateLog"
}
catch {
    $failure = $_
    Write-Host ''
    Write-Host "Update failed: $($failure.Exception.Message)" -ForegroundColor Red

    if ($didPull -and $oldCommit) {
        Write-Host 'Attempting automatic rollback to the previous commit...' -ForegroundColor Yellow
        try {
            Stop-BridgeProcesses
            Invoke-Git @('reset', '--hard', $oldCommit)

            & (Join-Path $repoRoot 'setup-dev.ps1')
            if ($LASTEXITCODE -ne 0) {
                throw "Rollback setup-dev.ps1 failed with exit code $LASTEXITCODE"
            }

            Start-BridgeTasks

            if (Test-BridgeHealth) {
                Write-Host "Rollback succeeded. DellPrintBridge is running at $oldCommit." -ForegroundColor Green
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
