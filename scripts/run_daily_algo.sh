#!/bin/bash
set -e

# Resolve repository root dynamically relative to the script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}"

echo "==========================================" >> "${REPO_DIR}/daily_cron.log"
echo "Starting AlgoTradingDaily: $(date)" >> "${REPO_DIR}/daily_cron.log"
echo "Repository path: ${REPO_DIR}" >> "${REPO_DIR}/daily_cron.log"
echo "==========================================" >> "${REPO_DIR}/daily_cron.log"

# 1. Pull latest code from GitHub
git pull origin main >> "${REPO_DIR}/daily_cron.log" 2>&1

# 2. Activate virtual environment
if [ -f "${REPO_DIR}/venv/bin/activate" ]; then
    source "${REPO_DIR}/venv/bin/activate"
else
    echo "⚠️ Warning: venv not found at ${REPO_DIR}/venv, using system python" >> "${REPO_DIR}/daily_cron.log"
fi

# 3. Launch the trading engine (Paper or Live)
python live_trading/paper_trader.py >> "${REPO_DIR}/trading_output.log" 2>&1

echo "Session finished cleanly at: $(date)" >> "${REPO_DIR}/daily_cron.log"