#!/bin/bash
# =====================================================================
# AlgoTradingDaily - Automated Daily Headless Runner (Linux VPS)
# =====================================================================
# Usage:
#   ./scripts/run_daily_algo.sh paper    # Runs Paper Trading Engine
#   ./scripts/run_daily_algo.sh live     # Runs Live Real-Money Engine
# =====================================================================

set -e

MODE="${1:-paper}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TODAY_DATE=$(date +"%Y-%m-%d")
RETENTION_DAYS=30

# Create hierarchical structured log directory
LOGS_DIR="${REPO_DIR}/logs/${MODE}"
mkdir -p "${LOGS_DIR}"
mkdir -p "${REPO_DIR}/logs"

if [ "${MODE}" = "live" ]; then
    TARGET_SCRIPT="live_trading/live_trader.py"
    DAILY_LOG="${LOGS_DIR}/live_${TODAY_DATE}.log"
    LATEST_LOG="${REPO_DIR}/live_trading_output.log"
else
    TARGET_SCRIPT="live_trading/paper_trader.py"
    DAILY_LOG="${LOGS_DIR}/paper_${TODAY_DATE}.log"
    LATEST_LOG="${REPO_DIR}/paper_trading_output.log"
fi

# 1. Automatic Housekeeping: Clean up logs older than 30 days
find "${LOGS_DIR}" -type f -name "*.log" -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true

echo "" >> "${DAILY_LOG}"
echo "==========================================" >> "${DAILY_LOG}"
echo "AlgoTradingDaily [${MODE^^}] Startup: $(date)" >> "${DAILY_LOG}"
echo "==========================================" >> "${DAILY_LOG}"

echo "=========================================="
echo "Starting AlgoTradingDaily [${MODE^^}]: $(date)"
echo "Repository path: ${REPO_DIR}"
echo "Daily Log:       ${DAILY_LOG}"
echo "Log Retention:   ${RETENTION_DAYS} days"
echo "=========================================="

cd "${REPO_DIR}"

# 2. Pull latest strategy updates and bug fixes from main
echo "Checking for repository updates..."
git pull origin main || echo "⚠️ Warning: git pull failed, continuing with local version"

# 3. Activate Virtual Environment
if [ -d "${REPO_DIR}/venv" ]; then
    source "${REPO_DIR}/venv/bin/activate"
    PYTHON_EXEC="${REPO_DIR}/venv/bin/python"
else
    echo "⚠️ Warning: venv not found at ${REPO_DIR}/venv, using system python"
    PYTHON_EXEC="python"
fi

# 4. Automatically restart Telegram Bot to reload fresh strategy and alert logic
echo "Checking and restarting Telegram Bot service..."
if systemctl is-active --quiet telegram_bot 2>/dev/null; then
    echo "🤖 Restarting systemd telegram_bot service..."
    sudo systemctl restart telegram_bot || true
else
    echo "🤖 Restarting background telegram_bot process..."
    pkill -f "alerts/tg_bot.py" || true
    sleep 1
    nohup "${PYTHON_EXEC}" "${REPO_DIR}/alerts/tg_bot.py" >> "${REPO_DIR}/logs/tg_bot.log" 2>&1 &
fi

# 5. Stream unbuffered output to both dated log and latest pointer
export PYTHONUNBUFFERED=1
python -u "${TARGET_SCRIPT}" 2>&1 | tee -a "${DAILY_LOG}" "${LATEST_LOG}"
ENGINE_EXIT=$?

# Record session end to the dated log and print a summary to stdout
echo "Session [${MODE^^}] finished at: $(date)" >> "${DAILY_LOG}"
echo "Session [${MODE^^}] finished at: $(date) (engine exit=${ENGINE_EXIT})"
exit "${ENGINE_EXIT}"
