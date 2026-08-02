#!/usr/bin/env python3
"""
UPS HAT (E) monitor with Alertzy push notifications.

A modified version of Waveshare's stock UPS HAT (E) demo script, extended
with the same kind of Alertzy push-notification support used in the UPS HAT
(B) monitor (https://github.com/in-sympathy/Waveshare_UPS_Hat_B_Monitor),
adapted to this HAT's register map:
https://www.waveshare.com/wiki/UPS_HAT_(E)_Register

What's added on top of the stock demo:
  - Alertzy push notification when mains (VBUS) power is lost / restored
  - Alertzy push notification when the low-voltage safety shutdown countdown
    begins (in addition to the existing stock auto-shutdown behaviour)
  - A local on-screen popup (notify-send) for the same three events, when a
    desktop session happens to be active on the board at that moment
  - A few consecutive-read confirmations before trusting a power-state
    change, so one noisy I2C sample can't fire a false alert
  - A try/except around each poll so a transient I2C error can't silently
    kill the whole monitor loop (and with it, the safety shutdown)
  - Optional: if a Waveshare Pioneer600 HAT is also stacked on this board,
    its SSD1306 OLED shows hostname, battery, network, RAM, and disk status.
    Detected automatically at startup - a no-op on boards without one.

The `requests` import below is optional on purpose: this file is meant to be
a drop-in replacement for Waveshare's stock ups.py, runnable with exactly
the commands their wiki documents (no new install step required). Without
`requests` installed, everything above still works exactly as it did in the
stock demo - only the Alertzy push notifications are skipped (with a clear
one-line note printed each time). See NOTES.md for the one-line install if
you want Alertzy actually sending pushes.
"""

import os
import socket
import subprocess
import time

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Optional Pioneer600 OLED support - see the "Pioneer600 OLED" section below
# for why these are two separate try blocks.
try:
    import spidev
    import RPi.GPIO as GPIO  # On Pi 5: `pip install rpi-lgpio` instead of RPi.GPIO - same import name, drop-in.
    from PIL import Image, ImageDraw, ImageFont
    OLED_LIBS_AVAILABLE = True
except ImportError:
    OLED_LIBS_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

import smbus

# ---------------------------------------------------------------------------
# Alertzy - https://alertzy.app
# ---------------------------------------------------------------------------
def _load_alertzy_key():
    """Reads the account key from alertzy.key in the same folder as this
    script, if present - keeps the real key out of git while the actual
    notification logic still lives in the repo. Falls back to the same
    placeholder notify() already treats as "not configured yet" if the
    file is missing, empty, or unreadable."""
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

# All boards share one Alertzy group/folder; the title (below) carries the
# hostname instead, so individual boards are told apart there.
ALERTZY_GROUP = "RaspberryPi"
DEVICE_NAME = socket.gethostname()


def notify(title, message, priority=0, group=ALERTZY_GROUP):
    """Send an Alertzy push notification. Never raises - a network hiccup
    should never be able to take down the monitoring/shutdown loop below."""
    if not REQUESTS_AVAILABLE:
        print(f"[Alertzy skipped - 'requests' not installed, see NOTES.md] {title}: {message}")
        return False
    if ALERTZY_ACCOUNT_KEY == "YOUR_ALERTZY_ACCOUNT_KEY_HERE":
        print(f"[Alertzy skipped - no account key set] {title}: {message}")
        return False
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
        return True
    except requests.RequestException as e:
        print(f"Alertzy notification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Local on-screen popup via notify-send - only fires when a desktop session
# actually happens to be active on the board at that moment.
# ---------------------------------------------------------------------------
def _find_active_graphical_session():
    """Return (username, dbus_address, display) for the first active local
    graphical (x11/wayland) login session, or None if the board is headless
    right now. Uses loginctl rather than assuming a fixed DISPLAY/user, since
    who (if anyone) is logged into the desktop can change over the life of
    this long-running script."""
    try:
        sessions = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
        for line in sessions:
            parts = line.split()
            if not parts:
                continue
            session_id = parts[0]
            props = subprocess.run(
                ["loginctl", "show-session", session_id,
                 "-p", "Name", "-p", "Type", "-p", "State", "-p", "Display"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            info = dict(p.split("=", 1) for p in props.splitlines() if "=" in p)
            if info.get("Type") in ("x11", "wayland") and info.get("State") == "active":
                username = info.get("Name")
                uid = subprocess.run(
                    ["id", "-u", username], capture_output=True, text=True, timeout=5
                ).stdout.strip()
                bus_path = f"/run/user/{uid}/bus"
                if uid and os.path.exists(bus_path):
                    return username, f"unix:path={bus_path}", info.get("Display") or ":0"
    except Exception:
        pass
    return None


def desktop_notify(title, message, urgency="normal", icon="dialog-information"):
    """Show a local popup via notify-send, if a desktop session is active on
    this board right now. Silently skipped when headless - never raises,
    same philosophy as notify(). Requires libnotify-bin (apt install
    libnotify-bin) and passwordless sudo for the session's user, same
    assumption the stock demo already makes for `sudo poweroff`."""
    session = _find_active_graphical_session()
    if session is None:
        print(f"[Desktop notify skipped - no active graphical session] {title}: {message}")
        return False
    username, dbus_address, display = session
    try:
        subprocess.run(
            ["sudo", "-u", username, "env",
             f"DISPLAY={display}", f"DBUS_SESSION_BUS_ADDRESS={dbus_address}",
             "notify-send", "-u", urgency, "-i", icon, title, message],
            timeout=5, check=False,
        )
        return True
    except Exception as e:
        print(f"Desktop notification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Optional: Pioneer600 SSD1306 OLED status display
#
# Hardware facts, confirmed against Waveshare's own Pioneer600 demo code
# (github.com/tayfunulu/Pioneer600, a mirror of their official sample):
#   - The OLED is SSD1306, 128x64, 4-wire SPI - NOT I2C, unlike most of the
#     other Pioneer600 peripherals.
#   - RST = BCM GPIO 19, DC = BCM GPIO 16, hardware SPI0 CE0 (bus=0, device=0)
#   - Also onboard: a PCF8574 GPIO expander at I2C address 0x20, and a
#     DS3231 RTC at 0x68 (fixed address, no address pins) - used below purely
#     as a presence-detection proxy for the whole board. SPI has no
#     bus-scan/ACK mechanism the way I2C does, so there's no way to directly
#     "probe" the OLED itself; if either of these known I2C chips answers on
#     the same bus UPS HAT (E) is already on, the Pioneer600 - OLED included
#     - is physically stacked here.
#
# spidev/RPi.GPIO/PIL and psutil are deliberately two separate try blocks:
# psutil (RAM/disk/network stats) is a genuinely independent concern from
# actually driving the screen, so a board with one but not the other still
# gets a partially-working display instead of nothing.
# ---------------------------------------------------------------------------
OLED_RST_PIN = 19
OLED_DC_PIN = 16
OLED_SPI_BUS = 0
OLED_SPI_DEVICE = 0
OLED_WIDTH = 128
OLED_HEIGHT = 64
PIONEER600_PROBE_ADDRESSES = (0x68, 0x20)  # DS3231, PCF8574
# When both wlan0 and eth0 are connected at once, showing both permanently
# forces the font down to a size too small to read on the physical 128x64
# screen - so instead only one network line is shown at a time, alternating
# every N render cycles. 1 = switch every cycle (~2s, matches the main
# loop's poll interval); bump this up if that feels too fast to read.
OLED_NETWORK_CYCLE_INTERVAL = 1


class _SSD1306:
    """Write-only SPI driver for the SSD1306 128x64 OLED. Structured to
    match Waveshare's own proven-working SSD1306.py as closely as possible:
    same constructor call pattern (see _open_spi below), same clear()/
    image()/display() method split, same command()-per-byte SPI framing,
    no explicit speed override - after two rounds of "more conventional but
    not what's actually proven on this hardware" mistakes here, this is a
    deliberately faithful port rather than a reimagining."""

    WIDTH = OLED_WIDTH
    HEIGHT = OLED_HEIGHT
    PAGES = HEIGHT // 8

    def __init__(self, rst_pin, dc_pin, spi):
        self._rst = rst_pin
        self._dc = dc_pin
        self._spi = spi
        self._buffer = [0] * (self.WIDTH * self.PAGES)
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self._dc, GPIO.OUT)
        GPIO.setup(self._rst, GPIO.OUT)

    def _command(self, cmd):
        GPIO.output(self._dc, GPIO.LOW)
        self._spi.writebytes([cmd])

    def reset(self):
        GPIO.output(self._rst, GPIO.HIGH)
        time.sleep(0.001)
        GPIO.output(self._rst, GPIO.LOW)
        time.sleep(0.010)
        GPIO.output(self._rst, GPIO.HIGH)

    def begin(self):
        self.reset()
        for cmd in (
            0xAE,                    # display off
            0xD5, 0x80,               # clock divide
            0xA8, 0x3F,               # multiplex 64
            0xD3, 0x00,               # display offset
            0x40,                     # start line 0
            0x8D, 0x14,               # charge pump on
            0x20, 0x00,               # horizontal addressing mode
            0xA1,                     # segment remap
            0xC8,                     # COM scan direction
            0xDA, 0x12,               # COM pins
            0x81, 0xCF,               # contrast
            0xD9, 0xF1,               # precharge
            0xDB, 0x40,               # VCOMH deselect
            0xA4,                     # resume to RAM content
            0xA6,                     # normal (not inverted) display
            0xAF,                     # display on
        ):
            self._command(cmd)

    def display(self):
        """Write the internal buffer to the physical display."""
        self._command(0x21)
        self._command(0)
        self._command(self.WIDTH - 1)
        self._command(0x22)
        self._command(0)
        self._command(self.PAGES - 1)
        GPIO.output(self._dc, GPIO.HIGH)
        self._spi.writebytes(self._buffer)

    def image(self, pil_image):
        """Load a PIL Image (mode '1', 128x64) into the internal buffer -
        does not touch the display, call display() after to push it out."""
        if pil_image.size != (self.WIDTH, self.HEIGHT):
            raise ValueError(f"image must be {self.WIDTH}x{self.HEIGHT}")
        pix = pil_image.load()
        index = 0
        for page in range(self.PAGES):
            for x in range(self.WIDTH):
                byte = 0
                for bit in range(8):
                    if pix[x, page * 8 + bit]:
                        byte |= (1 << bit)
                self._buffer[index] = byte
                index += 1

    def clear(self):
        self._buffer = [0] * (self.WIDTH * self.PAGES)


def pioneer600_present(i2c_bus):
    """Probe for a known Pioneer600 chip on the I2C bus. Returns False (not
    an exception) for anything that isn't a clean ACK, since a nonexistent
    address is the expected/normal case on a board without Pioneer600."""
    for addr in PIONEER600_PROBE_ADDRESSES:
        try:
            i2c_bus.read_byte(addr)
            chip = "DS3231 RTC" if addr == 0x68 else "PCF8574 GPIO expander"
            print(f"Pioneer600 presence probe: {chip} acked at {hex(addr)}")
            return True
        except Exception:
            continue
    return False


def _open_spi():
    """Waveshare's own proven code calls spidev.SpiDev(bus, device) directly
    - a single-call constructor form some spidev builds (notably the
    Raspberry Pi OS apt package) support as a shortcut, though it's not in
    the canonical PyPI spidev docs. Try that exact proven form first; fall
    back to the more universally-documented SpiDev() + .open() two-step for
    any spidev build that only supports that."""
    try:
        return spidev.SpiDev(OLED_SPI_BUS, OLED_SPI_DEVICE)
    except TypeError:
        spi = spidev.SpiDev()
        spi.open(OLED_SPI_BUS, OLED_SPI_DEVICE)
        return spi


def init_oled(i2c_bus, device_name, attempt=1, max_attempts=1):
    """A single, fast, non-blocking detection + setup attempt. Returns
    (disp, image, draw) on success, or None if the screen isn't there /
    libraries aren't installed / anything about SPI or GPIO setup fails.
    Never raises, never sleeps.

    `attempt`/`max_attempts` control nothing about retrying here - that's
    the caller's job (see the main loop below, which calls this once per
    poll cycle while nothing has succeeded yet). They only decide whether
    to print an in-progress message: the presence check runs fresh every
    call, so a transient miss (I2C not ready yet at the exact moment of one
    read) doesn't get printed as a hard failure on every single retry -
    only on the first attempt and the last one, so the log shows what's
    happening without a line every 2 seconds for up to a minute."""
    if not OLED_LIBS_AVAILABLE:
        print("[OLED skipped - spidev/RPi.GPIO/Pillow not installed]")
        return None
    if not pioneer600_present(i2c_bus):
        if attempt == 1 or attempt == max_attempts:
            print(f"[OLED not detected yet on the I2C bus (attempt {attempt}/{max_attempts})]")
        return None
    try:
        spi = _open_spi()
        disp = _SSD1306(OLED_RST_PIN, OLED_DC_PIN, spi)
        disp.begin()
        disp.clear()
        disp.display()
        image = Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
        draw = ImageDraw.Draw(image)
        print(f"Pioneer600 OLED detected and initialized ({device_name})")
        return disp, image, draw
    except Exception as e:
        if attempt == 1 or attempt == max_attempts:
            print(f"[OLED init attempt {attempt}/{max_attempts} failed: {e}]")
        return None


_font_cache = {}


def _get_font(size):
    """Cached font lookup - load_default(size=...) needs Pillow >= 10.1;
    older Pillow falls back to its one fixed-size default for every size."""
    if size not in _font_cache:
        try:
            _font_cache[size] = ImageFont.load_default(size=size)
        except TypeError:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def _pick_font_size(num_lines):
    """Sizes checked against the default PIL font's actual glyph bounding
    box at each likely line count, so there's always a few pixels of margin
    rather than an exact pixel-for-pixel fit."""
    if num_lines <= 3:
        return 13
    if num_lines == 4:
        return 11
    if num_lines == 5:
        return 10
    return 8  # 6+ lines - not currently reachable (cycling caps network
              # lines at one, so 5 is the real max), kept as a safety net
              # in case a future change adds another line


def _fmt_gb(n_bytes):
    return f"{n_bytes / (1024 ** 3):.1f}G"


def _fit_line(text, font, max_width, draw):
    """Truncate text with '..' if it would overflow max_width at this font -
    protects against hostnames (unbounded length) overflowing the display."""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while text and draw.textbbox((0, 0), text + "..", font=font)[2] > max_width:
        text = text[:-1]
    return text + ".." if text else ""


def gather_oled_lines(device_name, percent, charging, network_cycle=0):
    """Build the list of text lines to show, plus the index of the network
    line if it's currently alternating between two interfaces (or None if
    not) - render_oled() uses that index to add a ">>>" hint if there's
    room, without this function needing to know about fonts/pixel widths
    itself. `network_cycle` is an ever-increasing counter (the caller
    passes in how many render cycles have happened) - when both wlan0 and
    eth0 are connected, this decides which one gets shown this cycle,
    alternating between them, rather than showing both permanently and
    forcing the font down to a size too small to read. Each field is
    gathered independently so one failure doesn't blank out the rest of
    the screen."""
    lines = [
        f"Host: {device_name}",
        f"Batt: {percent}% - {'CHG' if charging else 'DIS'}",
    ]
    cycling_index = None

    if not PSUTIL_AVAILABLE:
        return lines, cycling_index

    try:
        addrs = psutil.net_if_addrs()
        wlan0_ip = next((a.address for a in addrs.get("wlan0", ())
                          if a.family == socket.AF_INET), None)
        eth0_ip = next((a.address for a in addrs.get("eth0", ())
                         if a.family == socket.AF_INET), None)
        if wlan0_ip and eth0_ip:
            show_wlan0 = (network_cycle // OLED_NETWORK_CYCLE_INTERVAL) % 2 == 0
            if show_wlan0:
                lines.append(f"wlan0: {wlan0_ip}")
            else:
                lines.append(f"eth0: {eth0_ip}")
            cycling_index = len(lines) - 1  # the other interface is waiting behind this one
        elif wlan0_ip:
            lines.append(f"wlan0: {wlan0_ip}")
        elif eth0_ip:
            lines.append(f"eth0: {eth0_ip}")
    except Exception:
        pass

    try:
        vm = psutil.virtual_memory()
        lines.append(f"RAM: {_fmt_gb(vm.available)}/{_fmt_gb(vm.total)}")
    except Exception:
        pass

    try:
        du = psutil.disk_usage("/")
        lines.append(f"Disk: {_fmt_gb(du.free)}/{_fmt_gb(du.total)}")
    except Exception:
        pass

    return lines, cycling_index


def render_oled(oled_context, device_name, percent, charging, network_cycle=0):
    """Draw the current status lines to the display. Raises on failure -
    the caller wraps this in its own try/except so a transient SPI/render
    error can't take down the main monitor loop."""
    disp, image, draw = oled_context
    lines, cycling_index = gather_oled_lines(device_name, percent, charging, network_cycle)
    font = _get_font(_pick_font_size(len(lines)))
    if cycling_index is not None:
        # Hint that a second interface is connected too and will show up
        # next cycle - but only if it actually fits. Dropping the hint is
        # better than truncating into it (or worse, into the IP itself),
        # which is what would happen if this were just appended blindly.
        hinted = lines[cycling_index] + " >>>"
        if draw.textbbox((0, 0), hinted, font=font)[2] <= OLED_WIDTH:
            lines[cycling_index] = hinted
    draw.rectangle((0, 0, OLED_WIDTH, OLED_HEIGHT), outline=0, fill=0)
    line_height = OLED_HEIGHT // max(len(lines), 1)
    for i, line in enumerate(lines):
        draw.text((0, i * line_height), _fit_line(line, font, OLED_WIDTH, draw), font=font, fill=255)
    disp.image(image)
    disp.display()


# ---------------------------------------------------------------------------
# UPS HAT (E) - register map from the Waveshare wiki
# ---------------------------------------------------------------------------
ADDR = 0x2d
LOW_VOL = 3150  # mV, per-cell low-voltage threshold (unchanged from stock)

# Register 0x02, per Waveshare's Register Manual:
#   BIT7 = 1 charging / 0 not charging
#   BIT6 = 1 fast charging / 0 not fast charging
#   BIT5 = 1 VBUS is powered / 0 VBUS is not powered
# NOTE: the stock ups.py demo labels the BIT5 (0x20) branch as "Discharge
# state" in its print statements, but the dedicated Register Manual defines
# that same bit as "VBUS is powered" - the opposite of discharge. The manual
# is the bit-exact authoritative reference (the print label is just an
# informal comment), and "VBUS powered" is exactly the signal mains-loss
# detection needs, so this script trusts the manual and treats "not charging
# AND VBUS not powered" as the real discharge/on-battery case.
BIT_CHARGING = 0x80
BIT_FAST_CHARGING = 0x40
BIT_VBUS_POWERED = 0x20

POLL_INTERVAL_SECONDS = 2
# Consecutive matching reads required before a power-state change is trusted
# (3 reads * 2s = 6s) - filters out a single noisy I2C sample.
POWER_STATE_CONFIRMATIONS = 3

bus = smbus.SMBus(1)

# Retried once per poll cycle (not blocked-on here) up to this many times -
# ~60s of slack at the 2s poll interval for whatever boot-time raciness
# (I2C/SPI not fully ready yet) a cold boot might have that a warm
# `systemctl restart` wouldn't, without delaying the safety-critical
# monitoring loop below by even one cycle to wait for it.
OLED_INIT_MAX_ATTEMPTS = 30
oled_init_attempts = 1 if OLED_LIBS_AVAILABLE else OLED_INIT_MAX_ATTEMPTS
oled_context = init_oled(bus, DEVICE_NAME, oled_init_attempts, OLED_INIT_MAX_ATTEMPTS)
oled_network_cycle = 0

low = 0
vbus_confirmed = None    # confirmed mains-power state; None = not established yet
vbus_candidate = None    # most recent value being confirmed
candidate_count = 0

print(f"UPS HAT (E) monitor starting on {DEVICE_NAME}")

while True:
    try:
        status = bus.read_i2c_block_data(ADDR, 0x02, 0x01)[0]
        charging = bool(status & BIT_CHARGING)
        fast_charging = bool(status & BIT_FAST_CHARGING)
        vbus_powered = bool(status & BIT_VBUS_POWERED)

        if fast_charging:
            print("Fast Charging state")
        elif charging:
            print("Charging state")
        elif vbus_powered:
            print("Idle state (VBUS powered, not charging)")
        else:
            print("Discharge state (running on battery)")

        data = bus.read_i2c_block_data(ADDR, 0x10, 0x06)
        print("VBUS Voltage %5dmV" % (data[0] | data[1] << 8))
        print("VBUS Current %5dmA" % (data[2] | data[3] << 8))
        print("VBUS Power   %5dmW" % (data[4] | data[5] << 8))

        data = bus.read_i2c_block_data(ADDR, 0x20, 0x0C)
        print("Battery Voltage %d mV" % (data[0] | data[1] << 8))
        current = data[2] | data[3] << 8
        if current > 0x7FFF:
            current -= 0xFFFF
        print("Battery Current %d mA" % current)
        percent = int(data[4] | data[5] << 8)
        print("Battery Percent %d%%" % percent)
        print("Remaining Capacity %d mAh" % (data[6] | data[7] << 8))
        if current < 0:
            print("Run Time To Empty %d min" % (data[8] | data[9] << 8))
        else:
            print("Average Time To Full %d min" % (data[10] | data[11] << 8))

        data = bus.read_i2c_block_data(ADDR, 0x30, 0x08)
        V1 = data[0] | data[1] << 8
        V2 = data[2] | data[3] << 8
        V3 = data[4] | data[5] << 8
        V4 = data[6] | data[7] << 8
        print("Cell Voltage1 %d mV" % V1)
        print("Cell Voltage2 %d mV" % V2)
        print("Cell Voltage3 %d mV" % V3)
        print("Cell Voltage4 %d mV" % V4)

        # -----------------------------------------------------------
        # Mains power lost / restored - debounced edge detection
        # -----------------------------------------------------------
        if vbus_powered == vbus_candidate:
            candidate_count += 1
        else:
            vbus_candidate = vbus_powered
            candidate_count = 1

        if candidate_count >= POWER_STATE_CONFIRMATIONS:
            if vbus_confirmed is None:
                # First confirmed reading since startup - record the
                # baseline AND send a one-off "here's the current state"
                # notification. Deliberately distinct from the lost/restored
                # messages below: this isn't a transition (there's no way
                # to know what happened before this process started, e.g.
                # after a reboot), just reporting current status right
                # after starting up.
                vbus_confirmed = vbus_candidate
                if vbus_confirmed:
                    msg = f"Started up. Running on mains. Battery at {percent}%."
                    print(msg)
                    popup_title = f"{DEVICE_NAME} - Startup: on MAINS"
                    notify(DEVICE_NAME, msg, priority=0)
                    desktop_notify(popup_title, msg, urgency="normal", icon="battery-good-charging")
                else:
                    msg = f"Started up. Running on UPS battery ({percent}%)."
                    print(msg)
                    popup_title = f"{DEVICE_NAME} - Startup: on UPS"
                    notify(DEVICE_NAME, msg, priority=0)
                    desktop_notify(popup_title, msg, urgency="critical", icon="dialog-warning")
            elif vbus_candidate != vbus_confirmed:
                vbus_confirmed = vbus_candidate
                if vbus_confirmed:
                    msg = f"Mains power restored. Battery at {percent}%."
                    print(msg)
                    popup_title = f"{DEVICE_NAME} runs on MAINS"
                    notify(DEVICE_NAME, msg, priority=0)
                    desktop_notify(popup_title, msg, urgency="normal", icon="battery-good-charging")
                else:
                    msg = f"Mains power lost. Running on UPS battery ({percent}%)."
                    print(msg)
                    popup_title = f"{DEVICE_NAME} runs on UPS"
                    notify(DEVICE_NAME, msg, priority=0)
                    desktop_notify(popup_title, msg, urgency="critical", icon="dialog-warning")

        # -----------------------------------------------------------
        # Low-voltage safety shutdown - identical to the stock demo,
        # plus one Alertzy heads-up when the countdown starts.
        # -----------------------------------------------------------
        if ((V1 < LOW_VOL) or (V2 < LOW_VOL) or (V3 < LOW_VOL) or (V4 < LOW_VOL)) and (current < 50):
            if low == 0:
                popup_title = f"{DEVICE_NAME} - LOW BATTERY"
                msg = f"Battery critically low ({percent}%). Shutting down in 60s if not charged."
                notify(DEVICE_NAME, msg, priority=0)
                desktop_notify(popup_title, msg, urgency="critical", icon="battery-caution")
            low += 1
            if low >= 30:
                print("System shutdown now")
                address = os.popen("i2cdetect -y -r 1 0x2d 0x2d | egrep '2d' | awk '{print $2}'").read()
                if address != '2d\n':
                    print("0x2d i2c address not detected, something wrong.")
                else:
                    print("If charged, the system can be powered on again")
                    os.popen("i2cset -y 1 0x2d 0x01 0x55")
                os.system("sudo poweroff")
            else:
                print("Voltage Low,please charge in time,otherwise it will shut down in {:2d} s".format(60 - 2 * low))
        else:
            low = 0

        if oled_context is None and oled_init_attempts < OLED_INIT_MAX_ATTEMPTS:
            oled_init_attempts += 1
            oled_context = init_oled(bus, DEVICE_NAME, oled_init_attempts, OLED_INIT_MAX_ATTEMPTS)

        if oled_context is not None:
            oled_network_cycle += 1
            try:
                render_oled(oled_context, DEVICE_NAME, percent, charging or fast_charging, oled_network_cycle)
            except Exception as e:
                print(f"OLED render failed, skipping this cycle: {e}")

        print("")

    except OSError as e:
        # Transient I2C error (e.g. "Remote I/O error") - skip this cycle
        # rather than crashing the whole monitor.
        print(f"I2C read failed, skipping this cycle: {e}")

    time.sleep(POLL_INTERVAL_SECONDS)
