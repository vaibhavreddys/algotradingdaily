#!/bin/bash
set -e

# Navigate to project directory
cd /home/ubuntu/trading/algotradingdaily

echo "==========================================" >> daily_cron.log
echo "Starting AlgoTradingDaily: $(date)" >> daily_cron.log
echo "==========================================" >> daily_cron.log

# 1. Pull latest code from GitHub
git pull origin main >> daily_cron.log 2>&1

# 2. Activate virtual environment
source venv/bin/activate

# 3. Launch the trading engine (Paper or Live)
python live_trading/paper_trader.py >> trading_output.log 2>&1

echo "Session finished cleanly at: $(date)" >> daily_cron.log