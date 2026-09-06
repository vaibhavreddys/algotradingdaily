#!/bin/bash
# =====================================================================
# OpenAlgo systemd Service Installer (post-OAuth edition, 2026-09)
# =====================================================================
# Installs/refreshes the `openalgo.service` unit matching the proven
# production layout on the reference VM:
#   - repo checkout at $HOME/openalgo
#   - venv at $HOME/openalgo/.venv (falls back: $HOME/openalgo/venv)
#   - app.py patched with allow_unsafe_werkzeug=True (flask-socketio
#     refuses the Werkzeug server in production mode otherwise)
#
# Run from anywhere:  bash scripts/install_openalgo_service.sh
# Idempotent: re-running overwrites the unit file and restarts the service.

set -e

CURRENT_USER="${USER:-$(whoami)}"
USER_HOME="${HOME:-$(eval echo ~$CURRENT_USER)}"

# Resolve the OpenAlgo checkout: $HOME/openalgo (reference layout) or siblings.
for candidate in "$USER_HOME/openalgo" "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/openalgo"; do
    if [ -f "$candidate/app.py" ]; then
        OPENALGO_DIR="$candidate"
        break
    fi
done
if [ -z "${OPENALGO_DIR:-}" ]; then
    echo "FATAL: could not locate an OpenAlgo checkout containing app.py" >&2
    exit 1
fi

# Resolve the Python interpreter: openalgo venv first, then sibling venvs.
for candidate in "$OPENALGO_DIR/.venv/bin/python" "$OPENALGO_DIR/venv/bin/python"; do
    if [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done
PYTHON_BIN="${PYTHON_BIN:-$(which python3)}"

echo "====================================================="
echo " Installing OpenAlgo systemd service"
echo " User:       ${CURRENT_USER}"
echo " Directory:  ${OPENALGO_DIR}"
echo " Python:     ${PYTHON_BIN}"
echo "====================================================="

# 1. Patch app.py so flask-socketio accepts the Werkzeug server headless.
if grep -q "allow_unsafe_werkzeug" "${OPENALGO_DIR}/app.py"; then
    echo "✅ app.py already patched with allow_unsafe_werkzeug=True"
else
    echo "Patching app.py with allow_unsafe_werkzeug=True..."
    sed -i 's/socketio\.run(app, host=host_ip, port=port, debug=debug, reloader_options=reloader_options)/socketio.run(app, host=host_ip, port=port, debug=debug, allow_unsafe_werkzeug=True, reloader_options=reloader_options)/g' "${OPENALGO_DIR}/app.py"
    grep -q "allow_unsafe_werkzeug" "${OPENALGO_DIR}/app.py" \
        && echo "✅ app.py patched successfully!" \
        || { echo "FATAL: patch did not apply — socketio.run call not found" >&2; exit 1; }
fi

# 2. Write the systemd unit. NOTE: a plain `systemctl reload openalgo` is
#    NOT applicable (Type=simple, no ExecReload); the auto-login script
#    therefore *restarts* the unit to refresh its in-process auth cache.
sudo bash -c "cat << 'EOF' > /etc/systemd/system/openalgo.service
[Unit]
Description=OpenAlgo Unified Broker Gateway
After=network-online.target
Wants=network-online.target

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
echo "✅ OpenAlgo service installed and started!"
echo "   Status:  systemctl status openalgo"
echo "   Health:  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/"
