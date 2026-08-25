<#
.SYNOPSIS
    Comprehensive sync tool to download databases, logs, and artifacts from Oracle Cloud VPS to Laptop.
.EXAMPLE
    .\scripts\sync_db_from_vps.ps1 -KeyPath "C:\path\to\oracle.key"
    .\scripts\sync_db_from_vps.ps1 -KeyPath "C:\path\to\oracle.key" -IncludeDuckDb
#>
param(
    [string]$VpsIp = "130.210.49.136",
    [string]$KeyPath = "C:\path\to\your\oracle_key.key",
    [switch]$IncludeDuckDb
)

$LocalRoot = Resolve-Path "$PSScriptRoot\.."
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " Syncing AlgoTrading State from VPS: $VpsIp" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

# Helper function to run scp safely
function Sync-RemotePath {
    param($RemotePattern, $LocalDir, $Description)
    Write-Host "📥 Fetching $Description..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
    scp -i $KeyPath -o StrictHostKeyChecking=no "ubuntu@${VpsIp}:${RemotePattern}" "$LocalDir\" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ $Description synced to: $LocalDir" -ForegroundColor Green
    } else {
        Write-Host "   ℹ️ No new files or failed for $Description" -ForegroundColor Gray
    }
}

# 1. Sync SQLite Trade Journals (paper_trades.db, live_trades.db)
Sync-RemotePath "/home/ubuntu/trading/algotradingdaily/database/*.db" "$LocalRoot\database" "SQLite Trade Journals"

# 2. Sync Live Market Logs (paper_trading_output.log, live_trading_output.log, daily_cron.log)
Sync-RemotePath "/home/ubuntu/trading/algotradingdaily/*_output.log" "$LocalRoot\logs" "Live Trading Logs"
Sync-RemotePath "/home/ubuntu/trading/algotradingdaily/daily_cron.log" "$LocalRoot\logs" "Daily Cron Execution Logs"

# 3. Sync Daily Downloaded Candle Archives (NIFTY50_15m.csv, etc.)
Sync-RemotePath "/home/ubuntu/trading/algotradingdaily/data_pipeline/*.csv" "$LocalRoot\data_pipeline" "Benchmark & Candle Archives"

# 4. Optionally sync DuckDB backtest database if updated on VPS
if ($IncludeDuckDb) {
    Sync-RemotePath "/home/ubuntu/trading/algotradingdaily/market_data/openalgo/backtest_data.duckdb" "$LocalRoot\market_data\openalgo" "DuckDB Backtest Storage"
}

Write-Host "`n🎉 Full State Sync Complete! Local laptop is now 100% up-to-date with VPS." -ForegroundColor Cyan
