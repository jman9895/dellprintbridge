#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallRoot
)

$ErrorActionPreference = 'SilentlyContinue'
$backendTask = 'DellPrintBridge'
$trayTask = 'DellPrintBridge Tray'

foreach ($taskName in @($trayTask, $backendTask)) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}

Get-Process DellPrintBridge, DellPrintBridgeTray -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Path -and $_.Path.StartsWith($InstallRoot, [System.StringComparison]::OrdinalIgnoreCase)
    } |
    Stop-Process -Force -ErrorAction SilentlyContinue

foreach ($ruleName in @(
    'DellPrintBridge - IPP',
    'DellPrintBridge - mDNS',
    'DellPrintBridge - Web UI'
)) {
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
}

# Runtime configuration and logs under %ProgramData%\DellPrintBridge are intentionally
# preserved so uninstall/reinstall does not discard the selected printer or diagnostics.
