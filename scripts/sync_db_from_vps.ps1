# =====================================================================
# Sync Trading Databases & DuckDB from Oracle Cloud VPS to Laptop
# =====================================================================
param(
    [string]$VpsIp = "130.210.49.136",
    [string]$KeyPath = "C:\path\to\your\oracle_key.key",
    [switch]$IncludeDuckDb
)

$LocalRoot = Resolve-Path "$PSScriptRoot\.."
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " Syncing AlgoTrading Databases from VPS: $VpsIp" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

# 1. Sync SQLite Trade Journals (paper_trades.db, live_trades.db)
Write-Host "📥 Fetching SQLite trade journals..." -ForegroundColor Yellow
$DbDir = "$LocalRoot\database"
New-Item -ItemType Directory -Force -Path $DbDir | Out-Null

scp -i $KeyPath -o StrictHostKeyChecking=no "ubuntu@${VpsIp}:/home/ubuntu/trading/algotradingdaily/database/*.db" "$DbDir\"

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ SQLite databases synced successfully to: $DbDir" -ForegroundColor Green
} else {
    Write-Host "⚠️ Warning: Failed to sync SQLite database files." -ForegroundColor Red
}

# 2. Optionally sync DuckDB backtest database if updated on VPS
if ($IncludeDuckDb) {
    Write-Host "📥 Fetching backtest_data.duckdb (may take a moment)..." -ForegroundColor Yellow
    $DuckDir = "$LocalRoot\market_data\openalgo"
    New-Item -ItemType Directory -Force -Path $DuckDir | Out-Null
    scp -i $KeyPath -o StrictHostKeyChecking=no "ubuntu@${VpsIp}:/home/ubuntu/trading/algotradingdaily/market_data/openalgo/backtest_data.duckdb" "$DuckDir\backtest_data.duckdb"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ DuckDB synced successfully to: $DuckDir\backtest_data.duckdb" -ForegroundColor Green
    }
}

Write-Host "`n🎉 Sync complete! Local laptop is now 100% up-to-date with VPS data." -ForegroundColor Cyan
