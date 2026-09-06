#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
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

$didPull = $false
if ($remoteCommit -eq $oldCommit) {
    Write-Host 'Code is already up to date. Dependencies and scheduled tasks will still be verified.' -ForegroundColor Green
} else {
    Write-Host "Available:  $remoteCommit" -ForegroundColor Yellow
    Write-Step 'Updating application files'
    Invoke-Git @('pull', '--ff-only')
    $didPull = $true
}

$installedCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $installedCommit) {
    throw 'Unable to determine the installed commit after update.'
}
Write-Host "Installed:  $installedCommit" -ForegroundColor Green

# Important: run the post-pull phase from a separate script. If update.ps1 itself was
# changed by the pull, continuing to execute the old in-memory copy can produce a
# one-run-behind/self-update race. update-worker.ps1 is loaded from disk only after
# the pull has finished, so every update uses the newly installed worker code.
$worker = Join-Path $repoRoot 'update-worker.ps1'
if (-not (Test-Path $worker)) {
    throw "Update worker was not found after the Git update: $worker"
}

Write-Step 'Starting post-update worker'
$workerArgs = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $worker,
    '-PreviousCommit', $oldCommit,
    '-InstalledCommit', $installedCommit
)
if ($didPull) {
    $workerArgs += '-DidPull'
}

# This process is already elevated. A normal child PowerShell inherits the elevated
# token, avoiding a second UAC prompt while ensuring the worker is a fresh process.
$process = Start-Process -FilePath 'powershell.exe' -ArgumentList $workerArgs -WorkingDirectory $repoRoot -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "DellPrintBridge update worker failed with exit code $($process.ExitCode). See $updateLog"
}
