#!/bin/bash
# =====================================================================
# AlgoTradingDaily Visualizer Dashboard systemd Daemon Installer
# =====================================================================

set -e

CURRENT_USER="${USER:-$(whoami)}"
USER_HOME="${HOME:-$(eval echo ~$CURRENT_USER)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Dynamically resolve Python virtual environment
if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -f "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
else
    PYTHON_BIN="$(which python3)"
fi

SERVICE_NAME="visualizer.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

echo "====================================================="
echo " Installing Visualizer Dashboard Systemd Daemon"
echo " User:        ${CURRENT_USER}"
echo " Directory:   ${PROJECT_DIR}"
echo " Python:      ${PYTHON_BIN}"
echo " Service:     ${SERVICE_PATH}"
echo " Port:        8501"
echo "====================================================="

sudo tee "${SERVICE_PATH}" > /dev/null << EOF
[Unit]
Description=AlgoTradingDaily Visualizer & Backtest Dashboard
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PYTHON_BIN} visualizer/server.py --port 8501 --no-browser
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling ${SERVICE_NAME} on boot..."
sudo systemctl enable "${SERVICE_NAME}"

echo "Starting ${SERVICE_NAME}..."
sudo systemctl restart "${SERVICE_NAME}"

echo "====================================================="
echo " Visualizer Service successfully installed and running 24/7!"
echo " Status check: sudo systemctl status ${SERVICE_NAME}"
echo " Access URL:   http://localhost:8501 (via SSH tunnel) or http://<VPS_IP>:8501"
echo "====================================================="
