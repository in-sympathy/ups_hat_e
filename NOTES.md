# What this bundle is

A drop-in replacement for Waveshare's stock `UPS_HAT_E.zip`
(https://files.waveshare.com/wiki/UPS-HAT-E/UPS_HAT_E.zip). Every install and
run command on their wiki (https://www.waveshare.com/wiki/UPS_HAT_(E)) works
exactly as documented, unchanged:

```
sudo apt-get install python3-smbus
wget .../UPS_HAT_E.zip      # (or just use this zip instead)
unzip UPS_HAT_E.zip
cd UPS_HAT_E
python3 ups.py
```

```
cd ~/UPS_HAT_E
DISPLAY=':0.0' python3 batteryTray.py
```

```
cd ~/UPS_HAT_E
sudo chmod +x main.sh
./main.sh
sudo reboot
```

`battery.sh`, `battery.desktop`, `README.rst`, and everything under
`images/` are byte-for-byte identical to Waveshare's originals. `main.sh`
has one addition beyond stock - see "Getting ups-monitor.service running at
boot" below - everything it originally did is untouched, just appended to.
`ups.py` and `batteryTray.py` changed.

## What's new in ups.py and batteryTray.py

Both now push a notification on three events: mains power lost, mains power
restored, and battery critically low (safety shutdown countdown starting).

**Alertzy fields** (mapped to match https://alertzy.app/#integration exactly):
- Title: just the hostname (`socket.gethostname()`) - same board sends every
  alert under one consistent title
- Message: the event description, e.g. `"Mains power lost. Running on UPS
  battery (58%)."`
- Priority: Normal (0) for all three events
- Group: `"RaspberryPi"` - fixed, shared across every board, so all your
  Pi's alerts land in one place in the app; the title is what tells you
  which board it was

**Desktop popup** (notify-send / showMessage - a separate channel, not an
Alertzy field, so it keeps richer context since there's no field limit to
work around): title is `"<hostname> runs on UPS"` / `"<hostname> runs on
MAINS"` / `"<hostname> - LOW BATTERY"`, message is the same event
description as above.

Everything else - register addresses, the low-voltage auto-shutdown
sequence, the tray icon/tooltip, the low-battery dialog - is untouched from
stock.

## One thing to know: a mislabeled bit in Waveshare's own demo

Waveshare's stock `ups.py` labels register `0x02` bit `0x20` as "Discharge
state" in its print statements. Their separate Register Manual
(https://www.waveshare.com/wiki/UPS_HAT_(E)_Register) defines that same bit
as **"VBUS is powered"** - the opposite meaning. The manual is the bit-exact
authoritative source, so that's what the mains-lost/restored detection here
is built on (see the comment above `BIT_VBUS_POWERED` in `ups.py`).

## Getting Alertzy actually sending pushes

Both files run exactly as-is even with nothing else installed - the
`requests` import is optional, and Alertzy notifications are silently
skipped (with a one-line console note) until you do two things:

1. `pip install -r requirements.txt --break-system-packages` (or
   `sudo apt install python3-requests`)
2. Create a file named `alertzy.key` in this same folder, containing
   nothing but your key from https://alertzy.app (no quotes, trailing
   whitespace is fine, it gets stripped). Both `ups.py` and `batteryTray.py`
   read the *same* `alertzy.key` at startup - one file to update, not two.
   `.gitignore` already excludes it, so it's safe to keep this folder in
   git without your real key ever getting committed.

If you'd rather not use a separate file, paste the key directly into the
`ALERTZY_ACCOUNT_KEY = _load_alertzy_key()` line in either file instead -
whatever you put there takes priority over `alertzy.key` not existing.

## Desktop popups

- `ups.py` uses `notify-send` (needs `libnotify-bin`) plus a `loginctl`
  session lookup, since it's meant to run continuously in the background,
  detached from any desktop login - it only pops something up if a desktop
  session happens to be active on the board *at that moment*.
- `batteryTray.py` uses Qt's own `QSystemTrayIcon.showMessage()` instead,
  since that script only ever runs while already inside a live desktop
  session (that's how `battery.sh` launches it) - no session discovery
  needed there.

If you run both at once (`ups.py` as a background service *and*
`batteryTray.py` via the desktop autostart), you'll get the same event on
both channels while a desktop session happens to be active. Harmless, just
not deduplicated.

## Optional: Pioneer600 OLED status display

If a Waveshare Pioneer600 HAT is also stacked on a board (in addition to
UPS HAT (E)), `ups.py` will automatically detect it and show live status on
its SSD1306 OLED: hostname, battery % + charging/discharging, `wlan0`/`eth0`
IP addresses (only shown if that interface is actually connected), available
RAM, and available disk space. On the board without a Pioneer600, this is a
complete no-op - nothing to configure, nothing to disable.

**How detection works:** the OLED itself is SPI, not I2C, and SPI has no
bus-scan/ACK mechanism the way I2C does - there's no way to directly "ask"
if something is connected the way `ups.py` already does for UPS HAT (E)
itself. Instead, `ups.py` probes the *same* I2C bus for two other chips that
are only present on a Pioneer600: the DS3231 RTC (fixed address `0x68`) or
the PCF8574 GPIO expander (`0x20`). If either answers, the whole board - the
OLED included - is physically stacked there. This probe runs once at
startup, not every poll cycle.

**Dependencies** (only needed on the board that actually has a Pioneer600 -
everywhere else, `ups.py` runs exactly as before, no new install needed):

```
pip install spidev Pillow psutil --break-system-packages
sudo raspi-config   # Interfacing Options -> SPI -> Yes, then reboot
```

Plus a GPIO library - which one depends on the Pi model:
- **Raspberry Pi 5**: `pip install rpi-lgpio --break-system-packages`
  (classic `RPi.GPIO` does not work on Pi 5's GPIO chip at all - `rpi-lgpio`
  is a drop-in replacement that imports under the exact same name)
- **Pi 4 or earlier**: `pip install RPi.GPIO --break-system-packages`, or
  it's often preinstalled already

`spidev`/GPIO/Pillow and `psutil` are independent, separately-optional
imports - a board with only one of the two installed still gets a partial
display (e.g. hostname/battery show even without `psutil`, network/RAM/disk
lines just won't) rather than the whole feature disabling itself.

**Hardware reference used:** Waveshare's own Pioneer600 pin assignments
(RST=BCM GPIO19, DC=BCM GPIO16, hardware SPI0 CE0). After an initial version
of this driver produced a blank screen, it was rewritten to structurally
match a confirmed-working copy of Waveshare's real `SSD1306.py` as closely
as possible, not just its init sequence:
- `spidev.SpiDev(bus, device)` as a single constructor call (their proven
  pattern) rather than `SpiDev()` + `.open()` - falls back to the two-step
  form automatically if a given spidev build only supports that
- No explicit clock speed override - relies on spidev's own default, same
  as their code, rather than a more aggressive explicit speed
- Commands sent one byte per `writebytes()` call (CS toggles between every
  byte) rather than batched into one transaction per logical command
- Separate `clear()` / `image()` / `display()` methods, with `clear()` +
  `display()` called once right after `begin()` - matching their exact
  proven startup sequence instead of only pushing real content later

**Layout note:** font size adapts to how many lines are active (4 lines
when no network is connected, up to 6 when both wlan0 and eth0 are up), so
text doesn't get cramped when there's less to show. Long hostnames are
truncated with ".." rather than overflowing the display. Format: `Host:
<hostname>` on line 1, `Batt: <pct>% - CHG` or `- DIS` on line 2.

## Getting ups-monitor.service running at boot

`./main.sh` now sets this up automatically, in addition to what it already
did for `batteryTray.py`'s autostart - re-run it any time (safe to run
repeatedly; it checks current state before changing anything):

```
cd ~/UPS_HAT_E
./main.sh
```

It generates `ups-monitor.service` with `WorkingDirectory`/`ExecStart`
pointing at wherever this folder actually is (not a fixed assumed path),
then checks: does the unit file exist and match? Is it enabled? Is it
currently running? - and only touches whatever's actually missing or stale,
so running it again on a board where everything's already correct doesn't
restart a perfectly good running service. If the folder ever gets moved,
running `main.sh` again will detect the path changed and restart the
service pointing at the new location.

Verify with the same three commands as before:

```
systemctl status ups-monitor
systemctl is-enabled ups-monitor
journalctl -u ups-monitor -b
```

## If the OLED works when you run `ups.py` manually but not at boot

This means the screen, wiring, I2C detection, and SPI driver are all
correct - the problem is specifically that `ups.py` isn't actually running
at boot. Two mechanisms exist in this bundle, and they are **not**
interchangeable:

- `ups-monitor.service` (systemd) - starts unconditionally as soon as Linux
  boots, regardless of whether anyone ever logs into a desktop. This is
  the correct place for the OLED code: it needs to run 24/7 for the same
  reason the low-voltage safety shutdown does.
- `battery.desktop`/`main.sh` (XDG autostart) - only starts `batteryTray.py`
  when a desktop *session* begins, i.e. only after someone (or auto-login)
  logs into the graphical desktop. It does not run "at boot" in the sense
  of running before/without a login - on a board without graphical
  auto-login enabled, this might not run for hours or days after a reboot,
  which is a weaker guarantee than what a systemd service gives you, not a
  stronger one.

Moving the OLED code into `batteryTray.py` would only trade one
autostart problem for a subtler version of the same problem, and would add
a second process polling the same I2C registers `ups.py` already polls
every 2 seconds - exactly the kind of bus contention risk flagged in your
own past hardware work. Kept it in `ups.py`; run `./main.sh` (see above) to
get the service installed/enabled/running, then check:

```
systemctl status ups-monitor
journalctl -u ups-monitor -b
```

If it's enabled and running, `journalctl -u ups-monitor -b` will show
exactly what happened with the OLED on the most recent boot - including
which of these three lines came up: the presence-probe hit, a successful
init, or `[OLED skipped - init failed: ...]` with the real exception.
That exception is the next thing to chase if it's still not lighting up.

`init_oled()` retries automatically now - not by blocking at startup, but
by trying again once per poll cycle (every 2s) for up to ~60 seconds, only
while nothing has succeeded yet. This closes two gaps from the first
version of this retry logic: it now also retries the I2C presence check
itself (previously only the SPI/GPIO setup after presence was retried - if
that one presence read landed during a boot-time hiccup, it never got a
second chance), and it no longer blocks the safety-critical monitoring loop
with `time.sleep()` while retrying - the main loop keeps running at its
normal cadence throughout, with OLED init just being one more thing it
tries on the side each cycle until it either succeeds or the budget runs
out. Once it succeeds, it stops trying; if it never does, it prints one
message on the first attempt and one on the last, not once per cycle.

If it's still not showing up after a real reboot with this in place, the
next things worth checking: confirm the running service is actually using
the *current* `ups.py` (not a stale copy at a different path than the one
you've been testing manually - `cat /proc/$(systemctl show -p MainPID
--value ups-monitor)/cmdline` shows the exact file path systemd is running),
and `journalctl -u ups-monitor -b` will show every attempt's outcome across
that boot, including the specific exception if SPI setup itself is failing
rather than presence detection.

## Tray icon not appearing under Wayland (labwc/wf-panel-pi)

`batteryTray.py`'s process runs fine, but the icon itself may never appear
in the panel - Wayland compositors (labwc, the Raspberry Pi OS Bookworm
default) use a different tray protocol (StatusNotifierItem over D-Bus) than
the older X11 systray mechanism this app was originally written against,
and unlike X11, a registration attempt that doesn't land isn't
automatically retried. This is a documented, known category of issue for
Qt tray apps under labwc, not specific to this code.

The stock demo calls `tray_icon.show()` exactly once, in `__init__`; if the
Wayland panel's tray watcher isn't ready in that exact moment (`battery.sh`
only waits a fixed 5 seconds before launching), nothing ever asks again -
meanwhile `setIcon()` was already being called every second regardless
(updating the battery-percentage icon), so the icon quietly kept updating
into a tray entry that was never actually created.

Fix: `refresh()` now also calls `tray_icon.show()` every cycle (once a
second, alongside the existing icon update), not just once at startup.
Calling `show()` on an already-visible icon is a harmless no-op, so this
costs nothing once the icon is up, but means a slow-to-initialize panel
gets asked again every second instead of getting exactly one shot.

I can't fully verify this against a real labwc/wf-panel-pi session from
here - confirmed the code now calls `show()` on every refresh cycle rather
than once, but whether that alone is sufficient depends on specifics of the
Wayland tray watcher's behavior that only show up on real hardware. If the
icon still doesn't appear after this, worth checking `journalctl --user -f`
while `batteryTray.py` starts, for any D-Bus/StatusNotifierItem errors.

## Optional: ups-monitor.service (manual reference)

`./main.sh` (see above) sets this up automatically now - this section is
for reference, or for installing by hand if you'd rather not run it.

Not part of Waveshare's original bundle - a systemd unit for running
`ups.py` as a proper background service (survives reboots, restarts itself
if it crashes, runs whether or not anyone's logged into the desktop).
`battery.desktop` alone (without the `main.sh` addition above) isn't a fit
for this: that mechanism only runs `batteryTray.py` while someone's logged
in, and the low-voltage safety shutdown needs to stay alive regardless.

The static `ups-monitor.service` file included in this bundle has an
example path (`/home/pi/UPS_HAT_E`) - edit `WorkingDirectory`/`ExecStart` to
match wherever you actually put this folder before using it manually; the
version `main.sh` generates does this substitution for you automatically:

```
sudo cp ups.py /home/pi/UPS_HAT_E/ups.py   # adjust path to match the file below
sudo cp ups-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ups-monitor.service
journalctl -u ups-monitor -f      # tail it live
```

It runs as `root` on purpose - sidesteps needing `i2c` group membership and
sudoers grants for both `poweroff` and the `sudo -u <desktop-user>` trick
`ups.py`'s desktop-popup code uses. Swap `User=root` for your own username
in the unit file if you'd rather keep it unprivileged, but then confirm that
user has passwordless sudo for both of those.
