# Waveshare UPS HAT (E) Monitor

![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-c51a4a)
![Python](https://img.shields.io/badge/python-3-blue)
![Based on](https://img.shields.io/badge/based%20on-Waveshare%20demo-orange)

A customised fork of Waveshare's stock UPS HAT (E) demo scripts, extended with Alertzy push notifications, on-screen desktop alerts, and an optional live status display on a stacked Pioneer600 HAT's OLED screen — while leaving the original register handling, low-voltage safety shutdown, and tray icon completely untouched. Sibling project to [Waveshare_UPS_Hat_B_Monitor](https://github.com/in-sympathy/Waveshare_UPS_Hat_B_Monitor).

## Why this exists

Waveshare's stock demo tells you your battery percentage if you're sitting in front of the screen watching it. It doesn't tell you anything once you've walked away — which is exactly when a power outage actually matters. This adds the missing piece: your phone buzzes the moment mains power drops, buzzes again when it's back, and warns you before the board shuts itself down — without changing how the UPS HAT itself is actually monitored or protected.

## Contents

- [Features](#features)
- [Hardware](#hardware)
- [What's in this repo](#whats-in-this-repo)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Alertzy account key](#alertzy-account-key)
  - [Notification events](#notification-events)
  - [Desktop popups](#desktop-popups)
  - [Pioneer600 OLED display](#pioneer600-oled-display)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Technical notes](#technical-notes)
- [Credits](#credits)

## Features

**Notifications** — fires on four events: startup status, mains power lost, mains power restored, battery critically low (shutdown imminent)
- Push notifications via [Alertzy](https://alertzy.app), hostname in the title so multi-board setups stay distinguishable
- Desktop popups too, via `notify-send` — all from `ups.py`, the single source for every event (see [How it works](#how-it-works))
- Lost/restored are debounced against single noisy I2C reads (a few seconds of confirmation before trusting a state change), so momentary blips don't spam your phone

**Optional Pioneer600 OLED status screen** — battery %, `wlan0`/`eth0` IP, free RAM, CPU load + temperature, free disk. Auto-detects the HAT on the shared I2C bus and simply does nothing on boards that don't have one — same files deploy unchanged to every board. If both network interfaces are connected, the display cycles between their IPs instead of cramming both on screen at a font size too small to read.

**Actually reliable** — a systemd service (auto-installed by `main.sh`) so it survives reboots and desktop logins/logouts alike, non-blocking retry logic for hardware that isn't quite ready yet at boot, and a script that keeps running through transient I2C/network hiccups instead of taking the safety shutdown down with it.

## Hardware

| | Required | Notes |
|---|---|---|
| Raspberry Pi | Yes | Tested on Pi 5, Raspberry Pi OS Bookworm (Wayland/labwc) |
| Waveshare UPS HAT (E) | Yes | The whole reason this exists |
| Waveshare Pioneer600 | Optional | Only needed for the OLED status screen |

## What's in this repo

| File | Purpose |
|---|---|
| `ups.py` | Headless monitor — notifications, safety shutdown, OLED. Runs as a systemd service. |
| `batteryTray.py` | GUI system-tray battery readout. Autostarts on desktop login. |
| `main.sh` | Run this once (and any time after pulling updates) — installs/updates both autostart mechanisms |
| `battery.sh`, `battery.desktop` | XDG autostart plumbing for `batteryTray.py` (Waveshare original, untouched) |
| `ups-monitor.service` | Reference systemd unit — `main.sh` generates its own with correct paths automatically, this is for manual install |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Keeps `alertzy.key` (and Python cruft) out of version control |
| `alertzy.key` | **Not committed** — your Alertzy account key goes here, see [Configuration](#alertzy-account-key) |
| `images/` | Battery icons for the tray version (Waveshare original) |

## Installation

**1. Enable the interfaces you need:**
```bash
sudo raspi-config
# Interface Options -> I2C -> Yes                 (always needed)
# Interface Options -> SPI -> Yes                  (only if you have a Pioneer600)
```

**2. Install the base dependencies:**
```bash
sudo apt-get install python3-smbus
pip install -r requirements.txt --break-system-packages
```

**3. Only if you have a Pioneer600** for the OLED display:
```bash
pip install spidev Pillow psutil --break-system-packages

# GPIO library depends on your Pi model - install ONE of these:
pip install rpi-lgpio --break-system-packages     # Raspberry Pi 5
pip install RPi.GPIO --break-system-packages      # Pi 4 or earlier
```
Classic `RPi.GPIO` does not work on the Pi 5's GPIO chip at all — `rpi-lgpio` is a drop-in replacement that imports under the exact same name, so no code changes are needed either way.

**4. Set your Alertzy key** — see [Alertzy account key](#alertzy-account-key) below.

**5. Set it all running:**
```bash
chmod +x main.sh
./main.sh
```
That one command installs the desktop tray-icon autostart *and* the systemd service. It's safe to re-run any time (after pulling updates, after moving the folder, or just to double check) — it only changes what's actually missing or out of date, so running it again on a board that's already set up correctly is a clean no-op.

**6. Verify:**
```bash
systemctl status ups-monitor
systemctl is-enabled ups-monitor
journalctl -u ups-monitor -f
```
You should see a startup notification arrive on your phone within a few seconds, and `journalctl` should be printing a poll cycle's worth of I2C readings every 2 seconds.

## Configuration

### Alertzy account key

Get a key from the [Alertzy](https://alertzy.app) app (Account tab), then:
```bash
echo "your-alertzy-account-key" > alertzy.key
```
No quotes needed, trailing whitespace is fine — it gets stripped. `ups.py` reads this at startup; it's the only script that needs it, since it's the sole source of every notification now (see [How it works](#how-it-works)). `.gitignore` already excludes it, so it's safe to keep this repo in git without the real key ever getting committed.

If you'd rather not use a separate file, paste the key directly into the `ALERTZY_ACCOUNT_KEY = _load_alertzy_key()` line in `ups.py` instead — whatever's there takes priority over the file not existing.

### Notification events

| Event | Fires when | Debounced? |
|---|---|---|
| Startup status | Every time `ups.py` starts (e.g. after a reboot) — reports current state, not a transition | No — reports immediately |
| Mains lost | Transition to running on UPS battery | Yes — ~3 confirmed reads (~6s) |
| Mains restored | Transition to running on mains power | Yes — ~3 confirmed reads (~6s) |
| Battery critical | Low-voltage safety shutdown countdown begins | No — fires on the first low reading |

All four are sent by `ups.py` only - see [How it works](#how-it-works) for why.

Alertzy fields for all four events (tune via the constants near the top of `ups.py`):

| Field | Value |
|---|---|
| Title | hostname |
| Message | event description (e.g. `Mains power lost. Running on UPS battery (58%).`) |
| Priority | Normal |
| Group | `RaspberryPi` (shared across all boards — the title tells them apart) |

Desktop popup urgency/icon - all via `ups.py`'s `notify-send` (independent of the Alertzy fields above, since popups aren't limited the same way):

| Event | Urgency | Icon |
|---|---|---|
| Startup, on mains | Normal | battery-good-charging |
| Startup, on battery | Critical | dialog-warning |
| Mains lost | Critical | dialog-warning |
| Mains restored | Normal | battery-good-charging |
| Battery critical | Critical | battery-caution |

### Desktop popups

All from `ups.py`, via `notify-send` (needs `libnotify-bin`) plus a `loginctl` session lookup — since it runs continuously in the background, detached from any desktop login, it only pops something up if a desktop session happens to be active on the board *at that moment*, otherwise it just skips silently, no error.

`batteryTray.py` doesn't send any event-driven notifications of its own (Alertzy or popup) - it used to, but with desktop auto-login both scripts end up running continuously side by side, and each independently detecting and notifying on the same hardware transition meant every single event landed twice: once from each process. Rather than deduplicate two overlapping notification systems, `ups.py` (systemd, always running regardless of login state) is simply the one source of truth for all of it now. `batteryTray.py`'s own contribution is its continuously-updating tray icon and tooltip (always-on, not tied to any specific event) and the stock low-battery warning dialog (a modal `QMessageBox`, untouched from Waveshare's original) - both are local, passive/blocking UI rather than a notification channel, so they were never part of the duplication in the first place.

### Pioneer600 OLED display

If a Pioneer600 HAT is also stacked on a board, `ups.py` automatically detects it and shows live status on its SSD1306 OLED: battery % + charging/discharging, a `wlan0`/`eth0` IP address, available RAM, CPU load and temperature, available disk space. On a board without one, this is a complete no-op — nothing to configure, nothing to disable.

**Layout:** line 1 is `Charging: <pct>%` when mains power is present, `Battery: <pct>%` when running off the UPS - then whichever of the network/RAM/CPU/disk lines are available. CPU line reads `CPU: <load>% | <temp> °C`, e.g. `CPU: 35% | 46 °C` — temperature is read directly from `/sys/class/thermal/thermal_zone0/temp` (falling back to `psutil`'s sensor API if that path isn't present), and the whole line is skipped if temperature can't be read rather than showing a partial line. Font size adapts to how many lines are active (4 lines with no network connected, up to 5 with one interface up), so text isn't cramped when there's less to show. Any line too wide for the display (e.g. an unusually long IP address) is truncated with `..` rather than overflowing.

**Both interfaces connected:** rather than showing both `wlan0` and `eth0` permanently (which would force the font down to a size too small to read on the physical 128x64 screen), the display alternates between them every render cycle (~2s), with a trailing `>>>` hint on whichever one's currently shown to indicate another is waiting — shown only when it actually fits; if a longer IP address would overflow the display with the hint added, the hint is dropped rather than truncated, so the IP itself is always shown complete and correct. Tunable via `OLED_NETWORK_CYCLE_INTERVAL` near the other OLED constants in `ups.py` — set it higher (e.g. `3`) for a slower cycle if 2 seconds per address feels too fast to read.

**How detection works:** the OLED itself is SPI, not I2C, and SPI has no bus-scan/ACK mechanism the way I2C does — there's no way to directly "ask" if something is connected the way `ups.py` already does for the UPS HAT itself. Instead, it probes the *same* I2C bus for two other chips that are only present on a Pioneer600: the DS3231 RTC (fixed address `0x68`) or the PCF8574 GPIO expander (`0x20`). If either answers, the whole board — OLED included — is physically stacked there. This probe runs at startup and then again once per poll cycle (non-blocking) for up to ~60 seconds if nothing's found yet, covering boot-time races where the bus isn't fully ready the instant the service starts; once it succeeds, it stops trying.

`spidev`/GPIO/Pillow and `psutil` are independent, separately-optional imports — a board with only one set installed still gets a partial display (the battery line shows even without `psutil`; network/RAM/CPU/disk lines just won't) rather than the whole feature disabling itself.

## How it works

Two entry points, for two different situations:

- **`ups.py`** is the one that matters most — headless, runs as a systemd service, alive 24/7 regardless of whether anyone's logged into a desktop. This is where the safety-critical low-voltage shutdown lives, and where all the notification logic actually runs from.
- **`batteryTray.py`** is a GUI companion — a system-tray icon for when you're at a desktop session and want an at-a-glance battery readout without opening a terminal.

`ups.py` reads `alertzy.key` and is the sole source of all four notification events (see [Desktop popups](#desktop-popups) above for why). `batteryTray.py` doesn't send notifications of its own at all - its job is purely the live tray icon/tooltip and the stock low-battery dialog.

Two deployment mechanisms, matched to how each script needs to run:

- **systemd** (`ups-monitor.service`, auto-installed by `main.sh`) starts unconditionally as soon as Linux boots. This is why `ups.py` uses it — it needs to run before/without any login, the same way the safety shutdown does.
- **XDG autostart** (`battery.desktop`, also set up by `main.sh`) only starts `batteryTray.py` when a desktop *session* begins — after a login, whether manual or automatic. It does not run "at boot" in the sense of running before any login exists.

`main.sh` sets up both in one run, and is safe to re-run any time — it checks current state (does the systemd unit exist and match the current path? is it enabled? is it running?) before touching anything, so re-running it on an already-correctly-configured board is a clean no-op. If the project folder ever moves, running it again detects the path changed and updates/restarts the service to match.

## Troubleshooting

**Alertzy notifications aren't arriving.** Check `alertzy.key` exists in this folder and actually contains your key (not the placeholder) — `cat alertzy.key`. Check the console/journal output for a line starting with `[Alertzy skipped - ...]`, which tells you exactly why (no key set, `requests` not installed, or a network error with the actual exception).

**OLED shows nothing, but `journalctl` shows no errors either.** Check whether it's even being detected: look for `Pioneer600 presence probe: ... acked at 0x68` (or `0x20`) in the output. If you see `[OLED skipped - Pioneer600 not detected on the I2C bus]` instead, the board genuinely isn't finding the HAT on I2C — check wiring/seating. If you see `[OLED skipped - spidev/RPi.GPIO/Pillow not installed]`, revisit step 3 of Installation.

**OLED works when you run `ups.py` manually, but not after a reboot.** This means the screen, wiring, I2C detection, and SPI driver are all correct — the problem is specifically that `ups.py` isn't actually running at boot, which almost always means the systemd service was never installed. Run `./main.sh`, then check:
```bash
systemctl status ups-monitor
journalctl -u ups-monitor -b
```
If status says `could not be found`, `main.sh` hasn't been run yet (or failed partway). If it's enabled and running but the OLED still isn't showing, `journalctl -u ups-monitor -b` will show exactly what happened on the most recent boot, including the specific exception if SPI setup is failing. Worth also confirming the service is running the *current* file, not a stale copy at a different path than the one you've been testing manually:
```bash
cat /proc/$(systemctl show -p MainPID --value ups-monitor)/cmdline
```

**Tray icon (`batteryTray.py`) never appears, even though the process is running.** Check your session type: `echo $XDG_SESSION_TYPE`. If it says `wayland` (the Raspberry Pi OS Bookworm default, via labwc), this is a known category of issue — Wayland compositors use a different tray protocol (StatusNotifierItem over D-Bus) than the older X11 systray mechanism most Qt tray apps, including this one, were originally written against. Unlike X11, a registration attempt that doesn't land the first time isn't automatically retried by default. This is already worked around here — `batteryTray.py` re-asserts tray visibility every refresh cycle instead of once at startup — but if it's still not appearing, check `journalctl --user -f` while the app starts, for anything mentioning D-Bus or StatusNotifierItem.

## Technical notes

A few things found and fixed along the way, for anyone comparing this against the stock demo or Waveshare's other UPS HAT scripts:

- **A mislabeled bit in Waveshare's own demo.** Stock `ups.py` labels register `0x02` bit `0x20` as "Discharge state" in its print statements. The separate [Register Manual](<https://www.waveshare.com/wiki/UPS_HAT_(E)_Register>) defines that same bit as **"VBUS is powered"** — the opposite meaning. The manual is the bit-exact authoritative source, so mains-lost/restored detection here is built on the documented meaning, not the stock comment (see the note above `BIT_VBUS_POWERED` in `ups.py`).

- **The SSD1306 driver structurally mirrors a confirmed-working reference**, not just its init sequence — matching Waveshare's own proven pattern in a few specific ways: `spidev.SpiDev(bus, device)` as a single constructor call rather than the more commonly-documented `SpiDev()` + `.open()` two-step (falls back to the two-step form automatically if a given `spidev` build doesn't support the single-call form); no explicit SPI clock speed override, relying on `spidev`'s own default; commands sent one byte per `writebytes()` call rather than batched into one transaction; and `clear()` + `display()` called once right after `begin()`, matching the proven startup sequence.

## Credits

Built on top of [Waveshare's UPS HAT (E) wiki demo](<https://www.waveshare.com/wiki/UPS_HAT_(E)>) and [register manual](<https://www.waveshare.com/wiki/UPS_HAT_(E)_Register>). Alertzy notification pattern carried over from [Waveshare_UPS_Hat_B_Monitor](https://github.com/in-sympathy/Waveshare_UPS_Hat_B_Monitor).
