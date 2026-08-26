#!/bin/bash
# =====================================================================
# OpenAlgo systemd Background Daemon Installer (Production Safe)
# =====================================================================

set -e

# Dynamically resolve user, home, and parent workspace
CURRENT_USER="${USER:-$(whoami)}"
USER_HOME="${HOME:-$(eval echo ~$CURRENT_USER)}"
TRADING_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENALGO_DIR="$TRADING_ROOT/openalgo/openalgo"

# Dynamically resolve Python virtual environment
if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -f "$OPENALGO_DIR/venv/bin/python" ]; then
    PYTHON_BIN="$OPENALGO_DIR/venv/bin/python"
elif [ -f "$TRADING_ROOT/algotradingdaily/venv/bin/python" ]; then
    PYTHON_BIN="$TRADING_ROOT/algotradingdaily/venv/bin/python"
else
    PYTHON_BIN="$(which python3)"
fi

echo "====================================================="
echo " Installing Production-Safe OpenAlgo Service"
echo " User:       ${CURRENT_USER}"
echo " Directory:  ${OPENALGO_DIR}"
echo " Python:     ${PYTHON_BIN}"
echo "====================================================="

# 1. Patch app.py to allow Werkzeug server in daemon mode if not already patched
if grep -q "allow_unsafe_werkzeug" "${OPENALGO_DIR}/app.py"; then
    echo "✅ app.py already patched with allow_unsafe_werkzeug=True"
else
    echo "Patching app.py with allow_unsafe_werkzeug=True..."
    sed -i 's/socketio\.run(app, host=host_ip, port=port, debug=debug, reloader_options=reloader_options)/socketio.run(app, host=host_ip, port=port, debug=debug, allow_unsafe_werkzeug=True, reloader_options=reloader_options)/g' "${OPENALGO_DIR}/app.py"
    echo "✅ app.py patched successfully!"
fi

# 2. Write systemd service file
SERVICE_FILE="/etc/systemd/system/openalgo.service"

sudo bash -c "cat << 'EOF' > ${SERVICE_FILE}
[Unit]
Description=OpenAlgo Unified Broker Gateway
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${OPENALGO_DIR}
ExecStart=${PYTHON_BIN} app.py
Restart=always
RestartSec=5s
Environment=PYTHONUNBUFFERED=1
Environment=FLASK_ENV=production

[Install]
WantedBy=multi-user.target
EOF"

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload
sudo systemctl enable openalgo
sudo systemctl restart openalgo

echo ""
echo "✅ OpenAlgo service successfully installed and started!"
echo "   Status: sudo systemctl status openalgo"
