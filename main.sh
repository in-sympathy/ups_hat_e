#!/bin/bash
echo "user name = ${USER}"
curPath=$(readlink -f "$(dirname "$0")")
echo $curPath

# ---------------------------------------------------------------------------
# I2C (always needed for UPS HAT (E) itself) and SPI (only needed for the
# optional Pioneer600 OLED, but harmless to enable either way) - checked and
# enabled automatically via raspi-config's non-interactive mode, so this
# doesn't need any manual raspi-config menu navigation. get_i2c/get_spi
# follow standard shell exit-code convention: 0 = already enabled, non-zero
# = disabled - same convention systemctl is-enabled/is-active use elsewhere
# in this script.
# ---------------------------------------------------------------------------
REBOOT_NEEDED=0

echo ""
echo "Checking I2C..."
if sudo raspi-config nonint get_i2c >/dev/null 2>&1; then
    echo "  Already enabled."
else
    echo "  Not enabled - enabling now..."
    sudo raspi-config nonint do_i2c 0
    REBOOT_NEEDED=1
fi

echo "Checking SPI..."
if sudo raspi-config nonint get_spi >/dev/null 2>&1; then
    echo "  Already enabled."
else
    echo "  Not enabled - enabling now (only strictly needed for a Pioneer600's OLED, but harmless regardless)..."
    sudo raspi-config nonint do_spi 0
    REBOOT_NEEDED=1
fi
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

if [ "${REBOOT_NEEDED}" = "1" ]; then
    echo "  Skipping start for now - I2C/SPI were just enabled and won't actually"
    echo "  work until you reboot, so starting it now would just fail. It's"
    echo "  enabled, though, so it'll start correctly on its own after the reboot."
else
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
fi

if [ "${REBOOT_NEEDED}" = "1" ]; then
    echo ""
    echo "================================================================"
    echo "I2C and/or SPI were just enabled for the first time - a reboot"
    echo "is needed before they'll actually work. Everything above is"
    echo "already set up and will start correctly on its own once you've"
    echo "rebooted - no need to run this script again afterward."
    echo ""
    echo "    sudo reboot"
    echo "================================================================"
fi
