#!/bin/bash
echo "user name = ${USER}"
curPath=$(readlink -f "$(dirname "$0")")
echo $curPath
sudo rm -rf /home/${USER}/.config/autostart/battery.desktop
if [ ! -d "/home/${USER}/.config/autostart" ];then
    sudo mkdir /home/${USER}/.config/autostart
fi
sed -i "s#.*Exec.*#Exec=${curPath}/battery.sh#" `grep Exec -rl battery.desktop`
sed -i "s#.*Icon.*#Icon=${curPath}/images/battery.1.png#" `grep Icon -rl battery.desktop`
sudo cp battery.desktop /home/${USER}/.config/autostart/
sudo rm -rf  battery.sh
sudo touch  battery.sh
sudo chmod 777 battery.sh
echo "sleep 5" >> battery.sh
echo "cd ${curPath}" >> battery.sh
echo "DISPLAY=':0.0' python3 batteryTray.py " >> battery.sh

# ---------------------------------------------------------------------------
# ups-monitor.service (systemd) - runs ups.py headlessly, 24/7, regardless
# of desktop login state. Unlike battery.desktop above, this is generated
# fresh each run with paths matching wherever this script actually lives
# (not a fixed assumed location), so it stays correct if the folder moves.
# ---------------------------------------------------------------------------
SERVICE_NAME="ups-monitor.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PYTHON3="$(command -v python3)"

echo ""
echo "Checking ${SERVICE_NAME}..."

TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" << EOF
[Unit]
Description=UPS HAT (E) monitor with Alertzy + desktop notifications
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${curPath}
ExecStart=${PYTHON3} ${curPath}/ups.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

if [ -f "${SERVICE_PATH}" ] && cmp -s "${TMP_SERVICE}" "${SERVICE_PATH}"; then
    echo "  Already installed and up to date at ${SERVICE_PATH}."
    SERVICE_CHANGED=0
else
    echo "  Installing/updating ${SERVICE_PATH} (WorkingDirectory=${curPath})..."
    sudo cp "${TMP_SERVICE}" "${SERVICE_PATH}"
    sudo systemctl daemon-reload
    SERVICE_CHANGED=1
fi
rm -f "${TMP_SERVICE}"

if ! systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "  Not enabled yet - enabling (will start automatically on future boots)..."
    sudo systemctl enable "${SERVICE_NAME}"
else
    echo "  Already enabled."
fi

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    if [ "${SERVICE_CHANGED}" = "1" ]; then
        echo "  Already running, but the unit file changed - restarting to pick it up..."
        sudo systemctl restart "${SERVICE_NAME}"
    else
        echo "  Already running."
    fi
else
    echo "  Not running - starting now..."
    sudo systemctl start "${SERVICE_NAME}"
fi

sleep 1
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "  ${SERVICE_NAME} is up and running."
else
    echo "  WARNING: ${SERVICE_NAME} does not appear to be running."
    echo "  Check details with: journalctl -u ${SERVICE_NAME} -b"
fi
