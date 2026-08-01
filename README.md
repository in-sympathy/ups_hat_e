# Waveshare UPS HAT (E) Monitor

![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-c51a4a)
![Python](https://img.shields.io/badge/python-3-blue)
![Based on](https://img.shields.io/badge/based%20on-Waveshare%20demo-orange)

A customised fork of Waveshare's stock UPS HAT (E) demo scripts, extended with Alertzy push notifications, on-screen desktop alerts, and an optional live status display on a stacked Pioneer600 HAT's OLED screen — while leaving the original register handling, low-voltage safety shutdown, and tray icon completely untouched. Sibling project to [Waveshare_UPS_Hat_B_Monitor](https://github.com/in-sympathy/Waveshare_UPS_Hat_B_Monitor).

## Why this exists

Waveshare's stock demo tells you your battery percentage if you're sitting in front of the screen watching it. It doesn't tell you anything once you've walked away — which is exactly when a power outage actually matters. This adds the missing piece: your phone buzzes the moment mains power drops, buzzes again when it's back, and warns you before the board shuts itself down — without changing how the UPS HAT itself is actually monitored or protected.

## Features

**Notifications** — fires on three events: mains power lost, mains power restored, battery critically low (shutdown imminent)
- Push notifications via [Alertzy](https://alertzy.app), hostname in the title so multi-board setups stay distinguishable
- Desktop popups too — `notify-send` for the headless monitor, native tray balloons for the GUI version
- Debounced against single noisy I2C reads (a few seconds of confirmation before trusting a state change), so momentary blips don't spam your phone

**Optional Pioneer600 OLED status screen** — hostname, battery %, `wlan0`/`eth0` IP (only shown if actually connected), free RAM, free disk. Auto-detects the HAT on the shared I2C bus and simply does nothing on boards that don't have one — same files deploy unchanged to every board.

**Actually reliable** — a systemd service (auto-installed by `main.sh`) so it survives reboots and desktop logins/logouts alike, non-blocking retry logic for hardware that isn't quite ready yet at boot, and a script that keeps running through transient I2C/network hiccups instead of taking the safety shutdown down with it.

## Hardware

| | Required | Notes |
|---|---|---|
| Raspberry Pi | Yes | Tested on Pi 5, Raspberry Pi OS Bookworm (Wayland/labwc) |
| Waveshare UPS HAT (E) | Yes | The whole reason this exists |
| Waveshare Pioneer600 | Optional | Only needed for the OLED status screen |

## What's in here

| File | Purpose |
|---|---|
| `ups.py` | Headless monitor — notifications, safety shutdown, OLED. Runs as a systemd service. |
| `batteryTray.py` | GUI system-tray version with the same notifications. Autostarts on desktop login. |
| `main.sh` | Run this once (and any time after pulling updates) — installs/updates both autostart mechanisms |
| `battery.sh`, `battery.desktop` | XDG autostart plumbing for `batteryTray.py` (Waveshare original, untouched) |
| `ups-monitor.service` | Reference systemd unit — `main.sh` generates its own with correct paths automatically, this is for manual install |
| `requirements.txt` | Python dependencies |
| `alertzy.key` | **Not committed** (gitignored) — your Alertzy account key goes here, see Configuration below |
| `NOTES.md` | The full technical writeup: every change, every bug found and fixed, troubleshooting |
| `images/` | Battery icons for the tray version (Waveshare original) |

## Quick start

```bash
git clone git@github.com:in-sympathy/ups_hat_e.git
cd ups_hat_e

sudo raspi-config
# Interface Options -> I2C -> Yes                 (always needed)
# Interface Options -> SPI -> Yes                 (only if you have a Pioneer600)

sudo apt-get install python3-smbus
pip install -r requirements.txt --break-system-packages
```

**Only if you have a Pioneer600** for the OLED display:
```bash
pip install spidev Pillow psutil --break-system-packages

# GPIO library depends on your Pi model - install ONE of these:
pip install rpi-lgpio --break-system-packages     # Raspberry Pi 5
pip install RPi.GPIO --break-system-packages      # Pi 4 or earlier
```

**Set your Alertzy key** (get one from the [Alertzy](https://alertzy.app) app, Account tab):
```bash
echo "your-alertzy-account-key" > alertzy.key
```

**Set it all running:**
```bash
chmod +x main.sh
./main.sh
```

That one command installs the desktop tray-icon autostart *and* the systemd service, and is safe to re-run any time — it only changes what's actually missing or out of date, so running it again on a board that's already set up correctly is a clean no-op.

**Verify:**
```bash
systemctl status ups-monitor
journalctl -u ups-monitor -f
```

## Configuration

Both `ups.py` and `batteryTray.py` read the same `alertzy.key` file, so there's exactly one place to update it. No quotes, trailing whitespace is fine. If you'd rather not use a separate file, you can paste the key directly into the `ALERTZY_ACCOUNT_KEY = _load_alertzy_key()` line in either script instead.

Alertzy notification fields, if you want to tune them (both scripts, same constants near the top):
| Field | Value |
|---|---|
| Title | hostname |
| Message | event description (e.g. `Mains power lost. Running on UPS battery (58%).`) |
| Priority | Normal |
| Group | `RaspberryPi` (shared across all boards — the title tells them apart) |

## A few notable things found along the way

- Waveshare's stock `ups.py` mislabels a status bit in its own print statements — `0x20` is commented as "Discharge state," but the official register manual defines it as **"VBUS is powered."** The opposite meaning. Mains-loss detection here is built on the documented meaning, not the stock comment.
- The Pioneer600's OLED is SPI, not I2C — but SPI has no bus-scan/ACK mechanism, so presence detection instead probes the board's onboard DS3231 RTC or PCF8574 GPIO expander (both I2C) as a proxy: if either answers, the whole board — OLED included — is physically there.
- Tray icons under Wayland (labwc, the Bookworm default) use a different protocol than the X11 systray this code was originally written against, and unlike X11, a registration attempt that doesn't land the first time never gets a second try by default — fixed by re-asserting visibility every refresh cycle instead of once at startup.

Full details, plus everything else that got fixed along the way, in [`NOTES.md`](NOTES.md).

## Credits

Built on top of [Waveshare's UPS HAT (E) wiki demo](<https://www.waveshare.com/wiki/UPS_HAT_(E)>) and [register manual](<https://www.waveshare.com/wiki/UPS_HAT_(E)_Register>). Alertzy notification pattern carried over from [Waveshare_UPS_Hat_B_Monitor](https://github.com/in-sympathy/Waveshare_UPS_Hat_B_Monitor).
