#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallRoot
)

$ErrorActionPreference = 'Stop'
$backendTask = 'DellPrintBridge'
$trayTask = 'DellPrintBridge Tray'
$backendExe = Join-Path $InstallRoot 'backend\DellPrintBridge.exe'
$trayExe = Join-Path $InstallRoot 'tray\DellPrintBridgeTray.exe'
$programDataDir = Join-Path $env:ProgramData 'DellPrintBridge'

if (-not (Test-Path $backendExe)) {
    throw "Backend executable was not found: $backendExe"
}
if (-not (Test-Path $trayExe)) {
    throw "Tray executable was not found: $trayExe"
}

New-Item -ItemType Directory -Path $programDataDir -Force | Out-Null

$rules = @(
    @{ Name='DellPrintBridge - IPP'; Protocol='TCP'; Port=631 },
    @{ Name='DellPrintBridge - mDNS'; Protocol='UDP'; Port=5353 },
    @{ Name='DellPrintBridge - Web UI'; Protocol='TCP'; Port=8631 }
)

foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    }

    New-NetFirewallRule `
        -DisplayName $rule.Name `
        -Direction Inbound `
        -Protocol $rule.Protocol `
        -LocalPort $rule.Port `
        -Action Allow `
        -Profile Private | Out-Null
}

Stop-ScheduledTask -TaskName $backendTask -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName $trayTask -ErrorAction SilentlyContinue

$backendAction = New-ScheduledTaskAction `
    -Execute $backendExe `
    -WorkingDirectory (Split-Path $backendExe -Parent)
$backendTrigger = New-ScheduledTaskTrigger -AtStartup
$backendPrincipal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest
$backendSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $backendTask `
    -Action $backendAction `
    -Trigger $backendTrigger `
    -Principal $backendPrincipal `
    -Settings $backendSettings `
    -Description 'Starts the DellPrintBridge backend at Windows startup.' `
    -Force | Out-Null

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trayAction = New-ScheduledTaskAction `
    -Execute $trayExe `
    -WorkingDirectory (Split-Path $trayExe -Parent)
$trayTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$trayPrincipal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$traySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $trayTask `
    -Action $trayAction `
    -Trigger $trayTrigger `
    -Principal $trayPrincipal `
    -Settings $traySettings `
    -Description 'Shows DellPrintBridge status in the signed-in user system tray.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $backendTask
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $trayTask

Write-Host 'DellPrintBridge runtime registration completed.' -ForegroundColor Green
