#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import smbus
import time
import logging
import signal
import socket
import threading
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
import PyQt5
from PyQt5.QtGui import (
    QIcon,
    QPixmap
)
from PyQt5.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QAction,
    QMessageBox
)
from PyQt5.QtCore import (
    QObject,
    QThread,
    pyqtSignal,
    QTimer,
    QSize
)

signal.signal(signal.SIGINT, signal.SIG_DFL)
logging.basicConfig(format="%(message)s", level=logging.INFO)

ADDR = 0x2d
LOW_VOL = 3150 #mV

# ---------------------------------------------------------------------------
# Alertzy - https://alertzy.app (same setup as ups.py - one alertzy.key file
# is read by both, so there's only one place to update the key)
# ---------------------------------------------------------------------------
def _load_alertzy_key():
    """Reads the account key from alertzy.key in the same folder as this
    script, if present. Falls back to the placeholder notify() already
    treats as "not configured yet" if the file is missing/empty/unreadable."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()  # matches battery.sh/systemd, which both cd here first
    key_path = os.path.join(script_dir, "alertzy.key")
    try:
        with open(key_path) as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    return "YOUR_ALERTZY_ACCOUNT_KEY_HERE"


ALERTZY_ACCOUNT_KEY = _load_alertzy_key()  # or paste your key directly here instead
ALERTZY_URL = "https://alertzy.app/send"
DEVICE_NAME = socket.gethostname()
# All boards share one Alertzy group/folder; the title carries the hostname
# instead, so individual boards are told apart there.
ALERTZY_GROUP = "RaspberryPi"

# Register 0x02 BIT5 (0x20) = "VBUS is powered" per Waveshare's Register
# Manual - see the equivalent note in ups_monitor.py for why this is trusted
# over the "Discharge state" label the stock code below uses for this bit.
BIT_VBUS_POWERED = 0x20
# Consecutive matching 1s Worker ticks required before a power-state change
# is trusted (3 * 1s = 3s) - filters out a single noisy I2C sample.
POWER_STATE_CONFIRMATIONS = 3


def notify(title, message, priority=0, group=ALERTZY_GROUP):
    """Send an Alertzy push notification on a background thread, so a slow
    network call can never freeze the tray icon / UI thread. Never raises."""
    def _send():
        if not REQUESTS_AVAILABLE:
            print(f"[Alertzy skipped - 'requests' not installed, see NOTES.md] {title}: {message}")
            return
        if ALERTZY_ACCOUNT_KEY == "YOUR_ALERTZY_ACCOUNT_KEY_HERE":
            print(f"[Alertzy skipped - no account key set] {title}: {message}")
            return
        try:
            files = {
                "accountKey": (None, ALERTZY_ACCOUNT_KEY),
                "title": (None, title),
                "message": (None, message),
                "group": (None, group),
                "priority": (None, str(priority)),
            }
            response = requests.post(ALERTZY_URL, files=files, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Alertzy notification failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


bus = smbus.SMBus(1)
        
class Worker(QObject):
    trayMessage = pyqtSignal(list, list, list, list)

    def run(self):
        while True:
            data = bus.read_i2c_block_data(ADDR, 0x02, 0x02)
            list1 = data
            data = bus.read_i2c_block_data(ADDR, 0x10, 0x06)
            list2 = [0,0,0]
            list2[0] = data[0] | data[1] << 8
            list2[1] = data[2] | data[3] << 8
            list2[2] = data[4] | data[5] << 8
            data = bus.read_i2c_block_data(ADDR, 0x20, 0x0C)
            list3 = [0,0,0,0,0,0]
            list3[0] = data[0] | data[1] << 8
            list3[1] = data[2] | data[3] << 8
            list3[2] = data[4] | data[5] << 8
            list3[3] = data[6] | data[7] << 8
            list3[4] = data[8] | data[9] << 8
            list3[5] = data[10] | data[11] << 8
            if(list3[1] > 0x7FFF):
                list3[1] -= 0xFFFF
            data = bus.read_i2c_block_data(ADDR, 0x30, 0x08)
            list4 = [0,0,0,0]
            list4[0] = data[0] | data[1] << 8
            list4[1] = data[2] | data[3] << 8
            list4[2] = data[4] | data[5] << 8
            list4[3] = data[6] | data[7] << 8
            # bus_voltage = 4.00             # voltage on V- (load side)
            # current = 1000                   # current in mA
            self.trayMessage.emit(list1, list2, list3, list4)
            time.sleep(1);
        
class MainWindow(QMessageBox):
    # Override the class constructor
    def __init__(self):
        # Be sure to call the super class method
        self.charge = 0
        self.tray_icon = None
        self.msgBox = None
        self.about = None
        self.counter = 0

        # Mains power lost/restored notification state - see refresh()
        self.vbus_confirmed = None
        self.vbus_candidate = None
        self.candidate_count = 0
    
        QMessageBox.__init__(self)
        self.resize(QSize(300, 200))
        self.setWindowTitle("Battery Status")  # Set a title
        
        # Init QSystemTrayIcon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("images/battery.png"))

        '''
            Define and add steps to work with the system tray icon
            show - show window
            Status - Status window
            exit - exit from application
        '''
        show_action = QAction("Status", self)
        quit_action = QAction("Exit", self)
        about_action = QAction("About", self)
        show_action.triggered.connect(self.show)
        about_action.triggered.connect(self.show_about)
        quit_action.triggered.connect(QApplication.instance().quit)
        tray_menu = QMenu()
        tray_menu.addAction(show_action)
        tray_menu.addAction(about_action)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        self._thread = QThread(self)
        self._worker = Worker()
        self._worker.moveToThread(self._thread)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.started.connect(self._worker.run)
        self._worker.trayMessage.connect(self.refresh)
        self._thread.start()
        self._timer = QTimer(self,timeout=self.on_timeout)
        self._timer.stop()
    
    def on_timeout(self):
        self.counter -= 1
        if(self.counter > 0):  #countdown
            if(self.charge == 1):
                self.msgBox.hide()
                self.msgBox.close()
                self._timer.stop()
                self.msgBox = None
            else:
                self.msgBox.setInformativeText("auto shutdown after " +str(int(self.counter)) + " seconds");
                self.msgBox.show()
        else:                  #timeout
            address = os.popen("i2cdetect -y -r 1 0x2d 0x2d | egrep '2d' | awk '{print $2}'").read()
            if(address=='2d\n'):
                #print("If charged, the system can be powered on again.")
                #write 0x55 to 0x01 register of 0x2d Address device
                os.popen("i2cset -y 1 0x2d 0x01 0x55")
            os.system("sudo poweroff")

    def refresh(self, list1, list2, list3, list4):
        v = list3[0] / 1000   #Battery Voltage
        c = list3[1] / 1000  #Battery Current
        if(c > 0):self.charge = 1
        else:self.charge = 0
        
        p = list3[2]   #Battery Percentage
        img = "images/battery." + str(int(p / 10 + self.charge * 11)) + ".png"
        self.tray_icon.setIcon(QIcon(img))
        # Re-assert visibility every cycle, not just once at __init__. Under
        # Wayland (labwc/wf-panel-pi and similar StatusNotifierItem hosts),
        # the panel's tray watcher isn't always ready the instant this app
        # starts, and unlike the one-shot X11 systray protocol, nothing
        # automatically retries a registration that didn't land the first
        # time. Calling show() on an already-visible icon is a harmless
        # no-op, so this costs nothing once it's up, but means a slow or
        # not-yet-ready panel gets asked again every second instead of once.
        self.tray_icon.show()
        self.setIconPixmap(QPixmap(img))
        s = "%d%%  %.1fV  %.2fA" % (p,v,c)
        self.tray_icon.setToolTip(s)
        if(list1[0] & 0x40):
            info1 = "Fast Charging state\n"
        elif(list1[0] & 0x80):
            info1 = "Charging state\n"
        elif(list1[0] & 0x20):
            info1 = "Discharge state\n"
        else:
            info1 = "Idle state\n"

        # Mains power lost/restored - debounced edge detection on the VBUS
        # bit, same approach as ups_monitor.py. Runs off this method's 1s
        # tick, so POWER_STATE_CONFIRMATIONS * 1s to confirm a change.
        vbus_powered = bool(list1[0] & BIT_VBUS_POWERED)
        if vbus_powered == self.vbus_candidate:
            self.candidate_count += 1
        else:
            self.vbus_candidate = vbus_powered
            self.candidate_count = 1
        if self.candidate_count >= POWER_STATE_CONFIRMATIONS:
            if self.vbus_confirmed is None:
                # First confirmed reading since startup - record the
                # baseline silently, don't fire a notification for it.
                self.vbus_confirmed = self.vbus_candidate
            elif self.vbus_candidate != self.vbus_confirmed:
                self.vbus_confirmed = self.vbus_candidate
                if self.vbus_confirmed:
                    popup_title = f"{DEVICE_NAME} runs on MAINS"
                    alert_msg = f"Mains power restored. Battery at {p}%."
                    notify(DEVICE_NAME, alert_msg, priority=0)
                    self.tray_icon.showMessage(popup_title, alert_msg, QSystemTrayIcon.Information, 10000)
                else:
                    popup_title = f"{DEVICE_NAME} runs on UPS"
                    alert_msg = f"Mains power lost. Running on UPS battery ({p}%)."
                    notify(DEVICE_NAME, alert_msg, priority=0)
                    self.tray_icon.showMessage(popup_title, alert_msg, QSystemTrayIcon.Warning, 10000)

        info2 = "Voltage:    %2.1fV           Capacity:   %dmAh\n" % (v,list3[3])
        info3 = "Current:    %2.2fA          Time To Empty   %d min\n" % (c,list3[4])
        info4 = "Percent:    %4d%%           Time To Full    %d min\n" % (p,list3[5])
        info5 = "Cell V1:     %4dmV        VBUS Voltage   %2.2fV\n" % (list4[0],list2[0]/1000)
        info6 = "Cell V2:     %4dmV        VBUS Current   %1.2fA\n" % (list4[1],list2[1]/1000)
        info7 = "Cell V3:     %4dmV        VBUS Power     %2.1fW\n" % (list4[2],list2[2]/1000)
        info8 = "Cell V4:     %4dmV        " % (list4[3])
        self.setText(info2+info3+info4+info5+info6+info7+info8+info1);
        localTime = time.localtime(time.time())
        logging.info(f"{localTime.tm_year:04d}-{localTime.tm_mon:02d}-{localTime.tm_mday:02d} {localTime.tm_hour:02d}:{localTime.tm_min:02d}:{localTime.tm_sec:02d}  {s}")
        if(((list4[0] < LOW_VOL) or (list4[1] < LOW_VOL) or (list4[2] < LOW_VOL) or (list4[3] < LOW_VOL)) and self.charge == 0):
            if(self.msgBox == None):
                popup_title = f"{DEVICE_NAME} - LOW BATTERY"
                alert_msg = f"Battery critically low ({p}%). Shutting down in 60s if not charged."
                notify(DEVICE_NAME, alert_msg, priority=0)
                self.tray_icon.showMessage(popup_title, alert_msg, QSystemTrayIcon.Critical, 10000)
                self.counter = 60
                self._timer.start(1000)
                self.msgBox = QMessageBox(QMessageBox.NoIcon,'Battery Warning',"<p><strong>The battery level is below<br>Please connect in the power adapter</strong>")
                self.msgBox.setIconPixmap(QPixmap("images/batteryQ.png"))
                self.msgBox.setInformativeText("auto shutdown after 60 seconds");
                self.msgBox.setStandardButtons(QMessageBox.NoButton);
                self.msgBox.exec()
            
    def show_about(self):
        if(self.about == None):
            self.about = QMessageBox(QMessageBox.NoIcon,'About',"<p><strong>Battery Monitor Demo</strong><p>Version: v1.0<p>It's a battery Display By waveshare\n")
            self.about.setInformativeText("<a href=\"https://www.waveshare.com\">WaveShare Official Website</a>");
            self.about.setIconPixmap(QPixmap("images/logo.png"))
            self.about.setDefaultButton(None)
            self.about.exec()
            self.about = None

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    mw = MainWindow()
    sys.exit(app.exec_())