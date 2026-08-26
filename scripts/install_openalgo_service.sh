#!/bin/bash
# =====================================================================
# OpenAlgo 24/7 Background Systemd Service Installer
# =====================================================================
set -e

SERVICE_FILE="/etc/systemd/system/openalgo.service"
OPENALGO_DIR="/home/ubuntu/trading/openalgo/openalgo"
PYTHON_BIN="/home/ubuntu/trading/openalgo/venv/bin/python"

if [ ! -d "$OPENALGO_DIR" ]; then
    echo "⚠️ Error: OpenAlgo directory not found at $OPENALGO_DIR"
    exit 1
fi

echo "====================================================="
echo " Installing OpenAlgo 24/7 systemd Service"
echo "====================================================="

sudo bash -c "cat << EOF > $SERVICE_FILE
[Unit]
Description=OpenAlgo Unified Broker Gateway
After=network.target

[Service]
Type=simple
User=ubuntu
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
echo "   Logs:   journalctl -u openalgo -f"
