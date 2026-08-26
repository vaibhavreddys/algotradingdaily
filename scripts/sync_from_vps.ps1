<#
.SYNOPSIS
    1-Click Smart State Sync Tool from Oracle Cloud VPS to Laptop
.EXAMPLE
    .\scripts\sync_from_vps.ps1 -KeyPath "C:\path\to\oracle.key"
    .\scripts\sync_from_vps.ps1 -KeyPath "C:\path\to\oracle.key" -IncludeDuckDb
#>
param(
    [string]$VpsIp = "130.210.49.136",
    [string]$KeyPath = "C:\path\to\your\oracle_key.key",
    [switch]$IncludeDuckDb
)

$LocalRoot = Resolve-Path "$PSScriptRoot\.."
$PyBin = if (Test-Path "$LocalRoot\venv\Scripts\python.exe") { "$LocalRoot\venv\Scripts\python.exe" } else { "python" }

$CmdArgs = @("$LocalRoot\scripts\sync_from_vps.py", "--ip", "$VpsIp")
if ($KeyPath) { $CmdArgs += @("--key", "$KeyPath") }
if ($IncludeDuckDb) { $CmdArgs += "--duckdb" }

& $PyBin $CmdArgs
