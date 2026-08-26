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

# 2b. Start the Telegram bot worker in the background (long-polling).
#     It serves /start, /stop, and (when TELEGRAM_OWNER_CHAT_ID is set) admin
#     commands. The bot shares its SQLite subscribers table with the trading
#     engine so trade alerts broadcast to every active subscriber.
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_INVITE_CODE:-}" ]; then
    BOT_LOG="${REPO_DIR}/telegram_bot_output.log"
    echo "Starting Telegram bot worker (logs: ${BOT_LOG})..."
    nohup python -u -m alerts.tg_bot >> "${BOT_LOG}" 2>&1 &
    BOT_PID=$!
    echo "Telegram bot worker started (PID ${BOT_PID})."
else
    echo "ℹ️ Telegram bot worker not started (TELEGRAM_BOT_TOKEN or TELEGRAM_INVITE_CODE missing)."
    BOT_PID=""
fi

# 3. Launch the selected trading engine (logs to file AND displays on terminal)
export PYTHONUNBUFFERED=1
python -u "${TARGET_SCRIPT}" 2>&1 | tee -a "${OUTPUT_LOG}"
ENGINE_EXIT=$?

# 4. Tear down the Telegram bot worker (if it was started) before exit.
if [ -n "${BOT_PID:-}" ] && kill -0 "${BOT_PID}" 2>/dev/null; then
    echo "Stopping Telegram bot worker (PID ${BOT_PID})..."
    kill "${BOT_PID}" 2>/dev/null || true
    wait "${BOT_PID}" 2>/dev/null || true
fi

echo "Session [${MODE_LOWER^^}] finished cleanly at: $(date) (engine exit=${ENGINE_EXIT})"
exit "${ENGINE_EXIT}"