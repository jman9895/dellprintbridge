#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$venv = Join-Path $PSScriptRoot '.venv'
if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3 is required. Install Python 3.10+ and run this script again.'
}

$python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
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

Write-Host ''
Write-Host 'Development setup complete.' -ForegroundColor Green
Write-Host "Run: $venv\Scripts\python.exe $PSScriptRoot\dellprintbridge.py"
Write-Host 'Then open: http://localhost:8631'
