#!/bin/bash

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

echo "=========================================="
echo "Starting AlgoTradingDaily [${MODE_LOWER^^}]: $(date)"
echo "Repository path: ${REPO_DIR}"
echo "Target log: ${OUTPUT_LOG}"
echo "=========================================="

# 1. Pull latest code from GitHub
git pull origin main || true

# 2. Activate virtual environment
if [ -f "${REPO_DIR}/venv/bin/activate" ]; then
    source "${REPO_DIR}/venv/bin/activate"
else
    echo "⚠️ Warning: venv not found at ${REPO_DIR}/venv, using system python"
fi

# 3. Launch the selected trading engine (logs to file AND displays on terminal)
export PYTHONUNBUFFERED=1
python -u "${TARGET_SCRIPT}" 2>&1 | tee -a "${OUTPUT_LOG}"

echo "Session [${MODE_LOWER^^}] finished cleanly at: $(date)"