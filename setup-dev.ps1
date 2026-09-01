#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

function Find-PythonExecutable {
    foreach ($command in @('py', 'python')) {
        $resolved = Get-Command $command -ErrorAction SilentlyContinue
        if ($resolved) {
            if ($command -eq 'py') {
                try {
                    $candidate = & $resolved.Source -3 -c "import sys; print(sys.executable)" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path $candidate.Trim())) {
                        return $candidate.Trim()
                    }
                } catch {}
            } elseif (Test-Path $resolved.Source) {
                return $resolved.Source
            }
        }
    }

    $registryRoots = @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )

    foreach ($root in $registryRoots) {
        if (-not (Test-Path $root)) { continue }

        $versions = Get-ChildItem $root -ErrorAction SilentlyContinue |
            Sort-Object PSChildName -Descending

        foreach ($version in $versions) {
            $installPathKey = Join-Path $version.PSPath 'InstallPath'
            if (-not (Test-Path $installPathKey)) { continue }

            $props = Get-ItemProperty $installPathKey -ErrorAction SilentlyContinue
            $candidates = @()

            if ($props.ExecutablePath) { $candidates += $props.ExecutablePath }
            if ($props.'(default)') { $candidates += (Join-Path $props.'(default)' 'python.exe') }

            try {
                $defaultPath = (Get-Item $installPathKey).GetValue('')
                if ($defaultPath) { $candidates += (Join-Path $defaultPath 'python.exe') }
            } catch {}

            foreach ($candidate in $candidates) {
                if ($candidate -and (Test-Path $candidate)) { return $candidate }
            }
        }
    }

    $commonRoots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:ProgramFiles 'Python*')
    )

    if (${env:ProgramFiles(x86)}) {
        $commonRoots += (Join-Path ${env:ProgramFiles(x86)} 'Python*')
    }

    foreach ($pattern in $commonRoots) {
        $dirs = Get-ChildItem $pattern -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending

        if ((Test-Path $pattern -PathType Container)) {
            $dirs = Get-ChildItem $pattern -Directory -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending
        }

        foreach ($dir in $dirs) {
            $candidate = Join-Path $dir.FullName 'python.exe'
            if (Test-Path $candidate) { return $candidate }
        }
    }

    return $null
}

$venv = Join-Path $PSScriptRoot '.venv'
$python = Find-PythonExecutable

if (-not $python) {
    throw 'Python 3.10+ is required. Python was not found in PATH, the Python registry keys, or standard install locations.'
}

Write-Host "Using Python: $python" -ForegroundColor Cyan
$versionText = & $python --version 2>&1
Write-Host $versionText

& $python -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')

$rules = @(
    @{ Name='DellPrintBridge - IPP'; Protocol='TCP'; Port=631 },
    @{ Name='DellPrintBridge - mDNS'; Protocol='UDP'; Port=5353 },
    @{ Name='DellPrintBridge - Web UI'; Protocol='TCP'; Port=8631 }
)

foreach ($rule in $rules) {
    if (-not (Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Protocol $rule.Protocol -LocalPort $rule.Port -Action Allow -Profile Private | Out-Null
    }
}

$taskName = 'DellPrintBridge'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$appScript = Join-Path $PSScriptRoot 'dellprintbridge.py'
$workingDirectory = $PSScriptRoot

$action = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument ('"{0}"' -f $appScript) `
    -WorkingDirectory $workingDirectory

$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Starts DellPrintBridge at Windows startup.' `
    -Force | Out-Null

Write-Host ''
Write-Host 'Development setup complete.' -ForegroundColor Green
Write-Host "Startup task installed: $taskName" -ForegroundColor Green
Write-Host "Log file: $env:ProgramData\DellPrintBridge\dellprintbridge.log"
Write-Host "Manual run: $venvPython $appScript"
Write-Host 'Web UI: http://localhost:8631'
Write-Host ''
Write-Host "To start it now in the background: Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Cyan
