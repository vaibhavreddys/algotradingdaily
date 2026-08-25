#!/bin/bash
set -e

# Resolve repository root dynamically relative to the script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}"

# Determine trading mode from argument (default: paper)
MODE="${1:-paper}"
MODE_LOWER="$(echo "${MODE}" | tr '[:upper:]' '[:lower:]')"

if [ "${MODE_LOWER}" == "live" ]; then
    TARGET_SCRIPT="live_trading/live_trader.py"
    OUTPUT_LOG="${REPO_DIR}/live_trading_output.log"
else
    TARGET_SCRIPT="live_trading/paper_trader.py"
    OUTPUT_LOG="${REPO_DIR}/paper_trading_output.log"
fi

echo "==========================================" >> "${REPO_DIR}/daily_cron.log"
echo "Starting AlgoTradingDaily [${MODE_LOWER^^}]: $(date)" >> "${REPO_DIR}/daily_cron.log"
echo "Repository path: ${REPO_DIR}" >> "${REPO_DIR}/daily_cron.log"
echo "Target log: ${OUTPUT_LOG}" >> "${REPO_DIR}/daily_cron.log"
echo "==========================================" >> "${REPO_DIR}/daily_cron.log"

# 1. Pull latest code from GitHub
git pull origin main >> "${REPO_DIR}/daily_cron.log" 2>&1 || true

# 2. Activate virtual environment
if [ -f "${REPO_DIR}/venv/bin/activate" ]; then
    source "${REPO_DIR}/venv/bin/activate"
else
    echo "⚠️ Warning: venv not found at ${REPO_DIR}/venv, using system python" >> "${REPO_DIR}/daily_cron.log"
fi

# 3. Launch the selected trading engine with unbuffered live output
export PYTHONUNBUFFERED=1
python -u "${TARGET_SCRIPT}" >> "${OUTPUT_LOG}" 2>&1

echo "Session [${MODE_LOWER^^}] finished cleanly at: $(date)" >> "${REPO_DIR}/daily_cron.log"