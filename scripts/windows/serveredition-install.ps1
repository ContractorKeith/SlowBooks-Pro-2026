# ============================================================================
# SlowBooks Pro - Server Edition install (Windows)
#
# Registers a scheduled task that runs the server at machine startup as
# SYSTEM (no login required), stores books machine-wide under
# C:\ProgramData\SlowBooksPro, and opens the firewall port. Run from an
# elevated PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File serveredition-install.ps1
#
# Undo everything with serveredition-uninstall.ps1.
# ============================================================================
#Requires -RunAsAdministrator
param(
    [int]$Port = 3001,
    [string]$ExePath = "",
    [string]$DataDir = "$env:ProgramData\SlowBooksPro"
)

$ErrorActionPreference = "Stop"
$TaskName = "SlowBooksProServer"
$RuleName = "SlowBooks Pro Server Edition"

# The script ships inside the bundle at _internal\scripts\windows\ -
# the exe is three levels up. Explicit -ExePath overrides.
if (-not $ExePath) {
    $ExePath = Join-Path $PSScriptRoot "..\..\..\SlowBooksPro.exe"
}
$ExePath = (Resolve-Path $ExePath).Path
if (-not (Test-Path $ExePath)) {
    throw "SlowBooksPro.exe not found at $ExePath - pass -ExePath"
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Write-Host ">> Opening firewall port $Port (rule: $RuleName)"
netsh advfirewall firewall delete rule name="$RuleName" | Out-Null
netsh advfirewall firewall add rule name="$RuleName" dir=in action=allow `
    protocol=TCP localport=$Port | Out-Null

Write-Host ">> Registering startup task $TaskName (runs as SYSTEM, no login needed)"
$TaskCmd = "`"$ExePath`" --serve-lan --port $Port --data-dir `"$DataDir`""
schtasks /Create /TN $TaskName /SC ONSTART /RU SYSTEM /RL HIGHEST /F /TR $TaskCmd | Out-Null

Write-Host ">> Starting the server now"
schtasks /Run /TN $TaskName | Out-Null
Start-Sleep -Seconds 8

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Host ""
Write-Host "SlowBooks Pro Server Edition is installed." -ForegroundColor Green
Write-Host "Books live in: $DataDir"
Write-Host "Your team connects at:"
Write-Host "    http://$($env:COMPUTERNAME):$Port"
foreach ($ip in $ips) { Write-Host "    http://${ip}:$Port" }
Write-Host ""
Write-Host "It starts automatically with Windows (before anyone logs in)."
Write-Host "Plain HTTP - trusted networks only."
