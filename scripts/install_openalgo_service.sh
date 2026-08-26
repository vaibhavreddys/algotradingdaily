#!/bin/bash
# =====================================================================
# OpenAlgo 24/7 Background Systemd Service Installer (Fully Portable)
# =====================================================================
set -e

# Resolve dynamic paths from user environment
CURRENT_USER="${USER:-$(whoami)}"
USER_HOME="${HOME:-$(eval echo ~$CURRENT_USER)}"
SERVICE_FILE="/etc/systemd/system/openalgo.service"

# Dynamic directory resolution
TRADING_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENALGO_DIR="$TRADING_ROOT/openalgo/openalgo"

if [ ! -d "$OPENALGO_DIR" ]; then
    OPENALGO_DIR="$USER_HOME/trading/openalgo/openalgo"
fi

if [ ! -d "$OPENALGO_DIR" ]; then
    echo "⚠️ Error: OpenAlgo directory not found relative to repository or in $USER_HOME/trading/openalgo/openalgo"
    exit 1
fi

# Dynamic Python binary resolver
if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -f "$OPENALGO_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$OPENALGO_DIR/venv/bin/python"
elif [ -f "$TRADING_ROOT/openalgo/venv/bin/python" ]; then
    PYTHON_BIN="$TRADING_ROOT/openalgo/venv/bin/python"
elif [ -f "$TRADING_ROOT/algotradingdaily/venv/bin/python" ]; then
    PYTHON_BIN="$TRADING_ROOT/algotradingdaily/venv/bin/python"
else
    PYTHON_BIN="$(which python3)"
fi

echo "====================================================="
echo " Installing Portable OpenAlgo systemd Service"
echo " User:       $CURRENT_USER"
echo " Directory:  $OPENALGO_DIR"
echo " Python:     $PYTHON_BIN"
echo "====================================================="

sudo bash -c "cat << EOF > $SERVICE_FILE
[Unit]
Description=OpenAlgo Unified Broker Gateway
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$OPENALGO_DIR
ExecStart=$PYTHON_BIN app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload
sudo systemctl enable openalgo.service
sudo systemctl restart openalgo.service

echo ""
echo "✅ OpenAlgo service successfully installed and started!"
echo "   Status: sudo systemctl status openalgo"
